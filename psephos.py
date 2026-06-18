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
INPUT_ROW = ROWS - 1
HISTORY_ROWS = ROWS - 2        # 最下行=入力, その上1行=区切り

# 4bit LUT 上の論理色 (VT100 LUT 既定: 0=黒, 15=白 を想定)
COL_BG = 0
COL_FG = 15
COL_DIM = 8
COL_ACC = 11   # アクセント (結果表示)

HISTORY_PATH = "/sd/psephos_history.txt"
HISTORY_MAX = 200              # メモリ保持上限 (PSRAM 余裕あるが安全側)


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


def _is_reserved_target(name):
    """代入の左辺として使用禁止の予約名か判定。"""
    if name in _NAMESPACE:
        return True
    if name == "ans":
        return True
    if name.startswith("ans") and name[3:] and name[3:].isdigit():
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


def render(history, buf, cursor, message=""):
    """履歴域 + 入力行を描画。cursor は buf 内のカーソル位置 (0 〜 len(buf))。"""
    if not _HW:
        # PC フォールバック: 端末に出力してロジックだけ確認
        for expr, res in history.items[-5:]:
            print("  {} = {}".format(expr, res))
        shown = buf[:cursor] + "|" + buf[cursor:]
        print("> " + shown + ("   [" + message + "]" if message else ""))
        return

    _clear()
    # --- 履歴域 (古い順に上から、最新が下に来るよう末尾を表示) ---
    visible = history.items[-HISTORY_ROWS:]
    row = 0
    for expr, res in visible:
        # 代入式 `x = 5` で結果も `5` のとき "x = 5 = 5" になるのを抑制
        if expr.endswith(" = " + res) or expr.endswith("=" + res):
            line = expr
        else:
            line = "{} = {}".format(expr, res)
        if len(line) > COLS:
            line = line[:COLS - 1] + "~"
        _draw_text(0, row * CHAR_H, line, COL_DIM)
        row += 1

    # --- 区切り線 ---
    sep_y = (ROWS - 2) * CHAR_H
    if hasattr(_display, "hline"):
        _display.hline(0, sep_y, SCREEN_W, COL_DIM)

    # --- 入力行 (長い場合はカーソル位置が見える形で末尾寄せ) ---
    prefix = "> "
    full = prefix + buf
    # 描画開始位置に対する buf 側の表示オフセット (左へ何文字スクロールしたか)
    shift = 0
    if len(full) > COLS:
        shift = len(full) - COLS
        prompt = full[shift:]
    else:
        prompt = full
    _draw_text(0, INPUT_ROW * CHAR_H, prompt, COL_FG)

    # --- カーソル下線 (アクセント色) ---
    cx_chars = len(prefix) + cursor - shift
    if 0 <= cx_chars < COLS and hasattr(_display, "fill_rect"):
        cx = cx_chars * CHAR_W
        cy = INPUT_ROW * CHAR_H + CHAR_H - 1
        _display.fill_rect(cx, cy, CHAR_W, 1, COL_ACC)

    # --- メッセージ (エラー等) を入力行の 1 行上に表示 ---
    if message:
        msg = message[:COLS]
        _draw_text(0, (ROWS - 2) * CHAR_H, msg, COL_ACC)

    _show()


# ---- メインループ --------------------------------------------------------

def main():
    history = History()
    buf = ""
    cursor = 0          # buf 内のカーソル位置 (0 〜 len(buf))
    hist_idx = -1       # -1 = 編集中 (履歴閲覧モード外), 0 以上 = history.items のインデックス
    saved_buf = ""      # 履歴閲覧開始時の編集中バッファを退避
    saved_cursor = 0
    message = "Psephos  ENTER=eval  ESC=quit  Up/Dn=history"
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
    """結果を見やすい文字列に整形 (整数は小数点を出さない)。"""
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return "{:.10g}".format(value)
    return str(value)


if __name__ == "__main__":
    main()
