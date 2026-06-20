# psephos.py -- 関数電卓 (PicoCalc / Raspberry Pi Pico 2W / MicroPython)
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
_ACTIVE_TOP = 0                              # 動的領域開始 y (px)
_ACTIVE_BOTTOM = SCREEN_H                    # 動的領域終了 y (px, exclusive)
_HISTORY_Y0 = 0                              # 履歴域開始 y (px)
_HISTORY_ROWS = ROWS - 2                     # 履歴域行数 (CHAR_H 単位)
_MESSAGE_Y = (ROWS - 2) * CHAR_H             # メッセージ行 y (px)
_INPUT_Y = (ROWS - 1) * CHAR_H               # 入力行 y (px)

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
CHROME_TOP_DEFAULT_H = 16                    # chrome 上部の高さ (px)
CHROME_BOTTOM_DEFAULT_H = 8                  # chrome 下部の高さ (px)
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


_COMMANDS = ("help", "theme")  # main() で特殊コマンドとして処理する名前


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
    """安全でない式を ValueError で弾く。

    MicroPython の eval は {"__builtins__": {}} を渡しても組み込み関数を
    遮断しない (CPython と挙動が異なり、実機で確認済 2026-06-18)。
    そこで字句レベルで識別子をホワイトリスト方式で検査する追加防御を行う:
      1. "__" を含む式は拒否 (dunder 経由のリフレクション攻撃を遮断)
      2. _NAMESPACE / "ans" / "ans<N>" / ユーザ変数以外の識別子を拒否
    """
    if "__" in expr:
        raise ValueError("disallowed: __ in expr")
    for ident in _extract_names(expr):
        if not _is_allowed_name(ident):
            raise ValueError("disallowed name: " + ident)


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

    実機 (PicoCalc): picocalc.keyboard.readinto(buf) は非ブロッキング。
    キーは 1 回の呼び出しで完全な形で返る (実機検証済 2026-06-18):
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
            n = picocalc.keyboard.readinto(buf)
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
    active_rows = (_ACTIVE_BOTTOM - _ACTIVE_TOP) // CHAR_H
    _HISTORY_ROWS = active_rows - 2          # 末尾 2 行をメッセージと入力に
    _INPUT_Y = _ACTIVE_BOTTOM - CHAR_H
    _MESSAGE_Y = _INPUT_Y - CHAR_H


def _redraw_chrome():
    """Chrome 画像を blit (theme 変更後・help 終了後の復元に使う)。"""
    if _chrome_buf is None or not _HW:
        return
    palette = _build_help_palette()
    try:
        _display.blit(_chrome_buf[1], 0, 0, -1, palette)
    except TypeError:
        _display.blit(_chrome_buf[1], 0, 0)


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


def _load_help_pages():
    """SD から全ヘルプページの GS4 バイナリを読み込んで FrameBuffer のリストを返す。
    全ページが読めない場合は None。"""
    try:
        import framebuf
    except ImportError:
        return None
    pages = []
    for path in HELP_PAGE_PATHS:
        buf = bytearray(HELP_PAGE_BYTES)
        try:
            with open(path, "rb") as f:
                n = f.readinto(buf)
            if n != HELP_PAGE_BYTES:
                return None
        except OSError:
            return None
        pages.append((buf, framebuf.FrameBuffer(buf, SCREEN_W, SCREEN_H, framebuf.GS4_HMSB)))
    return pages


def _show_help():
    """ヘルプ画面を表示し、任意キーで戻る。

    GS4 画像ファイル (HELP_PAGE_PATHS) がすべて揃っていればセマンティック 4 色画像を
    現在のテーマカラーパレット経由で blit する。← / → でページ送り、それ以外のキーで戻る。
    画像が無ければ内蔵テキストヘルプ (_HELP_LINES) を 6x8 フォントで描画する。
    """
    if not _HW:
        for line in _HELP_LINES:
            print(line)
        return

    pages = _load_help_pages()
    if pages is None:
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

    palette = _build_help_palette()
    idx = 0
    n_pages = len(pages)

    def _draw():
        _clear()
        _display.blit(pages[idx][1], 0, 0, -1, palette)
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
            # 端でのカーソル無視、他のエスケープシーケンスは無視
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

    # --- 履歴域 (古い順に上から、最新が下に来るよう末尾を表示) ---
    visible = history.items[-_HISTORY_ROWS:]
    for row, (expr, res) in enumerate(visible):
        # 代入式 `x = 5` で結果も `5` のとき "x = 5 = 5" になるのを抑制
        if expr.endswith(" = " + res) or expr.endswith("=" + res):
            line = expr
        else:
            line = "{} = {}".format(expr, res)
        if len(line) > COLS:
            line = line[:COLS - 1] + "~"
        _draw_text(0, _HISTORY_Y0 + row * CHAR_H, line, COL_DIM)

    # --- 区切り線 (メッセージ行と同じ y) ---
    if hasattr(_display, "hline"):
        _display.hline(0, _MESSAGE_Y, SCREEN_W, COL_DIM)

    # --- 入力行 (長い場合はカーソル位置が見える形で末尾寄せ) ---
    prefix = "> "
    full = prefix + buf
    shift = 0
    if len(full) > COLS:
        shift = len(full) - COLS
        prompt = full[shift:]
    else:
        prompt = full
    _draw_text(0, _INPUT_Y, prompt, COL_FG)

    # --- カーソル下線 (アクセント色) ---
    cx_chars = len(prefix) + cursor - shift
    if 0 <= cx_chars < COLS and hasattr(_display, "fill_rect"):
        cx = cx_chars * CHAR_W
        cy = _INPUT_Y + CHAR_H - 1
        _display.fill_rect(cx, cy, CHAR_W, 1, COL_ACC)

    # --- メッセージ (エラー等) を入力行の 1 行上に表示 ---
    if message:
        msg = message[:COLS]
        _draw_text(0, _MESSAGE_Y, msg, COL_ACC)

    _show()


# ---- メインループ --------------------------------------------------------

def main():
    _load_config()
    _apply_theme(_config.get("theme", "default"))
    _maybe_load_chrome()           # chrome.bin があればレイアウトを更新 + 起動時に blit
    _redraw_chrome()
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
                _show_help()
                _redraw_chrome()         # ヘルプ画面が画面全体を覆っていたので chrome を復元
                buf = ""
                cursor = 0
                message = ""
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
            # --- 通常評価 ---
            try:
                result = evaluate(expr)
                res_str = _format(result)
                history.add(expr, res_str)
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
