"""Psephos font sampler — Spleen vs Terminus 各サイズの実機表示比較。
ランチャから起動。→/← でページ切替、ESC で終了。
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


def _draw_char(disp, x, y, code, color, font, w, h):
    if code < 0x20 or code > 0x7E:
        return
    data = font[code - 0x20]
    bytes_per_row = (w + 7) // 8
    for row in range(h):
        for col in range(w):
            byte = data[row * bytes_per_row + (col >> 3)]
            if byte & (0x80 >> (col & 7)):
                disp.pixel(x + col, y + row, color)


def _draw_text(disp, x, y, s, color, font, w, h):
    cur_x = x
    for ch in s:
        if cur_x + w > SCREEN_W:
            break
        _draw_char(disp, cur_x, y, ord(ch), color, font, w, h)
        cur_x += w


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


SAMPLE1 = "abcdef ABCDEF 01234 !?+*/="
SAMPLE2 = "(x+1)/2 sin(t) y=mx+b"

PAGES = [
    [("spleen_5x8", "Spleen 5x8"), ("terminus_6x12", "Terminus 6x12")],
    [("spleen_6x12", "Spleen 6x12"), ("terminus_6x12", "Terminus 6x12")],
    [("spleen_8x16", "Spleen 8x16"), ("terminus_8x16", "Terminus 8x16")],
    [("spleen_12x24", "Spleen 12x24"), ("terminus_12x24", "Terminus 12x24")],
    [("spleen_16x32", "Spleen 16x32"), ("terminus_16x32", "Terminus 16x32")],
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
            page = PAGES[page_idx]
            hdr = "Font sampler {}/{}  <- -> :page  ESC:exit".format(page_idx + 1, len(PAGES))
            disp.text(hdr, 4, 4, COL_DIM)

            y = 20
            for font_mod_name, label in page:
                gc.collect()
                # Force fresh import (so memory of previous font is reclaimable)
                if font_mod_name in sys.modules:
                    del sys.modules[font_mod_name]
                try:
                    mod = __import__(font_mod_name)
                except Exception as e:
                    disp.text("import err: " + str(e)[:30], 4, y, COL_ACC)
                    y += 16
                    continue
                font = mod.FONT
                w = mod.W
                h = mod.H
                disp.text(label + " ({}x{})".format(w, h), 4, y, COL_ACC)
                y += 10
                _draw_text(disp, 4, y, SAMPLE1, COL_FG, font, w, h)
                y += h + 2
                _draw_text(disp, 4, y, SAMPLE2, COL_FG, font, w, h)
                y += h + 8
                # release
                del mod
                del sys.modules[font_mod_name]
                gc.collect()

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
