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
        # 定数
        "pi": math.pi, "e": math.e, "tau": getattr(math, "tau", 2 * math.pi),
        # ユーティリティ
        "min": min, "max": max,
    }
    return ns


_NAMESPACE = _build_namespace()
_ANS = 0.0   # 直前の計算結果 (ans で参照可能)


def evaluate(expr):
    """式文字列を評価して結果を返す。例外は呼び出し側で処理。"""
    global _ANS
    local = dict(_NAMESPACE)
    local["ans"] = _ANS
    # __builtins__ を空にして任意コード実行を防止
    result = eval(expr, {"__builtins__": {}}, local)
    _ANS = result
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

# 特殊キーコード (VT100 端末由来。実機で要確認・調整)
KEY_ENTER = "\n"
KEY_ENTER2 = "\r"
KEY_BACKSPACE = "\x08"
KEY_BACKSPACE2 = "\x7f"
KEY_ESC = "\x1b"


def _read_key():
    """1 文字(またはエスケープシーケンス)を返すブロッキング入力。
    実機キーボード API が異なる場合はこの関数のみ差し替える。"""
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        # 矢印キー等のエスケープシーケンスを読み飛ばす簡易処理
        seq = sys.stdin.read(2)
        return ("ESCSEQ", seq)
    return ch


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


def render(history, buf, message=""):
    """履歴域 + 入力行を描画。"""
    if not _HW:
        # PC フォールバック: 端末に出力してロジックだけ確認
        for expr, res in history.items[-5:]:
            print("  {} = {}".format(expr, res))
        print("> " + buf + ("   [" + message + "]" if message else ""))
        return

    _clear()
    # --- 履歴域 (古い順に上から、最新が下に来るよう末尾を表示) ---
    visible = history.items[-HISTORY_ROWS:]
    row = 0
    for expr, res in visible:
        line = "{} = {}".format(expr, res)
        if len(line) > COLS:
            line = line[:COLS - 1] + "~"
        _draw_text(0, row * CHAR_H, line, COL_DIM)
        row += 1

    # --- 区切り線 ---
    sep_y = (ROWS - 2) * CHAR_H
    if hasattr(_display, "hline"):
        _display.hline(0, sep_y, SCREEN_W, COL_DIM)

    # --- 入力行 ---
    prompt = "> " + buf
    if len(prompt) > COLS:
        prompt = prompt[-(COLS):]
    _draw_text(0, INPUT_ROW * CHAR_H, prompt, COL_FG)

    # --- メッセージ (エラー等) を入力行右側 or 履歴最下に重ねる ---
    if message:
        msg = message[:COLS]
        _draw_text(0, (ROWS - 2) * CHAR_H, msg, COL_ACC)

    _show()


# ---- メインループ --------------------------------------------------------

def main():
    history = History()
    buf = ""
    message = "Psephos  ENTER=calc  ESC=quit"
    render(history, buf, message)

    while True:
        key = _read_key()

        # エスケープシーケンス (矢印キー等) -- 最小実装では無視
        if isinstance(key, tuple):
            continue

        if key == KEY_ESC:
            _clear()
            _show()
            return

        if key in (KEY_ENTER, KEY_ENTER2):
            expr = buf.strip()
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
            render(history, buf, message)
            continue

        if key in (KEY_BACKSPACE, KEY_BACKSPACE2):
            buf = buf[:-1]
            render(history, buf, message)
            continue

        # 通常文字 (印字可能のみ受理)
        if isinstance(key, str) and len(key) == 1 and 32 <= ord(key) < 127:
            buf += key
            render(history, buf, message)


def _format(value):
    """結果を見やすい文字列に整形 (整数は小数点を出さない)。"""
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return "{:.10g}".format(value)
    return str(value)


if __name__ == "__main__":
    main()
