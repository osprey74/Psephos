"""Psephos レイアウト プレビュー — Pattern 1 = Terminus 12x24、
Pattern 2 = Terminus 8x16、Pattern 3 = Terminus 16x32 を実画面に配置して
全体感を確認するためのモックアプリ。

→/← で 3 種の画面（通常表示 / 大きな結果 / ヘルプ風）切替、ESC で終了。
"""

import sys
import gc
import time
import picocalc

SCREEN_W = 320
SCREEN_H = 320
COL_BG = 0
COL_FG = 15
COL_DIM = 8
COL_ACC = 11


def _import_font(name):
    if name in sys.modules:
        del sys.modules[name]
    gc.collect()
    mod = __import__(name)
    return mod.FONT, mod.W, mod.H


def _release_font(name):
    if name in sys.modules:
        del sys.modules[name]
    gc.collect()


def _draw_char(disp, x, y, code, color, font, w, h):
    if code < 0x20 or code > 0x7E:
        return
    data = font[code - 0x20]
    bpr = (w + 7) // 8
    for row in range(h):
        for col in range(w):
            if data[row * bpr + (col >> 3)] & (0x80 >> (col & 7)):
                disp.pixel(x + col, y + row, color)


def _draw_text(disp, x, y, s, color, font, w, h):
    cx = x
    for ch in s:
        if cx + w > SCREEN_W:
            break
        _draw_char(disp, cx, y, ord(ch), color, font, w, h)
        cx += w


def _read_key_blocking():
    buf = bytearray(8)
    while True:
        try:
            n = picocalc.terminal.readinto(buf)
        except OSError:
            n = None
        if n:
            if n >= 2 and buf[0] == 0x1b and buf[1] == 0x1b:
                return ("ESC", None)
            if buf[0] == 0x1b:
                return ("SEQ", bytes(buf[1:n]))
            return ("CHR", chr(buf[0]))
        time.sleep_ms(10)


# --- ページ 1: 通常表示（chrome + 履歴 + 入力欄） ---

def page_normal(disp):
    # Top chrome (Pattern 1: Terminus 12x24)
    font12, w12, h12 = _import_font("terminus_12x24")
    disp.hline(0, 0, SCREEN_W, COL_ACC)
    _draw_text(disp, 4, 2, "PSEPHOS-prog sci calc", COL_ACC, font12, w12, h12)
    disp.hline(0, 27, SCREEN_W, COL_ACC)
    _release_font("terminus_12x24")

    # History (Pattern 2: Terminus 8x16)
    font8, w8, h8 = _import_font("terminus_8x16")
    history_samples = [
        ("1+1", "2"),
        ("sin(pi/4)", "0.7071067812"),
        ("sqrt(2)+sqrt(3)", "3.146264369"),
        ("e**2", "7.389056099"),
        ("log(100)", "4.605170186"),
        ("(3+4)*5/7", "5"),
    ]
    y = 32
    for expr, res in history_samples:
        line = "{} = {}".format(expr, res)
        if len(line) > 40:
            line = line[:39] + "~"
        _draw_text(disp, 4, y, line, COL_DIM, font8, w8, h8)
        y += h8 + 2
    _release_font("terminus_8x16")

    # Separator above input
    disp.hline(0, SCREEN_H - 36, SCREEN_W, COL_DIM)

    # Input row (Pattern 1: Terminus 12x24)
    font12, w12, h12 = _import_font("terminus_12x24")
    _draw_text(disp, 0, SCREEN_H - 30, "> alpha*omega+pi/4", COL_FG, font12, w12, h12)
    # cursor
    disp.fill_rect(18 * w12, SCREEN_H - 4, w12, 2, COL_ACC)
    _release_font("terminus_12x24")


# --- ページ 2: 大きな結果表示（CAS + big_calc 兼用） ---

def page_big_calc(disp):
    # Top chrome
    font12, w12, h12 = _import_font("terminus_12x24")
    disp.hline(0, 0, SCREEN_W, COL_ACC)
    _draw_text(disp, 4, 2, "PSEPHOS-prog sci calc", COL_ACC, font12, w12, h12)
    disp.hline(0, 27, SCREEN_W, COL_ACC)

    # Big calc: 入力式 (Terminus 12x24)
    _draw_text(disp, 4, 70, "sqrt(50)+e^2", COL_FG, font12, w12, h12)
    _release_font("terminus_12x24")

    # 結果 (Pattern 3: Terminus 16x32)
    font16, w16, h16 = _import_font("terminus_16x32")
    _draw_text(disp, 4, 130, "= 14.46580", COL_ACC, font16, w16, h16)
    _release_font("terminus_16x32")

    # CAS 表示 (Terminus 12x24 + 8x16 指数)
    font12, w12, h12 = _import_font("terminus_12x24")
    # "x^2 + 3x + " (12x24)
    _draw_text(disp, 4, 200, "x", COL_FG, font12, w12, h12)
    _release_font("terminus_12x24")
    # 指数 "2" (8x16)
    font8, w8, h8 = _import_font("terminus_8x16")
    _draw_text(disp, 4 + w12, 200, "2", COL_FG, font8, w8, h8)
    _release_font("terminus_8x16")
    font12, w12, h12 = _import_font("terminus_12x24")
    _draw_text(disp, 4 + w12 + w8 + 4, 200, "+ 3x + 1", COL_FG, font12, w12, h12)

    # 下端 chrome line
    disp.hline(0, SCREEN_H - 4, SCREEN_W, COL_ACC)
    _release_font("terminus_12x24")


# --- ページ 3: ヘルプ風 (多くの行を 8x16 で詰めて表示) ---

def page_help(disp):
    font12, w12, h12 = _import_font("terminus_12x24")
    disp.hline(0, 0, SCREEN_W, COL_ACC)
    _draw_text(disp, 4, 2, "Help (Pattern 2 dense)", COL_ACC, font12, w12, h12)
    disp.hline(0, 27, SCREEN_W, COL_ACC)
    _release_font("terminus_12x24")

    font8, w8, h8 = _import_font("terminus_8x16")
    help_lines = [
        "Functions:",
        "  sin cos tan asin acos atan",
        "  exp log log10 sqrt pow",
        "  floor ceil abs round",
        "  radians degrees",
        "Constants: pi e tau",
        "Variables: x = 3 (session only)",
        "Commands: help theme clear cas",
        "Keys: Enter=eval ESC=quit",
        "      Up/Down=history",
        "      Left/Right=cursor",
    ]
    y = 36
    for line in help_lines:
        _draw_text(disp, 4, y, line, COL_FG, font8, w8, h8)
        y += h8 + 1
    _release_font("terminus_8x16")
    disp.hline(0, SCREEN_H - 4, SCREEN_W, COL_ACC)


PAGES = [
    ("Normal screen", page_normal),
    ("Big calc result", page_big_calc),
    ("Help (dense)", page_help),
]


def main():
    disp = picocalc.display
    import os as _os
    prev = None
    try:
        prev = _os.dupterm(None)
    except Exception:
        pass
    try:
        import picocalcdisplay
        picocalcdisplay.startAutoUpdate()
    except Exception:
        pass

    try:
        page_idx = 0
        while True:
            gc.collect()
            disp.fill(COL_BG)
            label, fn = PAGES[page_idx]
            fn(disp)
            # Page indicator (small)
            font5 = None
            try:
                font5, w5, h5 = _import_font("terminus_8x16")
                pg_text = "<- {}/{} ->".format(page_idx + 1, len(PAGES))
                _draw_text(disp, SCREEN_W - len(pg_text) * w5 - 4,
                           SCREEN_H - h5 - 6, pg_text, COL_DIM, font5, w5, h5)
                _release_font("terminus_8x16")
            except Exception:
                pass

            disp.show()
            kt, val = _read_key_blocking()
            if kt == "ESC":
                break
            if kt == "SEQ":
                if val == b"[C" and page_idx < len(PAGES) - 1:
                    page_idx += 1
                elif val == b"[D" and page_idx > 0:
                    page_idx -= 1
    finally:
        if prev is not None:
            try:
                _os.dupterm(prev)
            except Exception:
                pass
        disp.fill(COL_BG)
        disp.show()


if __name__ == "__main__":
    main()
