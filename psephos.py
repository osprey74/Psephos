# psephos.py -- プログラマブル関数電卓 (PicoCalc / Raspberry Pi Pico 2W / MicroPython)
#
# 命名: Psephos (ψῆφος = 計算に用いた小石) -- 仮称。一括置換で変更可。
# 依存: LofiFren/zenodante 系 PicoCalc MicroPython ファームウェア
#   - picocalc.display : framebuf サブクラス (標準 framebuf メソッド利用可)
#   - picocalc.keyboard 相当のキー入力 (機種により API 名が異なるため抽象化)
#
# 設計方針 (DESIGN.md 参照):
#   - 安全な eval (組み込み無効化 + 許可関数のみ) で式評価
#   - 計算履歴をリスト保持 + SD カードに追記永続化 (/sd/psephos_history.txt)
#   - 320x320 / 6x8 フォント前提で画面を「履歴域」と「入力行」に分割
#
# 注意: キーボード取得 API は環境差があるため _read_key() に集約。
#       実機で動かない場合はここだけ調整すれば全体が動く設計。

import math

# ---- ハードウェア抽象化 --------------------------------------------------

try:
    import picocalc
    _display = picocalc.display
    _HW = True
except Exception:
    # PC 上のフォールバック (端末で擬似動作させ、ロジックを検証するため)
    _display = None
    _HW = False

# 画面・フォント定数 (zenodante ドライバの 6x8 フォント前提)
SCREEN_W = 320
SCREEN_H = 320
CHAR_W = 6
CHAR_H = 8
COLS = SCREEN_W // CHAR_W      # 53
ROWS = SCREEN_H // CHAR_H      # 40

# レイアウト (chrome 画像非装着時の既定値 = 全画面利用)
# chrome.bin がある場合は _maybe_load_chrome() がこれらを書き換える。
HISTORY_LEFT_PX = 7                          # 履歴の左余白 (px)
HISTORY_RIGHT_PX = 7                         # 履歴の右余白 (px、左と対称)
HISTORY_COLS = (SCREEN_W - HISTORY_LEFT_PX - HISTORY_RIGHT_PX) // CHAR_W   # = 51
# 入力行は `_draw_text_2x()` で 2 倍描画する。framebuf.text の組み込みフォントは 8x8
# (PicoCalc の drawTxt6x8 とは別物) なので、ベースを 8x8 として計算する。
INPUT_SCALE = 2                              # 入力行の拡大率
INPUT_BASE_W = 8                             # framebuf.text の組み込みフォント幅
INPUT_BASE_H = 8                             # framebuf.text の組み込みフォント高
INPUT_CHAR_W = INPUT_BASE_W * INPUT_SCALE    # 16 px / char
INPUT_CHAR_H = INPUT_BASE_H * INPUT_SCALE    # 16 px / char
INPUT_COLS = SCREEN_W // INPUT_CHAR_W        # 320 / 16 = 20 cols

INPUT_TOP_PAD = 5                            # 入力行とメッセージ行の間の余白 (px, 区切り線 + 上下 2px ずつ)
INPUT_BOTTOM_PAD = 2                         # 入力行と chrome 下端領域 (4px) の間の余白 (px)

_ACTIVE_TOP = 0                              # 動的領域開始 y (px)
_ACTIVE_BOTTOM = SCREEN_H                    # 動的領域終了 y (px, exclusive)
_HISTORY_Y0 = 0                              # 履歴域開始 y (px)
_INPUT_Y = SCREEN_H - INPUT_CHAR_H - INPUT_BOTTOM_PAD    # 入力行 y (下余白を考慮)
_MESSAGE_Y = _INPUT_Y - INPUT_TOP_PAD - CHAR_H           # メッセージ行 y (px)
_HISTORY_ROWS = (_MESSAGE_Y - _HISTORY_Y0) // CHAR_H     # 履歴行数 (動的に算出)

# 4bit LUT 上の論理色 (VT100 LUT 既定: 0=黒, 1=赤, 2=緑, 3=黄, 4=青,
# 5=マゼンタ, 6=シアン, 7=明灰, 8=暗灰, 9〜15=各色の明るい版・白)
# テーマ切替で書き換えるため module global で保持。
COL_BG = 0
COL_FG = 15
COL_DIM = 8
COL_ACC = 11   # アクセント (結果表示)

# テーマ: (FG, BG, DIM, ACC) のタプル
_THEMES = {
    "default": (15, 0, 8, 11),    # 白文字 / 黒地 / 暗灰 / 黄アクセント
    "amber":   (11, 0, 3, 15),    # 黄文字 / 黒地 / 暗黄 / 白アクセント
    "green":   (10, 0, 2, 15),    # 緑文字 / 黒地 / 暗緑 / 白アクセント
    "cyan":    (14, 0, 6, 15),    # シアン文字 / 黒地 / 暗シアン / 白アクセント
    "invert":  (0, 15, 8, 1),     # 黒文字 / 白地 / 暗灰 / 暗赤
}


def _apply_theme(name):
    """テーマ名から論理色を更新。未知の名前は False を返す。"""
    global COL_FG, COL_BG, COL_DIM, COL_ACC
    if name in _THEMES:
        COL_FG, COL_BG, COL_DIM, COL_ACC = _THEMES[name]
        return True
    return False


HISTORY_PATH = "/sd/psephos_history.txt"
HISTORY_MAX = 200              # メモリ保持上限 (PSRAM 余裕あるが安全側)
CONFIG_PATH = "/sd/psephos_config.txt"

# Chrome レイヤ (アプリ枠) 関連
CHROME_IMG_PATH = "/sd/psephos_chrome.bin"
CHROME_BYTES = (SCREEN_W * SCREEN_H) // 2    # GS4_HMSB: 4bpp = 51,200 byte
CHROME_TOP_DEFAULT_H = 17                    # chrome 上部の高さ (px) — 履歴は y=17 から
CHROME_BOTTOM_DEFAULT_H = 4                  # chrome 下部の高さ (px) — 画面下から 4px 確保 (うち下 2px が水平線)
_chrome_buf = None                           # (bytearray, FrameBuffer) or None

# 設定 (起動時に CONFIG_PATH からロード、`theme` 適用時に保存)
_config = {
    "theme": "default",
    "precision": 10,
    "history_max": HISTORY_MAX,
}


def _load_config():
    """設定ファイルを読み込み _config に反映。`key = value` 1 行形式。"""
    try:
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k not in _config:
                    continue
                # int 変換できれば数値、ダメなら文字列のまま
                try:
                    _config[k] = int(v)
                except ValueError:
                    _config[k] = v
    except OSError:
        pass


def _save_config():
    """_config を CONFIG_PATH に書き出す。失敗は黙って無視 (SD 無し等)。"""
    try:
        with open(CONFIG_PATH, "w") as f:
            for k, v in _config.items():
                f.write("{}={}\n".format(k, v))
    except OSError:
        pass


# ---- 安全な式評価 --------------------------------------------------------

# 許可する関数・定数のみを名前空間に渡す。eval の組み込みは無効化する。
def _build_namespace():
    ns = {
        # 三角
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "atan2": math.atan2,
        # 双曲線 (MicroPython math にあれば)
        # 指数・対数
        "exp": math.exp, "log": math.log, "log10": math.log10,
        "sqrt": math.sqrt, "pow": math.pow,
        # 端数・絶対値
        "floor": math.floor, "ceil": math.ceil, "fabs": math.fabs,
        "abs": abs, "round": round,
        # 角度変換
        "radians": math.radians, "degrees": math.degrees,
        # 進数変換 (Phase 3)
        "hex": hex, "bin": bin, "oct": oct, "int": int, "float": float,
        # 定数
        "pi": math.pi, "e": math.e, "tau": getattr(math, "tau", 2 * math.pi),
        # ユーティリティ
        "min": min, "max": max,
    }
    return ns


_NAMESPACE = _build_namespace()
_ANS = 0.0           # 直前の計算結果 (ans で参照可能)
_ANS_HISTORY = []    # 結果スタック (最新が index 0)。ans2..ans10 で参照可能
_ANS_DEPTH = 10
_USER_VARS = {}      # ユーザ定義変数 (セッション中のみ保持)


def _extract_names(expr):
    """式中の識別子を抽出。数値リテラル (10進/16進/2進/8進/指数表記) は除外する。"""
    names = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isdigit() or (c == "." and i + 1 < n and expr[i + 1].isdigit()):
            # 数値リテラル: 0x.. / 0b.. / 0o.. / 10進 / 1.5e-10 等を丸ごと消費
            if c == "0" and i + 1 < n and expr[i + 1] in "xXbBoO":
                prefix = expr[i + 1]
                if prefix in "xX":
                    valid = "0123456789abcdefABCDEF_"
                elif prefix in "bB":
                    valid = "01_"
                else:  # oO
                    valid = "01234567_"
                i += 2
                while i < n and expr[i] in valid:
                    i += 1
            else:
                i += 1
                while i < n:
                    ch = expr[i]
                    if ch.isdigit() or ch == ".":
                        i += 1
                    elif ch in "eE":
                        i += 1
                        if i < n and expr[i] in "+-":
                            i += 1
                    else:
                        break
        elif c.isalpha() or c == "_":
            j = i
            # MicroPython の一部ビルドに str.isalnum() が無いため isalpha/isdigit で代替
            while i < n and (expr[i].isalpha() or expr[i].isdigit() or expr[i] == "_"):
                i += 1
            names.append(expr[j:i])
        else:
            i += 1
    return names


def _is_identifier(s):
    """有効な Python 識別子か判定。"""
    if not s:
        return False
    if not (s[0].isalpha() or s[0] == "_"):
        return False
    for c in s[1:]:
        if not (c.isalpha() or c.isdigit() or c == "_"):
            return False
    return True


_COMMANDS = ("help", "theme", "clear", "cas")  # main() で特殊コマンドとして処理する名前


def _is_reserved_target(name):
    """代入の左辺として使用禁止の予約名か判定。"""
    if name in _NAMESPACE:
        return True
    if name == "ans":
        return True
    if name.startswith("ans") and name[3:] and name[3:].isdigit():
        return True
    if name in _COMMANDS:
        return True
    return False


def _is_allowed_name(name):
    """式中の識別子として許可されているか判定。"""
    if name in _NAMESPACE:
        return True
    if name == "ans":
        return True
    if name.startswith("ans") and name[3:] and name[3:].isdigit():
        return True
    if name in _USER_VARS:
        return True
    return False


def _check_safe(expr):
    """安全でない式を ValueError で弾く (AST ベース)。

    MicroPython の eval は {"__builtins__": {}} を渡しても組み込み関数を
    遮断しない (CPython と挙動が異なり、実機で確認済 2026-06-18)。
    そこで内製 CAS パーサで AST を構築し、ノード単位で検査する:
      1. "__" を含む式は拒否 (dunder 経由のリフレクション攻撃を遮断)
      2. パース不能な式 (文字列リテラル・属性アクセス・lambda 等を含む) は拒否
      3. AST 上の **関数呼び出し** (`_CasCall`) は _NAMESPACE の関数に限定
      4. 変数参照は自由 (eval が NameError なら呼び出し側で symbolic mode に落とす)

    これにより `x + x` のような未定義変数を含む式は通り、`open(...)` や
    `(open)(0)` のような呼び出しは AST 構造から確実にブロックされる。
    """
    if "__" in expr:
        raise ValueError("disallowed: __ in expr")
    try:
        node = _cas_parse(expr)
    except Exception:
        raise ValueError("invalid syntax")
    _check_safe_node(node)


def _check_safe_node(node):
    """AST を再帰的に検査。未許可の関数呼び出しを発見したら ValueError。"""
    if isinstance(node, _CasCall):
        if node.name not in _NAMESPACE:
            raise ValueError("disallowed call: " + node.name)
        for a in node.args:
            _check_safe_node(a)
    elif isinstance(node, _CasBinOp):
        _check_safe_node(node.l)
        _check_safe_node(node.r)
    elif isinstance(node, _CasUnaryOp):
        _check_safe_node(node.x)
    # _CasNum, _CasVar はそれ自体安全


def _split_assignment(expr):
    """代入式なら (lhs, rhs) を返す。そうでなければ None。

    `==` `<=` `>=` `!=` の `=` は代入と誤判定しない。複合代入 (`+=` 等) は非対応。
    """
    n = len(expr)
    i = 0
    while i < n:
        if expr[i] == "=":
            prev_c = expr[i - 1] if i > 0 else ""
            next_c = expr[i + 1] if i + 1 < n else ""
            if next_c == "=":
                i += 2
                continue
            if prev_c in "<>!=+-*/%":
                i += 1
                continue
            return expr[:i].strip(), expr[i + 1:].strip()
        i += 1
    return None


def _build_locals():
    """eval 用の locals 辞書を構築 (NAMESPACE + ans + ans2..ansN + ユーザ変数)。"""
    local = dict(_NAMESPACE)
    local["ans"] = _ANS
    for k in range(2, _ANS_DEPTH + 1):
        if k - 1 < len(_ANS_HISTORY):
            local["ans" + str(k)] = _ANS_HISTORY[k - 1]
    local.update(_USER_VARS)
    return local


def _record_ans(value):
    """直近結果と ans スタックを更新。"""
    global _ANS
    _ANS = value
    _ANS_HISTORY.insert(0, value)
    if len(_ANS_HISTORY) > _ANS_DEPTH:
        del _ANS_HISTORY[_ANS_DEPTH:]


def evaluate(expr):
    """式文字列を評価して結果を返す。代入式 (`x = ...`) も許容。例外は呼び出し側で処理。"""
    expr = expr.strip()
    assignment = _split_assignment(expr)
    if assignment is not None:
        lhs, rhs = assignment
        if not _is_identifier(lhs):
            raise ValueError("invalid assignment target: " + lhs)
        if _is_reserved_target(lhs):
            raise ValueError("reserved name: " + lhs)
        if not rhs:
            raise ValueError("empty RHS")
        _check_safe(rhs)
        result = eval(rhs, {"__builtins__": {}}, _build_locals())
        _USER_VARS[lhs] = result
        _record_ans(result)
        return result

    _check_safe(expr)
    result = eval(expr, {"__builtins__": {}}, _build_locals())
    _record_ans(result)
    return result


# ---- CAS (記号計算 + 視覚レンダリング) ----------------------------------
#
# Phase 5a / Tier 1: 式パース → bounding box レイアウト → ハイブリッド描画
# (framebuf 8x8 ASCII グリフを 2x で拡大 + 線描画プリミティブ)
# 詳細は design/HANDOFF_phase5_cas.md を参照。
# 記号簡約は Tier 2 で別途。Tier 1 はそのままの式を視覚化するのみ。

# CAS レンダリング寸法 (2x スケール)
_CAS_CHAR_W = 16        # 8x8 framebuf font × 2x = 16 px / char
_CAS_CHAR_H = 16
_CAS_LINE_W = 2         # 分数バー・オーバーラインの太さ (px)
_CAS_SQRT_W = 10        # √ グリフ全体の幅 (px)
_CAS_GLYPH_SCALE = 2    # Greek 文字グリフ (16x16 source) のピクセル拡大率 → 32x32 出力


# AST ノード型（クラスベース、メモリ最小化のため __slots__）
class _CasNode:
    pass


class _CasNum(_CasNode):
    __slots__ = ("text",)
    def __init__(self, text):
        self.text = text         # 文字列保持 (10進・16進・指数表記そのまま)


class _CasVar(_CasNode):
    __slots__ = ("name",)
    def __init__(self, name):
        self.name = name


class _CasBinOp(_CasNode):
    __slots__ = ("op", "l", "r")
    def __init__(self, op, l, r):
        self.op = op             # '+' '-' '*' '/' '**' '%'
        self.l = l
        self.r = r


class _CasUnaryOp(_CasNode):
    __slots__ = ("op", "x")
    def __init__(self, op, x):
        self.op = op             # '+' '-'
        self.x = x


class _CasCall(_CasNode):
    __slots__ = ("name", "args")
    def __init__(self, name, args):
        self.name = name
        self.args = args         # list[_CasNode]


# --- Tokenizer / Parser ---

def _cas_tokenize(s):
    """式文字列をトークン列 [(kind, value), ...] に分解。"""
    tokens = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == " " or c == "\t":
            i += 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            start = i
            if c == "0" and i + 1 < n and s[i + 1] in "xXbBoO":
                i += 2
                while i < n and (s[i].isalpha() or s[i].isdigit() or s[i] == "_"):
                    i += 1
            else:
                i += 1
                while i < n:
                    ch = s[i]
                    if ch.isdigit() or ch == ".":
                        i += 1
                    elif ch in "eE":
                        i += 1
                        if i < n and s[i] in "+-":
                            i += 1
                    else:
                        break
            tokens.append(("NUM", s[start:i]))
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (s[i].isalpha() or s[i].isdigit() or s[i] == "_"):
                i += 1
            tokens.append(("NAME", s[start:i]))
        elif c == "*" and i + 1 < n and s[i + 1] == "*":
            tokens.append(("**", "**"))
            i += 2
        elif c in "+-*/%(),":
            tokens.append((c, c))
            i += 1
        else:
            raise ValueError("CAS: unexpected char " + repr(c))
    return tokens


class _CasParser:
    def __init__(self, tokens):
        self.toks = tokens
        self.pos = 0

    def _peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def _eat(self):
        t = self._peek()
        if t is not None:
            self.pos += 1
        return t

    def _expect(self, kind):
        t = self._peek()
        if t is None or t[0] != kind:
            raise ValueError("CAS: expected " + kind)
        return self._eat()

    def parse(self):
        node = self._add()
        if self._peek() is not None:
            raise ValueError("CAS: trailing tokens")
        return node

    def _add(self):
        left = self._mul()
        while True:
            t = self._peek()
            if t is None or t[0] not in ("+", "-"):
                break
            op = self._eat()[0]
            left = _CasBinOp(op, left, self._mul())
        return left

    def _mul(self):
        left = self._unary()
        while True:
            t = self._peek()
            if t is None or t[0] not in ("*", "/", "%"):
                break
            op = self._eat()[0]
            left = _CasBinOp(op, left, self._unary())
        return left

    def _unary(self):
        t = self._peek()
        if t and t[0] in ("+", "-"):
            op = self._eat()[0]
            return _CasUnaryOp(op, self._unary())
        return self._pow()

    def _pow(self):
        left = self._atom()
        t = self._peek()
        if t and t[0] == "**":
            self._eat()
            return _CasBinOp("**", left, self._unary())  # 右結合
        return left

    def _atom(self):
        t = self._peek()
        if t is None:
            raise ValueError("CAS: unexpected end")
        if t[0] == "NUM":
            self._eat()
            return _CasNum(t[1])
        if t[0] == "NAME":
            self._eat()
            name = t[1]
            if self._peek() and self._peek()[0] == "(":
                self._eat()
                args = []
                if self._peek() and self._peek()[0] != ")":
                    args.append(self._add())
                    while self._peek() and self._peek()[0] == ",":
                        self._eat()
                        args.append(self._add())
                self._expect(")")
                return _CasCall(name, args)
            return _CasVar(name)
        if t[0] == "(":
            self._eat()
            node = self._add()
            self._expect(")")
            return node
        raise ValueError("CAS: unexpected token " + repr(t))


def _cas_parse(expr_str):
    return _CasParser(_cas_tokenize(expr_str)).parse()


# --- Layout (bounding box + draw closure) ---

class _CasBox:
    """式描画用 bounding box。
    w, h はピクセル単位の寸法。baseline は box 上端から「主行」までのピクセル数で、
    二項演算で上下行揃え (基準揃え) に使う。draw は描画クロージャ。"""
    __slots__ = ("w", "h", "baseline", "_draw")
    def __init__(self, w, h, baseline, draw):
        self.w = w
        self.h = h
        self.baseline = baseline
        self._draw = draw
    def render(self, x, y, color):
        self._draw(x, y, color)


# --- Greek 文字グリフ (各 16x16、'#' = 点灯) ---

_GLYPH_PI = (
    "                ",
    "                ",
    "  ############  ",
    "  ############  ",
    "                ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "    ##    ##    ",
    "                ",
    "                ",
)

_GLYPH_THETA = (
    "                ",
    "                ",
    "     ####       ",
    "    #    #      ",
    "   #      #     ",
    "   #      #     ",
    "   ########     ",
    "   #      #     ",
    "   #      #     ",
    "    #    #      ",
    "     ####       ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_PHI = (
    "                ",
    "      ##        ",
    "      ##        ",
    "   ########     ",
    "  ##  ##  ##    ",
    "  #   ##   #    ",
    "  #   ##   #    ",
    "  #   ##   #    ",
    "  ##  ##  ##    ",
    "   ########     ",
    "      ##        ",
    "      ##        ",
    "                ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_LAMBDA = (
    "                ",
    "                ",
    "   ##           ",
    "    ##          ",
    "     #          ",
    "     ##         ",
    "      #         ",
    "      ##        ",
    "     ###        ",
    "     # ##       ",
    "    #   ##      ",
    "    #    ##     ",
    "   ##     ##    ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_ALPHA = (
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "    ####    ##  ",
    "   ##  ##   ##  ",
    "  ##    ## ##   ",
    "  ##    ####    ",
    "  ##    ## ##   ",
    "   ##  ##   ##  ",
    "    ####    ##  ",
    "                ",
    "                ",
)

_GLYPH_BETA = (
    "                ",
    "      ####      ",
    "     #    #     ",
    "    #      #    ",
    "    #     #     ",
    "    #####       ",
    "    #    #      ",
    "    #     #     ",
    "    #    #      ",
    "    #####       ",
    "    #           ",
    "    #           ",
    "    #           ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_GAMMA = (
    "                ",
    "                ",
    "                ",
    "   #       #    ",
    "   ##     ##    ",
    "    #    #      ",
    "    ##  ##      ",
    "     ####       ",
    "      ##        ",
    "      ##        ",
    "     #          ",
    "    #           ",
    "   #            ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_DELTA = (
    "                ",
    "       ####     ",
    "      #         ",
    "      #         ",
    "       ##       ",
    "        ##      ",
    "       #  #     ",
    "      #    #    ",
    "      #    #    ",
    "      #    #    ",
    "       #  #     ",
    "        ##      ",
    "                ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_EPSILON = (
    "                ",
    "                ",
    "                ",
    "                ",
    "     #####      ",
    "    #     #     ",
    "    #           ",
    "     ####       ",
    "    #           ",
    "    #     #     ",
    "     #####      ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_MU = (
    "                ",
    "                ",
    "                ",
    "                ",
    "   #      #     ",
    "   #      #     ",
    "   #      #     ",
    "   #      #     ",
    "   #      #     ",
    "   #     ##     ",
    "   ###  # #     ",
    "   #            ",
    "   #            ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_SIGMA = (
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "   ############ ",
    "  ##       ###  ",
    "  ##        ##  ",
    "  ##        ##  ",
    "   ##      ##   ",
    "    ########    ",
    "                ",
    "                ",
)

_GLYPH_TAU = (
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "   #########    ",
    "       #        ",
    "       #        ",
    "       #        ",
    "       #        ",
    "       #  #     ",
    "        ##      ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_OMEGA = (
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "                ",
    "  ##       ##   ",
    " ####     ####  ",
    " ## ##   ## ##  ",
    " ##  ## ##  ##  ",
    " ##   ###   ##  ",
    "  ##   #   ##   ",
    "                ",
    "                ",
)

_GLYPH_PHI_UPPER = (
    "                ",
    "       ##       ",
    "    ########    ",
    "   #   ##   #   ",
    "  #    ##    #  ",
    "  #    ##    #  ",
    "  #    ##    #  ",
    "  #    ##    #  ",
    "  #    ##    #  ",
    "   #   ##   #   ",
    "    ########    ",
    "       ##       ",
    "                ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_THETA_UPPER = (
    "                ",
    "                ",
    "     ####       ",
    "    #    #      ",
    "   #      #     ",
    "   #      #     ",
    "   #      #     ",
    "   #  ##  #     ",
    "   #      #     ",
    "   #      #     ",
    "   #      #     ",
    "    #    #      ",
    "     ####       ",
    "                ",
    "                ",
    "                ",
)

_GLYPH_OMEGA_UPPER = (
    "                ",
    "                ",
    "                ",
    "     #####      ",
    "    #     #     ",
    "   #       #    ",
    "   #       #    ",
    "   #       #    ",
    "   #       #    ",
    "   #       #    ",
    "    #     #     ",
    "     #   #      ",
    "   ##     ##    ",
    "  ###     ###   ",
    "                ",
    "                ",
)

_GLYPH_SIGMA_UPPER = (
    "                ",
    "                ",
    "  ###########   ",
    "  #         #   ",
    "   #        #   ",
    "    #           ",
    "     #          ",
    "      #         ",
    "       #        ",
    "      #         ",
    "     #          ",
    "    #           ",
    "   #            ",
    "  #         #   ",
    "  ###########   ",
    "                ",
)

_GLYPH_DELTA_UPPER = (
    "                ",
    "                ",
    "       ##       ",
    "       ##       ",
    "      ####      ",
    "      ####      ",
    "     ##  ##     ",
    "     ##  ##     ",
    "    ##    ##    ",
    "    ##    ##    ",
    "   ##      ##   ",
    "   ##      ##   ",
    "  ##        ##  ",
    "  ##        ##  ",
    "  ##############",
    "                ",
)


# 名前 → グリフのマップ (Python 識別子名 ⇒ Greek 文字グリフ)
# 小文字
_GLYPH_MAP = {
    "pi":      _GLYPH_PI,
    "theta":   _GLYPH_THETA,
    "phi":     _GLYPH_PHI,
    "lambda":  _GLYPH_LAMBDA,
    "alpha":   _GLYPH_ALPHA,
    "beta":    _GLYPH_BETA,
    "gamma":   _GLYPH_GAMMA,
    "delta":   _GLYPH_DELTA,
    "epsilon": _GLYPH_EPSILON,
    "mu":      _GLYPH_MU,
    "sigma":   _GLYPH_SIGMA,
    "tau":     _GLYPH_TAU,
    "omega":   _GLYPH_OMEGA,
    # 大文字 (Python の予約語と被るので、慣習的に capitalized で使う)
    "Phi":     _GLYPH_PHI_UPPER,
    "Theta":   _GLYPH_THETA_UPPER,
    "Omega":   _GLYPH_OMEGA_UPPER,
    "Sigma":   _GLYPH_SIGMA_UPPER,
    "Delta":   _GLYPH_DELTA_UPPER,
}


def _draw_glyph(x, y, glyph, color, scale=1):
    """指定のグリフ (string tuple) を (x, y) から描画。
    scale=1 で 16x16、scale=2 で 32x32 (各ソースピクセル → scale×scale ブロック)。"""
    if not _HW:
        return
    has_rect = hasattr(_display, "fill_rect")
    for py, row in enumerate(glyph):
        for px, c in enumerate(row):
            if c == " ":
                continue
            if scale == 1:
                _display.pixel(x + px, y + py, color)
            elif has_rect:
                _display.fill_rect(x + px * scale, y + py * scale, scale, scale, color)
            else:
                for dy in range(scale):
                    for dx in range(scale):
                        _display.pixel(x + px * scale + dx, y + py * scale + dy, color)


def _draw_text_small(x, y, s, color):
    """8x8 framebuf 組み込みフォントを 1x スケールで描画 (指数用)。"""
    if not _HW or not s:
        return
    import framebuf
    str_w = len(s) * 8
    pad_w = ((str_w + 7) // 8) * 8
    buf = bytearray((pad_w * 8) // 8)
    tmp = framebuf.FrameBuffer(buf, pad_w, 8, framebuf.MONO_HMSB)
    tmp.fill(0)
    tmp.text(s, 0, 0, 1)
    for py in range(8):
        for px in range(str_w):
            if tmp.pixel(px, py):
                _display.pixel(x + px, y + py, color)


def _cas_text_box(s):
    """CAS 文字列 box。Greek 名 ('pi' 等) は 32x32 グリフ、通常テキストは 16x16。"""
    # 名前がシンボルマップにあればグリフ描画 (例: 'pi' → π)
    if s in _GLYPH_MAP:
        glyph = _GLYPH_MAP[s]
        w = 16 * _CAS_GLYPH_SCALE       # 32 px (テキストより大きい)
        h = 16 * _CAS_GLYPH_SCALE
        bl = h // 2
        def draw(x, y, color):
            _draw_glyph(x, y, glyph, color, scale=_CAS_GLYPH_SCALE)
        return _CasBox(w, h, bl, draw)
    w = len(s) * _CAS_CHAR_W
    h = _CAS_CHAR_H
    bl = _CAS_CHAR_H // 2
    def draw(x, y, color):
        _draw_text_2x(x, y, s, color)
    return _CasBox(w, h, bl, draw)


def _cas_text_small_box(s):
    """小サイズ (8x8) の文字列 box (指数用)。"""
    w = len(s) * 8
    h = 8
    bl = h // 2
    def draw(x, y, color):
        _draw_text_small(x, y, s, color)
    return _CasBox(w, h, bl, draw)


def _cas_layout(node):
    if isinstance(node, _CasNum):
        return _cas_text_box(node.text)
    if isinstance(node, _CasVar):
        return _cas_text_box(node.name)
    if isinstance(node, _CasUnaryOp):
        # -x で x が BinOp なら括弧 (-(a+b))
        xb = _cas_layout_with_paren(node.x, 4, is_right=False, outer_op=node.op)
        op = node.op
        def draw(x, y, color):
            _draw_text_2x(x, y + xb.baseline - _CAS_CHAR_H // 2, op, color)
            xb.render(x + _CAS_CHAR_W, y, color)
        return _CasBox(_CAS_CHAR_W + xb.w, xb.h, xb.baseline, draw)
    if isinstance(node, _CasBinOp):
        if node.op == "/":
            return _cas_layout_fraction(node.l, node.r)
        if node.op == "**":
            return _cas_layout_power(node.l, node.r)
        # 暗黙の乗算 (数値 × 数値以外、または両辺ともに非数値) は * を省略して並置。
        # 両辺が数値リテラルのときだけ "2*3" のように * を残す (`23` と紛れるため)。
        if node.op == "*":
            ln = isinstance(node.l, _CasNum)
            rn = isinstance(node.r, _CasNum)
            if not (ln and rn):
                return _cas_layout_implicit_mul(node.l, node.r)
        return _cas_layout_binop_inline(node.op, node.l, node.r)
    if isinstance(node, _CasCall):
        if node.name == "sqrt" and len(node.args) == 1:
            return _cas_layout_sqrt(node.args[0])
        if node.name == "abs" and len(node.args) == 1:
            return _cas_layout_abs(node.args[0])
        return _cas_layout_call(node.name, node.args)
    raise ValueError("CAS: unknown node")


# --- 括弧の自動付与 ---

def _cas_prec(node):
    """演算子優先順位 (高いほど結合が強い)。atom = 5、unary = 4、** = 3、*/% = 2、+- = 1。"""
    if isinstance(node, _CasBinOp):
        if node.op in ("+", "-"):
            return 1
        if node.op in ("*", "/", "%"):
            return 2
        if node.op == "**":
            return 3
    if isinstance(node, _CasUnaryOp):
        return 4
    return 5


def _cas_paren_box(inner):
    """inner の左右に '(' ')' (2x スケール) を付けた Box を返す。"""
    paren_w = _CAS_CHAR_W
    paren_h = _CAS_CHAR_H
    h = max(inner.h, paren_h)
    bl = max(inner.baseline, paren_h // 2)
    w = paren_w + inner.w + paren_w
    def draw(x, y, color):
        _draw_text_2x(x, y + bl - paren_h // 2, "(", color)
        inner.render(x + paren_w, y + bl - inner.baseline, color)
        _draw_text_2x(x + paren_w + inner.w, y + bl - paren_h // 2, ")", color)
    return _CasBox(w, h, bl, draw)


def _cas_layout_with_paren(node, outer_prec, is_right=False, outer_op=None):
    """node のレイアウトを返す。outer_prec より優先順位が低い場合、または
    非可換演算 (-, /, %) の右側に同優先順位の演算がある場合、`(` `)` で囲む。"""
    box = _cas_layout(node)
    n_prec = _cas_prec(node)
    needs = False
    if n_prec < outer_prec:
        needs = True
    elif n_prec == outer_prec and is_right and outer_op in ("-", "/", "%"):
        needs = True
    return _cas_paren_box(box) if needs else box


def _cas_layout_implicit_mul(l, r):
    """暗黙の乗算 (2√5 のように * を表示しない並置レイアウト)。必要なら括弧付き。"""
    lb = _cas_layout_with_paren(l, 2, is_right=False, outer_op="*")
    rb = _cas_layout_with_paren(r, 2, is_right=True, outer_op="*")
    above = max(lb.baseline, rb.baseline)
    below = max(lb.h - lb.baseline, rb.h - rb.baseline)
    h = above + below
    bl = above
    gap = 2
    w = lb.w + gap + rb.w
    def draw(x, y, color):
        lb.render(x, y + above - lb.baseline, color)
        rb.render(x + lb.w + gap, y + above - rb.baseline, color)
    return _CasBox(w, h, bl, draw)


def _cas_layout_binop_inline(op, l, r):
    outer_prec = 2 if op in ("*", "/", "%") else 1
    lb = _cas_layout_with_paren(l, outer_prec, is_right=False, outer_op=op)
    rb = _cas_layout_with_paren(r, outer_prec, is_right=True, outer_op=op)
    op_text = " " + op + " "
    op_w = len(op_text) * _CAS_CHAR_W
    above = max(lb.baseline, rb.baseline)
    below = max(lb.h - lb.baseline, rb.h - rb.baseline)
    h = above + below
    bl = above
    w = lb.w + op_w + rb.w
    def draw(x, y, color):
        lb.render(x, y + above - lb.baseline, color)
        _draw_text_2x(x + lb.w, y + above - _CAS_CHAR_H // 2, op_text, color)
        rb.render(x + lb.w + op_w, y + above - rb.baseline, color)
    return _CasBox(w, h, bl, draw)


_CAS_FRAC_PAD = 2     # フラクションバーと分子・分母の間の余白 (px)


def _cas_layout_fraction(num, denom):
    nb = _cas_layout(num)
    db = _cas_layout(denom)
    bar_w = max(nb.w, db.w) + 8                              # 上下端より少し広めに
    h = nb.h + _CAS_FRAC_PAD + _CAS_LINE_W + _CAS_FRAC_PAD + db.h
    bl = nb.h + _CAS_FRAC_PAD + _CAS_LINE_W // 2             # 横棒中央を baseline に
    def draw(x, y, color):
        nb.render(x + (bar_w - nb.w) // 2, y, color)
        bar_y = y + nb.h + _CAS_FRAC_PAD
        if hasattr(_display, "fill_rect"):
            _display.fill_rect(x, bar_y, bar_w, _CAS_LINE_W, color)
        db.render(x + (bar_w - db.w) // 2, bar_y + _CAS_LINE_W + _CAS_FRAC_PAD, color)
    return _CasBox(bar_w, h, bl, draw)


def _cas_layout_power(base, exp):
    # 底に低優先順位演算が来たら括弧 ((a+b)^2 等)
    bb = _cas_layout_with_paren(base, 3, is_right=False, outer_op="**")
    # 指数: 数値は小フォント、それ以外は通常レイアウトに括弧付き (2^(a+b) 等)
    if isinstance(exp, _CasNum):
        eb = _cas_text_small_box(exp.text)
    else:
        eb = _cas_layout_with_paren(exp, 3, is_right=True, outer_op="**")
    exp_offset = max(2, bb.h // 2)                 # 指数を半文字分上にシフト
    above = bb.baseline + exp_offset
    h = above + (bb.h - bb.baseline)
    bl = above
    w = bb.w + eb.w + 2
    def draw(x, y, color):
        bb.render(x, y + above - bb.baseline, color)
        eb.render(x + bb.w + 2, y, color)
    return _CasBox(w, h, bl, draw)


def _cas_draw_sqrt_glyph(x, y, h, color):
    """√ 形のグリフを (x, y) から幅 _CAS_SQRT_W、高さ h で 2px 太線で描画。
    右上 (x+W-2, y) が overline と接続する位置になる。"""
    if not _HW:
        return
    bot_y = y + h - 1
    # 左フック (右下がりの斜め、2px 太線)
    if h >= 6 and hasattr(_display, "fill_rect"):
        _display.fill_rect(x, bot_y - 4, 2, 2, color)        # 上端
        _display.fill_rect(x + 2, bot_y - 2, 2, 2, color)    # 中央
    if hasattr(_display, "fill_rect"):
        _display.fill_rect(x + 4, bot_y - 1, 2, 2, color)    # 底点
    # 右上への対角線 (x+4, bot_y) → (x+W-2, y)、2px 太
    span = h - 1
    dx = (_CAS_SQRT_W - 2) - 4                                # 通常 4
    if span > 0:
        for i in range(1, span + 1):
            px = x + 4 + (dx * i) // span
            py = bot_y - i
            _display.pixel(px, py, color)
            if px + 1 < x + _CAS_SQRT_W:
                _display.pixel(px + 1, py, color)            # 2px 太


def _cas_layout_sqrt(child):
    xb = _cas_layout(child)
    overline = _CAS_LINE_W
    pad = 2                                           # オーバーラインと中身の間
    h = xb.h + overline + pad
    bl = h // 2
    w = _CAS_SQRT_W + xb.w + 4                        # 末尾余白
    def draw(x, y, color):
        _cas_draw_sqrt_glyph(x, y, h, color)
        overline_x = x + _CAS_SQRT_W
        if hasattr(_display, "fill_rect"):
            _display.fill_rect(overline_x, y, xb.w + 4, overline, color)
        xb.render(overline_x + 2, y + overline + pad, color)
    return _CasBox(w, h, bl, draw)


def _cas_layout_abs(child):
    xb = _cas_layout(child)
    bar_pad = _CAS_CHAR_W // 2                       # 縦棒の左右パディング
    w = bar_pad * 4 + xb.w
    h = xb.h
    bl = xb.baseline
    def draw(x, y, color):
        if hasattr(_display, "fill_rect"):
            _display.fill_rect(x + bar_pad, y, _CAS_LINE_W, h, color)
            _display.fill_rect(x + w - bar_pad - _CAS_LINE_W, y, _CAS_LINE_W, h, color)
        xb.render(x + bar_pad * 2, y, color)
    return _CasBox(w, h, bl, draw)


def _cas_layout_call(name, args):
    arg_boxes = [_cas_layout(a) for a in args]
    name_w = len(name) * _CAS_CHAR_W
    paren_w = _CAS_CHAR_W
    sep_w = _CAS_CHAR_W * 2                          # ", "
    aw = sum(b.w for b in arg_boxes) + (sep_w * max(0, len(arg_boxes) - 1))
    w = name_w + paren_w + aw + paren_w
    if arg_boxes:
        above = max(b.baseline for b in arg_boxes)
        below = max(b.h - b.baseline for b in arg_boxes)
        above = max(above, _CAS_CHAR_H // 2)
        below = max(below, _CAS_CHAR_H - _CAS_CHAR_H // 2)
    else:
        above = _CAS_CHAR_H // 2
        below = _CAS_CHAR_H - _CAS_CHAR_H // 2
    h = above + below
    bl = above
    def draw(x, y, color):
        _draw_text_2x(x, y + above - _CAS_CHAR_H // 2, name + "(", color)
        cx = x + name_w + paren_w
        for i, ab in enumerate(arg_boxes):
            if i > 0:
                _draw_text_2x(cx, y + above - _CAS_CHAR_H // 2, ", ", color)
                cx += sep_w
            ab.render(cx, y + above - ab.baseline, color)
            cx += ab.w
        _draw_text_2x(cx, y + above - _CAS_CHAR_H // 2, ")", color)
    return _CasBox(w, h, bl, draw)


# --- Tier 2 + 3a: 記号簡約 ---
# 基本ルール:
#   - 算術畳み込み (定数の計算結果に置換)
#   - 分数約分 (gcd ベース)
#   - sqrt の完全平方因子抽出
#   - 単純恒等式 (x*1, x+0, ...)
#   - 数値係数を前置 (sqrt(5)*9 -> 9*sqrt(5))
#   - 暗黙の乗算 / 分数 normalization
# Phase 5c 追加:
#   - 同じ底のべき乗結合 (x^a * x^b -> x^(a+b)、x*x -> x^2)
#   - 同類項結合 (a*x + b*x -> (a+b)*x、x+x -> 2*x)
#   - 分配 (Num * (a±b) -> Num*a ± Num*b)


def _extract_coef(node):
    """ノードを (coef_int, rest_node) に分解する。同類項結合のため。
    例:  2*x -> (2, x);  x -> (1, x);  -x -> (-1, x);  5 -> (5, None)
    数値定数のみの場合 rest=None。整数係数として抽出できない場合 (1, node)。"""
    if isinstance(node, _CasNum):
        ci = _try_int(node.text)
        if ci is not None:
            return ci, None
        return 1, node
    if isinstance(node, _CasUnaryOp) and node.op == "-":
        c, r = _extract_coef(node.x)
        return -c, r
    if isinstance(node, _CasBinOp) and node.op == "*":
        if isinstance(node.l, _CasNum):
            ci = _try_int(node.l.text)
            if ci is not None:
                return ci, node.r
        if isinstance(node.r, _CasNum):
            ci = _try_int(node.r.text)
            if ci is not None:
                return ci, node.l
    return 1, node


def _make_coef_term(coef, rest):
    """係数 coef とシンボリック部分 rest から AST を再構築する。"""
    if rest is None:
        return _CasNum(_num_text(coef))
    if coef == 0:
        return _CasNum("0")
    if coef == 1:
        return rest
    if coef == -1:
        return _CasUnaryOp("-", rest)
    return _CasBinOp("*", _CasNum(_num_text(coef)), rest)


# --- Tier 2: 記号簡約 (基本のみ: 算術畳み込み / 分数約分 / sqrt 素因数分解 / 単純恒等式) ---

def _gcd(a, b):
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def _extract_sqrt(n):
    """n から完全平方因子を抽出。n = sq*sq * rem として (sq, rem) を返す。"""
    if n <= 0:
        return 1, n
    sq = 1
    i = 2
    while i * i <= n:
        if n % (i * i) == 0:
            n //= (i * i)
            sq *= i
        else:
            i += 1
    return sq, n


def _num_text(v):
    """数値を CasNum 用のテキスト表現にする (整数なら小数点なし)。"""
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return "{:.10g}".format(v)
    return str(v)


def _is_num_eq(node, val):
    if not isinstance(node, _CasNum):
        return False
    try:
        return float(node.text) == val
    except ValueError:
        return False


def _try_int(text):
    """テキストを int に変換。10/16/2/8 進対応。失敗時 None。"""
    try:
        if text.startswith(("0x", "0X")):
            return int(text, 16)
        if text.startswith(("0b", "0B")):
            return int(text, 2)
        if text.startswith(("0o", "0O")):
            return int(text, 8)
        if "." in text or "e" in text or "E" in text:
            return None
        return int(text)
    except ValueError:
        return None


def _try_float(text):
    try:
        if text.startswith(("0x", "0X")):
            return float(int(text, 16))
        if text.startswith(("0b", "0B")):
            return float(int(text, 2))
        if text.startswith(("0o", "0O")):
            return float(int(text, 8))
        return float(text)
    except ValueError:
        return None


def _cas_simplify(node):
    """AST を再帰的に簡約。簡約不能なら同形ノードを返す。"""
    if isinstance(node, _CasNum) or isinstance(node, _CasVar):
        return node
    if isinstance(node, _CasUnaryOp):
        x = _cas_simplify(node.x)
        if node.op == "+":
            return x
        if node.op == "-" and isinstance(x, _CasNum):
            v = _try_float(x.text)
            if v is not None:
                return _CasNum(_num_text(-v))
        return _CasUnaryOp(node.op, x)
    if isinstance(node, _CasBinOp):
        l = _cas_simplify(node.l)
        r = _cas_simplify(node.r)
        op = node.op
        # 整数畳み込み (両辺整数のとき)
        li = _try_int(l.text) if isinstance(l, _CasNum) else None
        ri = _try_int(r.text) if isinstance(r, _CasNum) else None
        if li is not None and ri is not None:
            if op == "+":
                return _CasNum(_num_text(li + ri))
            if op == "-":
                return _CasNum(_num_text(li - ri))
            if op == "*":
                return _CasNum(_num_text(li * ri))
            if op == "/":
                if ri != 0:
                    g = _gcd(li, ri)
                    if g > 0:
                        ln, rn = li // g, ri // g
                        if rn == 1:
                            return _CasNum(_num_text(ln))
                        if rn == -1:
                            return _CasNum(_num_text(-ln))
                        return _CasBinOp("/", _CasNum(_num_text(ln)), _CasNum(_num_text(rn)))
            if op == "%" and ri != 0:
                return _CasNum(_num_text(li % ri))
            if op == "**" and ri >= 0:
                return _CasNum(_num_text(li ** ri))
        # 浮動小数畳み込み (両辺数値・整数簡約失敗のとき)
        lf = _try_float(l.text) if isinstance(l, _CasNum) else None
        rf = _try_float(r.text) if isinstance(r, _CasNum) else None
        if lf is not None and rf is not None and (li is None or ri is None):
            try:
                if op == "+":
                    return _CasNum(_num_text(lf + rf))
                if op == "-":
                    return _CasNum(_num_text(lf - rf))
                if op == "*":
                    return _CasNum(_num_text(lf * rf))
                if op == "/" and rf != 0:
                    return _CasNum(_num_text(lf / rf))
                if op == "**":
                    return _CasNum(_num_text(lf ** rf))
            except (ValueError, ZeroDivisionError, OverflowError):
                pass
        # 恒等式
        if op == "+":
            if _is_num_eq(l, 0):
                return r
            if _is_num_eq(r, 0):
                return l
        elif op == "-":
            if _is_num_eq(r, 0):
                return l
            if _cas_nodes_equal(l, r):
                return _CasNum("0")
        elif op == "*":
            if _is_num_eq(l, 1):
                return r
            if _is_num_eq(r, 1):
                return l
            if _is_num_eq(l, 0) or _is_num_eq(r, 0):
                return _CasNum("0")
        elif op == "/":
            if _is_num_eq(r, 1):
                return l
            if _cas_nodes_equal(l, r) and not _is_num_eq(r, 0):
                return _CasNum("1")
        # Phase 5c: 同類項結合 (a*x + b*x → (a+b)*x、x+x → 2*x、similar for -)
        if op in ("+", "-"):
            lc, lt = _extract_coef(l)
            rc, rt = _extract_coef(r)
            if lt is not None and rt is not None and _cas_nodes_equal(lt, rt):
                new_coef = lc + rc if op == "+" else lc - rc
                # 結合後の式に対し再帰的に簡約 (分配が必要な場合に有効)
                return _cas_simplify(_make_coef_term(new_coef, lt))
        # Phase 5c: 同じ底のべき乗結合 (x^a * x^b → x^(a+b)、x*x → x^2)
        if op == "*":
            # x * x → x^2
            if _cas_nodes_equal(l, r) and not isinstance(l, _CasNum):
                return _CasBinOp("**", l, _CasNum("2"))
            # x^a * x^b → x^(a+b)
            if (isinstance(l, _CasBinOp) and l.op == "**"
                    and isinstance(r, _CasBinOp) and r.op == "**"
                    and _cas_nodes_equal(l.l, r.l)):
                new_exp = _cas_simplify(_CasBinOp("+", l.r, r.r))
                return _CasBinOp("**", l.l, new_exp)
            # x^a * x → x^(a+1)
            if isinstance(l, _CasBinOp) and l.op == "**" and _cas_nodes_equal(l.l, r):
                new_exp = _cas_simplify(_CasBinOp("+", l.r, _CasNum("1")))
                return _CasBinOp("**", l.l, new_exp)
            # x * x^a → x^(a+1)
            if isinstance(r, _CasBinOp) and r.op == "**" and _cas_nodes_equal(l, r.l):
                new_exp = _cas_simplify(_CasBinOp("+", r.r, _CasNum("1")))
                return _CasBinOp("**", l, new_exp)
        # Phase 5c: 分配 (Num * (a±b) → Num*a ± Num*b)
        if op == "*":
            if isinstance(l, _CasNum) and isinstance(r, _CasBinOp) and r.op in ("+", "-"):
                new_l = _cas_simplify(_CasBinOp("*", l, r.l))
                new_r = _cas_simplify(_CasBinOp("*", l, r.r))
                return _cas_simplify(_CasBinOp(r.op, new_l, new_r))
            if isinstance(r, _CasNum) and isinstance(l, _CasBinOp) and l.op in ("+", "-"):
                new_l = _cas_simplify(_CasBinOp("*", l.l, r))
                new_r = _cas_simplify(_CasBinOp("*", l.r, r))
                return _cas_simplify(_CasBinOp(l.op, new_l, new_r))
        # Phase 5c: ネスト係数の畳み込み (c1 * (c2 * x) → (c1*c2) * x、両配置に対応)
        if op == "*":
            if isinstance(l, _CasNum) and isinstance(r, _CasBinOp) and r.op == "*":
                if isinstance(r.l, _CasNum):
                    return _cas_simplify(_CasBinOp("*",
                                                    _CasBinOp("*", l, r.l), r.r))
                if isinstance(r.r, _CasNum):
                    return _cas_simplify(_CasBinOp("*",
                                                    _CasBinOp("*", l, r.r), r.l))
            if isinstance(r, _CasNum) and isinstance(l, _CasBinOp) and l.op == "*":
                if isinstance(l.l, _CasNum):
                    return _cas_simplify(_CasBinOp("*",
                                                    _CasBinOp("*", l.l, r), l.r))
                if isinstance(l.r, _CasNum):
                    return _cas_simplify(_CasBinOp("*",
                                                    _CasBinOp("*", l.r, r), l.l))
        # 乗算 × 分数の畳み込み: (a/b)*c → (a*c)/b、c*(a/b) → (c*a)/b
        if op == "*":
            if isinstance(l, _CasBinOp) and l.op == "/" and isinstance(r, _CasNum):
                new_num = _cas_simplify(_CasBinOp("*", l.l, r))
                return _CasBinOp("/", new_num, l.r)
            if isinstance(r, _CasBinOp) and r.op == "/" and isinstance(l, _CasNum):
                new_num = _cas_simplify(_CasBinOp("*", l, r.l))
                return _CasBinOp("/", new_num, r.r)
            # 数値係数を前に: (non-Num) * Num → Num * (non-Num)
            if isinstance(r, _CasNum) and not isinstance(l, _CasNum):
                return _CasBinOp("*", r, l)
        # べき乗の単純化: x^0 → 1、x^1 → x
        if op == "**":
            if _is_num_eq(r, 0):
                return _CasNum("1")
            if _is_num_eq(r, 1):
                return l
            if _is_num_eq(l, 1):
                return _CasNum("1")
            if _is_num_eq(l, 0):
                return _CasNum("0")
        return _CasBinOp(op, l, r)
    if isinstance(node, _CasCall):
        args = [_cas_simplify(a) for a in node.args]
        # sqrt 素因数分解
        if node.name == "sqrt" and len(args) == 1:
            a = args[0]
            if isinstance(a, _CasNum):
                vi = _try_int(a.text)
                if vi is not None and vi >= 0:
                    sq, rem = _extract_sqrt(vi)
                    if rem == 1:
                        return _CasNum(_num_text(sq))
                    if sq > 1:
                        return _CasBinOp(
                            "*",
                            _CasNum(_num_text(sq)),
                            _CasCall("sqrt", [_CasNum(_num_text(rem))]),
                        )
        return _CasCall(node.name, args)
    return node


def _cas_nodes_equal(a, b):
    """AST 構造比較 (簡約前後の変化検出に使う)。"""
    if type(a) is not type(b):
        return False
    if isinstance(a, _CasNum):
        return a.text == b.text
    if isinstance(a, _CasVar):
        return a.name == b.name
    if isinstance(a, _CasUnaryOp):
        return a.op == b.op and _cas_nodes_equal(a.x, b.x)
    if isinstance(a, _CasBinOp):
        return a.op == b.op and _cas_nodes_equal(a.l, b.l) and _cas_nodes_equal(a.r, b.r)
    if isinstance(a, _CasCall):
        if a.name != b.name or len(a.args) != len(b.args):
            return False
        for ai, bi in zip(a.args, b.args):
            if not _cas_nodes_equal(ai, bi):
                return False
        return True
    return False


# --- 履歴参照解決 + 表示 ---

def _cas_resolve_ref(ref, history):
    """ref ('ans' / 'ans2' .. 'ansN') を履歴の式文字列に解決。失敗時 None。"""
    if not history.items:
        return None
    if ref == "ans" or ref == "ans1":
        return history.items[-1][0]
    if ref.startswith("ans") and ref[3:] and ref[3:].isdigit():
        n = int(ref[3:])
        if 1 <= n <= len(history.items):
            return history.items[-n][0]
    return None


def _show_big_calc(expr_str, res_str):
    """計算結果を動的領域中央に表示し、任意キーで戻る。

    レイアウト (上から):
        Line 1: 入力式 (テキスト 2x、FG)
        Line 2: 入力式の数式記法 (CAS layout、FG) — パース成功時
        Line 3: 数値計算結果 `= res_str` (テキスト 2x、ACC) — res_str が None でなければ
        Line 4: 記号簡約結果の数式記法 (CAS layout、ACC) — 簡約形が異なる場合

    `res_str` に None を渡すと「symbolic-only モード」になり Line 3 を省略する
    (eval が NameError で失敗したケース)。
    """
    if not _HW:
        print(expr_str + (" = " + res_str if res_str is not None else "  (symbolic)"))
        return
    max_cols = SCREEN_W // _CAS_CHAR_W

    def _trunc(s):
        return s if len(s) <= max_cols else s[:max_cols - 1] + "~"

    expr_disp = _trunc(expr_str)
    eq_disp = _trunc("= " + res_str) if res_str is not None else None

    # パース + 簡約 (失敗時は line 2/4 をスキップ)
    node = None
    simplified = None
    try:
        node = _cas_parse(expr_str)
        simplified = _cas_simplify(node)
    except Exception:
        pass

    line_box = None
    line_box_simp = None
    if node is not None:
        try:
            line_box = _cas_layout(node)
        except Exception:
            line_box = None
        # 4 行目 (簡約結果) は次のすべてを満たすときのみ表示:
        # - 簡約が成功している
        # - 簡約 AST が原式 AST と構造的に異なる
        # - 数値結果ありの場合、簡約結果が「数値リテラルかつ res_str と同値」ではない (重複抑制)
        if simplified is not None and not _cas_nodes_equal(node, simplified):
            redundant = (
                res_str is not None
                and isinstance(simplified, _CasNum)
                and simplified.text == res_str
            )
            if not redundant:
                try:
                    line_box_simp = _cas_layout(simplified)
                except Exception:
                    line_box_simp = None

    # 各行 (height, width, draw(x, y)) のリストを構築
    lines = []
    lines.append((_CAS_CHAR_H, len(expr_disp) * _CAS_CHAR_W,
                  lambda x, y, s=expr_disp: _draw_text_2x(x, y, s, COL_FG)))
    if line_box is not None:
        b = line_box
        lines.append((b.h, b.w,
                      lambda x, y, box=b: box.render(x, y, COL_FG)))
    if eq_disp is not None:
        lines.append((_CAS_CHAR_H, len(eq_disp) * _CAS_CHAR_W,
                      lambda x, y, s=eq_disp: _draw_text_2x(x, y, s, COL_ACC)))
    if line_box_simp is not None:
        b = line_box_simp
        lines.append((b.h, b.w,
                      lambda x, y, box=b: box.render(x, y, COL_ACC)))

    gap = _CAS_CHAR_H // 2                       # 行間 8 px
    total_h = sum(h for h, _w, _d in lines) + gap * (len(lines) - 1)
    avail = _ACTIVE_BOTTOM - _ACTIVE_TOP
    top_y = _ACTIVE_TOP + max(0, (avail - total_h) // 2)

    _clear_active()
    y = top_y
    for i, (h, w, draw_fn) in enumerate(lines):
        x = max(0, (SCREEN_W - w) // 2)
        try:
            draw_fn(x, y)
        except Exception:
            pass
        y += h + gap
    _show()
    while True:
        k = _read_key()
        if isinstance(k, tuple):
            continue
        break


def _show_cas(expr_str):
    """式を視覚レンダリングして任意キーで戻る。"""
    if not _HW:
        print("CAS:", expr_str)
        return
    try:
        node = _cas_parse(expr_str)
        box = _cas_layout(node)
    except Exception as ex:
        _clear_active()
        _draw_text(0, _ACTIVE_TOP + CHAR_H, "CAS parse error:", COL_ACC)
        _draw_text(0, _ACTIVE_TOP + 2 * CHAR_H, str(ex)[:COLS], COL_FG)
        _show()
        while True:
            k = _read_key()
            if isinstance(k, tuple):
                continue
            break
        return
    _clear_active()
    # box を動的領域の中央に配置 (はみ出す場合は左上に寄せる)
    cx = max(0, (SCREEN_W - box.w) // 2)
    avail_h = _ACTIVE_BOTTOM - _ACTIVE_TOP - CHAR_H  # 下部 1 行ぶんは元式表示用に確保
    cy = _ACTIVE_TOP + max(0, (avail_h - box.h) // 2)
    box.render(cx, cy, COL_FG)
    # 元式テキストを薄く下に表示 (リファレンス)
    src = expr_str[:COLS]
    _draw_text(0, _ACTIVE_BOTTOM - CHAR_H, src, COL_DIM)
    _show()
    while True:
        k = _read_key()
        if isinstance(k, tuple):
            continue
        break


# ---- 履歴管理 ------------------------------------------------------------

class History:
    def __init__(self, path=HISTORY_PATH, limit=HISTORY_MAX):
        self.path = path
        self.limit = limit
        self.items = []          # [(expr, result_str), ...]
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if "\t" in line:
                        expr, res = line.split("\t", 1)
                        self.items.append((expr, res))
        except OSError:
            pass  # 初回はファイルなし -> 空のまま
        if len(self.items) > self.limit:
            self.items = self.items[-self.limit:]

    def add(self, expr, result_str):
        self.items.append((expr, result_str))
        if len(self.items) > self.limit:
            self.items = self.items[-self.limit:]
        # 追記 (1 計算 1 行)。SD 無し環境では黙って無視。
        try:
            with open(self.path, "a") as f:
                f.write("{}\t{}\n".format(expr, result_str))
        except OSError:
            pass

    def clear(self):
        self.items = []
        try:
            with open(self.path, "w") as f:
                f.write("")
        except OSError:
            pass


# ---- 入力 (キーボード抽象化) --------------------------------------------
#
# PicoCalc のキーボード取得は機種・ファーム差が大きいため _read_key() に集約。
# 多くのファームでは sys.stdin もしくは picocalc のキー API でコードを取得できる。
# ここでは「1 文字を待って返す」ブロッキング取得を実装する。

import sys
import time

# 特殊キーコード
KEY_ENTER = "\n"
KEY_ENTER2 = "\r"
KEY_BACKSPACE = "\x08"
KEY_BACKSPACE2 = "\x7f"
KEY_ESC = "\x1b"

POLL_MS = 10   # 実機キーポーリング間隔


def _read_key():
    """1 文字(または特殊キー/エスケープシーケンス)を返すブロッキング入力。

    実機 (PicoCalc): picocalc.terminal.readinto(buf) は非ブロッキング。
    ランチャ経由起動時は terminal がキー入力をバッファリングするため、
    keyboard 直接アクセスではなく terminal 経由で読む必要がある。
    キーは 1 回の呼び出しで完全な形で返る:
        - 通常文字: 1 byte
        - Enter:    b'\\r\\n'   (2 byte)
        - Esc 単押: b'\\x1b\\x1b' (2 byte, picocalc.py 流儀)
        - 矢印:     b'\\x1b[A/B/C/D'
        - Backspace: 0x7F
    PC フォールバックでは従来通り sys.stdin で動作。
    """
    if not _HW:
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # PC 側も bytes に揃えて main() の比較を一本化
            return ("ESCSEQ", sys.stdin.read(2).encode())
        return ch

    buf = bytearray(8)
    while True:
        try:
            n = picocalc.terminal.readinto(buf)
        except OSError:
            n = None
        if n:
            break
        time.sleep_ms(POLL_MS)

    if n >= 2 and buf[0] == 0x1b and buf[1] == 0x1b:
        return KEY_ESC
    if buf[0] == 0x1b:
        return ("ESCSEQ", bytes(buf[1:n]))
    if buf[0] in (0x0A, 0x0D):
        return KEY_ENTER
    if buf[0] in (0x08, 0x7F):
        return KEY_BACKSPACE
    return chr(buf[0])


# ---- 描画 ----------------------------------------------------------------

def _draw_text(x, y, s, color=COL_FG):
    if _HW:
        _display.text(s, x, y, color)
    else:
        pass  # PC フォールバックでは描画しない


def _draw_text_2x(x, y, s, color=COL_FG):
    """framebuf 組み込み 8x8 フォントを 2 倍に拡大して「ノーマル太さ」で描画。

    一時 MONO_HMSB バッファに 1x で描き、各セットピクセルを 2x 座標に 1x1 ドットで
    スタンプ + 右隣・下隣・右下隣が set なら接続ピクセルを補う。これにより文字は
    16x16 のサイズ感だが線の太さは 1px のままになる (2x2 ブロック塗りつぶしと
    比較して bold 感がなくなる)。
    """
    if not _HW or not s:
        return
    import framebuf
    str_w_px = len(s) * INPUT_BASE_W                 # 8 px / char
    pad_w = ((str_w_px + 7) // 8) * 8                # 8 の倍数に切り上げ
    buf = bytearray((pad_w * INPUT_BASE_H) // 8)
    tmp = framebuf.FrameBuffer(buf, pad_w, INPUT_BASE_H, framebuf.MONO_HMSB)
    tmp.fill(0)
    tmp.text(s, 0, 0, 1)
    for py in range(INPUT_BASE_H):
        for px in range(str_w_px):
            if not tmp.pixel(px, py):
                continue
            dx = x + px * INPUT_SCALE
            dy = y + py * INPUT_SCALE
            _display.pixel(dx, dy, color)
            # 隣接 set ピクセルとの間を 1 px の橋渡しで繋ぐ (線が途切れないように)
            right_set = (px + 1 < str_w_px) and tmp.pixel(px + 1, py)
            down_set = (py + 1 < INPUT_BASE_H) and tmp.pixel(px, py + 1)
            if right_set:
                _display.pixel(dx + 1, dy, color)
            if down_set:
                _display.pixel(dx, dy + 1, color)
            # 斜め接続 (右下が set で右・下が両方とも未 set の場合のみ)
            if (px + 1 < str_w_px and py + 1 < INPUT_BASE_H and
                    tmp.pixel(px + 1, py + 1) and not right_set and not down_set):
                _display.pixel(dx + 1, dy + 1, color)


def _clear():
    if _HW:
        _display.fill(COL_BG)


def _show():
    if _HW:
        try:
            _display.show()
        except Exception:
            pass


def _maybe_load_chrome():
    """`CHROME_IMG_PATH` が存在すれば chrome 画像を読み込みレイアウトを再計算。
    存在しない場合は何もしない（全画面動作のまま）。"""
    global _chrome_buf
    global _ACTIVE_TOP, _ACTIVE_BOTTOM, _HISTORY_Y0, _HISTORY_ROWS, _MESSAGE_Y, _INPUT_Y
    if not _HW:
        return
    try:
        import framebuf
    except ImportError:
        return
    buf = bytearray(CHROME_BYTES)
    try:
        with open(CHROME_IMG_PATH, "rb") as f:
            n = f.readinto(buf)
        if n != CHROME_BYTES:
            return
    except OSError:
        return
    _chrome_buf = (buf, framebuf.FrameBuffer(buf, SCREEN_W, SCREEN_H, framebuf.GS4_HMSB))
    # レイアウトを chrome 対応値に更新
    _ACTIVE_TOP = CHROME_TOP_DEFAULT_H
    _ACTIVE_BOTTOM = SCREEN_H - CHROME_BOTTOM_DEFAULT_H
    _HISTORY_Y0 = _ACTIVE_TOP
    _INPUT_Y = _ACTIVE_BOTTOM - INPUT_CHAR_H - INPUT_BOTTOM_PAD   # 入力下余白を確保
    _MESSAGE_Y = _INPUT_Y - INPUT_TOP_PAD - CHAR_H                # 入力上余白も確保
    _HISTORY_ROWS = (_MESSAGE_Y - _HISTORY_Y0) // CHAR_H          # 動的算出


def _redraw_chrome():
    """Chrome 画像を blit (theme 変更後・help 終了後の復元に使う)。"""
    if _chrome_buf is None or not _HW:
        return
    palette = _build_help_palette()
    try:
        _display.blit(_chrome_buf[1], 0, 0, -1, palette)
    except TypeError:
        _display.blit(_chrome_buf[1], 0, 0)


def _free_chrome():
    """`_chrome_buf` を解放し、had_chrome (元々ロードされていたか) を返す。
    `_maybe_load_chrome` が再ロード可能なので、help 用にメモリを開ける時に使う。"""
    global _chrome_buf
    had = _chrome_buf is not None
    _chrome_buf = None
    return had


def _clear_active():
    """動的領域のみクリア。chrome 未装着時は全画面クリアと等価。"""
    if not _HW:
        return
    if _chrome_buf is None:
        _display.fill(COL_BG)
    else:
        _display.fill_rect(0, _ACTIVE_TOP, SCREEN_W, _ACTIVE_BOTTOM - _ACTIVE_TOP, COL_BG)


_HELP_LINES = [
    "Psephos Help",
    "============",
    "",
    "Trig    sin cos tan asin acos atan atan2",
    "ExpLog  exp log log10 sqrt pow",
    "Round   floor ceil fabs abs round",
    "Angle   radians degrees",
    "Radix   hex bin oct int float",
    "Const   pi e tau",
    "Util    min max",
    "Ans     ans  ans2..ans10",
    "Vars    x = 3   (session only, no shadow of builtins)",
    "Literal 1.5e-10   0xFF   0b101   0o777",
    "",
    "Keys",
    "  Up/Down     history recall / restore",
    "  Left/Right  cursor move",
    "  Home/End    line start / end",
    "  Backspace   delete before cursor",
    "  Enter       evaluate",
    "  ESC         quit Psephos",
    "",
    "Commands",
    "  help              this screen",
    "  theme             list available themes",
    "  theme <name>      apply theme (default/amber/green/cyan/invert)",
    "  clear             clear history (asks y/n)",
    "",
    "Press any key to return...",
]


HELP_PAGE_PATHS = ("/sd/psephos_help_p1.bin", "/sd/psephos_help_p2.bin")
HELP_PAGE_BYTES = (SCREEN_W * SCREEN_H) // 2   # GS4_HMSB: 4bpp = 51,200 byte


def _build_help_palette():
    """16 エントリのテーマカラーパレットを GS4_HMSB FrameBuffer として構築。

    画像のピクセル値 (セマンティック index) -> 実テーマ色 のマッピング:
        0 -> COL_BG  (背景)
        1 -> COL_FG  (本文)
        2 -> COL_DIM (補足)
        3 -> COL_ACC (見出し)
        4..15 -> COL_FG (フォールバック、本来未使用)

    GS4_HMSB packing: 1 byte = 2 pixel, high nibble = 偶数 index, low nibble = 奇数 index。
    """
    import framebuf
    pal = bytearray(8)
    pal[0] = (COL_BG << 4) | (COL_FG & 0x0F)    # px 0, 1
    pal[1] = (COL_DIM << 4) | (COL_ACC & 0x0F)  # px 2, 3
    fb = (COL_FG << 4) | (COL_FG & 0x0F)
    for i in range(2, 8):
        pal[i] = fb                              # px 4..15 fallback
    return framebuf.FrameBuffer(pal, 16, 1, framebuf.GS4_HMSB)


def _check_help_pages():
    """全ヘルプページファイルの存在を確認。問題なければ True。"""
    for path in HELP_PAGE_PATHS:
        try:
            with open(path, "rb") as f:
                pass
        except OSError:
            return False
    return True


def _show_help():
    """ヘルプ画面を表示し、任意キーで戻る (1 ページずつロードで省メモリ)。"""
    if not _HW:
        for line in _HELP_LINES:
            print(line)
        return

    if not _check_help_pages():
        # フォールバック: テキストヘルプ (6x8 ASCII)
        _clear()
        for r, line in enumerate(_HELP_LINES):
            if r >= ROWS:
                break
            _draw_text(0, r * CHAR_H, line[:COLS], COL_FG)
        _show()
        while True:
            k = _read_key()
            if isinstance(k, tuple):
                continue
            break
        return

    import framebuf
    try:
        import gc
        gc.collect()
    except ImportError:
        pass
    palette = _build_help_palette()
    page_buf = bytearray(HELP_PAGE_BYTES)               # 1 ページぶんのみ常駐
    page_fb = framebuf.FrameBuffer(page_buf, SCREEN_W, SCREEN_H, framebuf.GS4_HMSB)
    idx = 0
    n_pages = len(HELP_PAGE_PATHS)

    def _draw():
        try:
            with open(HELP_PAGE_PATHS[idx], "rb") as f:
                f.readinto(page_buf)
        except OSError:
            return
        _clear()
        try:
            _display.blit(page_fb, 0, 0, -1, palette)
        except TypeError:
            _display.blit(page_fb, 0, 0)
        _show()

    _draw()
    while True:
        k = _read_key()
        if isinstance(k, tuple) and k[0] == "ESCSEQ":
            seq = k[1]
            if seq == b"[C" and idx < n_pages - 1:
                idx += 1
                _draw()
            elif seq == b"[D" and idx > 0:
                idx -= 1
                _draw()
            continue
        # 通常キー (ESC 含む) で戻る
        break


def render(history, buf, cursor, message=""):
    """履歴域 + 入力行を描画。cursor は buf 内のカーソル位置 (0 〜 len(buf))。"""
    if not _HW:
        # PC フォールバック: 端末に出力してロジックだけ確認
        for expr, res in history.items[-5:]:
            print("  {} = {}".format(expr, res))
        shown = buf[:cursor] + "|" + buf[cursor:]
        print("> " + shown + ("   [" + message + "]" if message else ""))
        return

    # 動的領域のみクリア (chrome 装着時は chrome を消さない)
    _clear_active()

    # --- 履歴域 (古い順に上から、最新が下に来るよう末尾を表示。左に 2 文字分の余白) ---
    visible = history.items[-_HISTORY_ROWS:]
    for row, (expr, res) in enumerate(visible):
        # 代入式 `x = 5` で結果も `5` のとき "x = 5 = 5" になるのを抑制
        if expr.endswith(" = " + res) or expr.endswith("=" + res):
            line = expr
        else:
            line = "{} = {}".format(expr, res)
        if len(line) > HISTORY_COLS:
            line = line[:HISTORY_COLS - 1] + "~"
        _draw_text(HISTORY_LEFT_PX, _HISTORY_Y0 + row * CHAR_H, line, COL_DIM)

    # --- 区切り線 (入力行の 3 px 上、上下 2px の余白を確保) ---
    if hasattr(_display, "hline"):
        _display.hline(0, _INPUT_Y - 3, SCREEN_W, COL_DIM)

    # --- 入力行 (2x スケール、カーソル位置を見せるためのスクロール) ---
    # 画面端から 1 文字分のマージンを確保 (INPUT_COLS - 1 列が実効表示幅)
    prefix = "> "
    full = prefix + buf
    visible_cols = INPUT_COLS - 1                    # 19 cols 実効幅
    # カーソルが visible 範囲に収まるようシフト量を決定。カーソル論理列 = len(prefix)+cursor
    shift = max(0, len(prefix) + cursor - (visible_cols - 1))
    prompt = full[shift:shift + visible_cols]
    _draw_text_2x(0, _INPUT_Y, prompt, COL_FG)

    # --- カーソル下線 (アクセント色、2x スケールに合わせて 16 px 幅・下端 2 px) ---
    cx_chars = len(prefix) + cursor - shift
    if 0 <= cx_chars < visible_cols and hasattr(_display, "fill_rect"):
        cx = cx_chars * INPUT_CHAR_W
        cy = _INPUT_Y + INPUT_CHAR_H - 2
        _display.fill_rect(cx, cy, INPUT_CHAR_W, 2, COL_ACC)

    # --- メッセージ (エラー等) を入力行の 1 行上に表示 ---
    if message:
        msg = message[:COLS]
        _draw_text(0, _MESSAGE_Y, msg, COL_ACC)

    _show()


# ---- メインループ --------------------------------------------------------

def main():
    # 前回フリーズ等で残った大量メモリ (chrome_buf / framebuf / closure 等) を回収
    # ランチャ経由 exec(...) の前に動作した Python オブジェクトは sys.modules や
    # script_globals 由来でリークが残ることが実機で確認されている (2026-06-22)
    try:
        import gc as _gc
        _gc.collect()
    except Exception:
        pass
    try:
        _main_run()
    finally:
        # 例外パスでも dupterm を必ず復元 (py_run.py の input() が動くように)
        if _HW:
            try:
                import os as _os
                _os.dupterm(_DUPTERM_PREV[0])
            except Exception:
                pass


_DUPTERM_PREV = [None]


def _main_run():
    # ランチャ経由起動時のハードウェア・端末状態をクリーンアップ
    if _HW:
        # 1. dupterm を一時解除 (REPL とのキー入力競合を避ける)
        try:
            import os as _os
            _DUPTERM_PREV[0] = _os.dupterm(None)
        except Exception:
            pass
        # 2. Core1 auto-refresh を再有効化 (停止状態なら復帰)
        try:
            import picocalcdisplay
            picocalcdisplay.startAutoUpdate()
        except Exception:
            pass
        # 3. menu 残骸をクリア (snake.py 等他アプリ準拠)
        try:
            _display.fill(COL_BG)
            _display.show()
        except Exception:
            pass
    _load_config()
    _apply_theme(_config.get("theme", "default"))
    _maybe_load_chrome()           # chrome.bin があればレイアウトを更新 + 起動時に blit
    _redraw_chrome()
    if _HW:
        _show()                     # chrome 表示確定
    history = History()
    buf = ""
    cursor = 0          # buf 内のカーソル位置 (0 〜 len(buf))
    hist_idx = -1       # -1 = 編集中 (履歴閲覧モード外), 0 以上 = history.items のインデックス
    saved_buf = ""      # 履歴閲覧開始時の編集中バッファを退避
    saved_cursor = 0
    message = "Psephos  ENTER=eval  ESC=quit  type 'help' for keys"
    render(history, buf, cursor, message)

    def _load_hist(idx):
        # idx 番目の履歴を buf に読み込む
        return history.items[idx][0]

    while True:
        key = _read_key()

        # --- エスケープシーケンス (矢印, Home/End 等) ---
        if isinstance(key, tuple) and key[0] == "ESCSEQ":
            seq = key[1]
            if seq == b"[A":          # ↑: 古い履歴へ
                if history.items:
                    if hist_idx == -1:
                        saved_buf = buf
                        saved_cursor = cursor
                        hist_idx = len(history.items) - 1
                    elif hist_idx > 0:
                        hist_idx -= 1
                    buf = _load_hist(hist_idx)
                    cursor = len(buf)
                    render(history, buf, cursor, message)
            elif seq == b"[B":        # ↓: 新しい履歴 or 編集中バッファ復元
                if hist_idx != -1:
                    if hist_idx < len(history.items) - 1:
                        hist_idx += 1
                        buf = _load_hist(hist_idx)
                        cursor = len(buf)
                    else:
                        hist_idx = -1
                        buf = saved_buf
                        cursor = saved_cursor
                    render(history, buf, cursor, message)
            elif seq == b"[D":        # ←: カーソル左
                if cursor > 0:
                    cursor -= 1
                    render(history, buf, cursor, message)
            elif seq == b"[C":        # →: カーソル右
                if cursor < len(buf):
                    cursor += 1
                    render(history, buf, cursor, message)
            elif seq == b"[H":        # Home: 行頭
                if cursor != 0:
                    cursor = 0
                    render(history, buf, cursor, message)
            elif seq == b"[F":        # End: 行末
                if cursor != len(buf):
                    cursor = len(buf)
                    render(history, buf, cursor, message)
            # その他のシーケンス (Shift+矢印, Delete 等) は無視
            continue

        if key == KEY_ESC:
            _clear()
            _show()
            # dupterm 復元は main() の finally で処理
            return

        if key in (KEY_ENTER, KEY_ENTER2):
            expr = buf.strip()
            hist_idx = -1
            saved_buf = ""
            saved_cursor = 0
            if not expr:
                continue
            # --- 特殊コマンド: help / theme ---
            if expr == "help":
                # ヘルプ用に chrome_buf (51200 byte) を解放してメモリ確保
                _had_chrome = _free_chrome()
                try:
                    import gc as _gc
                    _gc.collect()
                except Exception:
                    pass
                try:
                    _show_help()
                except Exception as ex:
                    message = "Help err: " + str(ex)[:COLS - 10]
                # 終了後 chrome を再ロード
                if _had_chrome:
                    _maybe_load_chrome()
                _redraw_chrome()         # ヘルプ画面が画面全体を覆っていたので chrome を復元
                try:
                    import gc as _gc
                    _gc.collect()
                except Exception:
                    pass
                buf = ""
                cursor = 0
                # message は維持 (ヘルプ前の状態を画面に残す)
                render(history, buf, cursor, message)
                continue
            if expr == "theme":
                names = " ".join(sorted(_THEMES.keys()))
                cur = _config.get("theme", "default")
                message = "Themes: " + names + "  (now: " + cur + ")"
                buf = ""
                cursor = 0
                render(history, buf, cursor, message)
                continue
            if expr.startswith("theme "):
                name = expr[6:].strip()
                if _apply_theme(name):
                    _config["theme"] = name
                    _save_config()
                    _redraw_chrome()     # 新パレットで chrome を再描画
                    message = "Theme: " + name
                else:
                    message = "Unknown theme: " + name
                buf = ""
                cursor = 0
                render(history, buf, cursor, message)
                continue
            if expr == "clear":
                # y/n 確認プロンプトを出して履歴消去
                buf = ""
                cursor = 0
                message = "Clear history? (y/n)"
                render(history, buf, cursor, message)
                while True:
                    k = _read_key()
                    if isinstance(k, tuple):
                        continue                              # エスケープシーケンスは無視
                    if k in ("y", "Y"):
                        history.clear()
                        message = "History cleared"
                        break
                    if k in ("n", "N", KEY_ESC):
                        message = "Clear cancelled"
                        break
                    # それ以外は無視して継続
                render(history, buf, cursor, message)
                continue
            if expr == "cas" or expr.startswith("cas "):
                # 履歴の式を CAS で視覚表示
                ref = expr[4:].strip() if expr.startswith("cas ") else "ans"
                src = _cas_resolve_ref(ref, history)
                if src is None:
                    message = "Usage: cas ans[N]  (N=1..10)"
                else:
                    _show_cas(src)
                    _redraw_chrome()
                    message = ""
                buf = ""
                cursor = 0
                render(history, buf, cursor, message)
                continue
            # --- 通常評価 ---
            try:
                result = evaluate(expr)
                res_str = _format(result)
                history.add(expr, res_str)
                _show_big_calc(expr, res_str)        # 2x 全画面表示 → 任意キーで戻る
                _redraw_chrome()
                message = ""
            except NameError:
                # 未定義変数を含む式: 数値計算は不能だが symbolic 表示する
                history.add(expr, "")
                _show_big_calc(expr, None)            # res_str=None で symbolic-only
                _redraw_chrome()
                message = ""
            except ZeroDivisionError:
                message = "Error: division by zero"
            except Exception as ex:
                message = "Error: " + str(ex)[:COLS - 8]
            buf = ""
            cursor = 0
            render(history, buf, cursor, message)
            continue

        if key in (KEY_BACKSPACE, KEY_BACKSPACE2):
            if cursor > 0:
                buf = buf[:cursor - 1] + buf[cursor:]
                cursor -= 1
                render(history, buf, cursor, message)
            continue

        # 通常文字 (印字可能のみ受理) -- カーソル位置に挿入
        if isinstance(key, str) and len(key) == 1 and 32 <= ord(key) < 127:
            buf = buf[:cursor] + key + buf[cursor:]
            cursor += 1
            render(history, buf, cursor, message)


def _format(value):
    """結果を見やすい文字列に整形 (整数は小数点を出さない、有効桁は _config['precision'])。"""
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        p = _config.get("precision", 10)
        return ("{:." + str(p) + "g}").format(value)
    return str(value)


if __name__ == "__main__":
    main()
