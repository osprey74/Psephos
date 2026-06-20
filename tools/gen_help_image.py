"""gen_help_image.py -- Psephos のヘルプ画像 (日本語) を生成する.

PicoCalc の 320x320 ディスプレイ向け、framebuf.MONO_HMSB 形式の生バイナリを出力。
1 pixel = 1 bit、横方向 MSB 先頭で 8 pixel/byte パッキング (320*320/8 = 12,800 byte)。

実行: py -3.12 tools/gen_help_image.py
依存: Pillow  (pip install pillow)
出力: assets/help_ja.bin

実機側 (psephos.py _show_help) は次の手順で表示する:
  1. /sd/psephos_help.bin を 12800 byte 読み込み
  2. framebuf.FrameBuffer(buf, 320, 320, framebuf.MONO_HMSB) で wrap
  3. 1bit -> 4bit パレット (COL_BG / COL_FG) を blit() の引数で渡す
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 320, 320
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "help_ja.bin"

# Windows 標準の Noto Sans JP (variable font)
FONT_PATH = r"C:\Windows\Fonts\NotoSansJP-VF.ttf"
FONT_SIZE = 12
LINE_H = 13
MARGIN = 3

# 全行を Noto Sans JP で描画。22 行制限内に収まるよう圧縮。
HELP_LINES = [
    "Psephos -- 関数電卓 ヘルプ",
    "──────────────────────────────",
    "[関数]",
    " 三角     sin cos tan asin acos atan atan2",
    " 指数対数 exp log log10 sqrt pow",
    " 端数     floor ceil fabs abs round",
    " 角度     radians degrees",
    " 進数     hex bin oct int float",
    " 定数     pi e tau     汎用 min max",
    "[直近結果] ans = 最新、ans2..ans10 = N 計算前",
    "[変数]     x = 3   (セッション中のみ保持)",
    "[リテラル] 1.5e-10  0xFF  0b101  0o777",
    "[キー]",
    " ↑↓ 履歴呼び出し/復元    ←→ カーソル移動",
    " Home/End 行頭/末   BS 前文字削除",
    " Enter 評価          ESC Psephos 終了",
    "[コマンド]",
    " help          このヘルプ画面",
    " theme         テーマ一覧表示",
    " theme <名前>  default/amber/green/cyan/invert",
    "",
    "任意のキーで戻る",
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("1", (W, H), 0)   # 1bit, 0=BG, 1=FG
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

    for i, line in enumerate(HELP_LINES):
        y = MARGIN + i * LINE_H
        if y + FONT_SIZE > H - MARGIN:
            print(f"WARNING: line {i} 以降は画面外 ({line!r})")
            break
        draw.text((MARGIN, y), line, font=font, fill=1)

    # Pillow の "1" モード tobytes は MSB-first パッキング (bit 7 = 左端) だが、
    # 実機 (LofiFren MicroPython 1.25 on Pico 2W) で framebuf.MONO_HMSB を宣言した
    # FrameBuffer は実質 LSB-first 動作する (bit 0 = 左端) ことを実機検証で確認した
    # (2026-06-18)。ドキュメントとは挙動が異なる。
    # よって出力前に各バイトのビットを逆転させる必要がある。
    raw = img.tobytes()
    # 256 値の逆転テーブルを作って高速変換
    rev_table = bytes(int("{:08b}".format(b)[::-1], 2) for b in range(256))
    data = raw.translate(rev_table)
    OUT.write_bytes(data)
    print(f"Wrote {OUT}: {len(data)} bytes (bit-reversed for MicroPython MONO_HMSB)")

    # デバッグ用に PNG も書き出すと修正サイクルが早い
    debug_png = OUT.with_suffix(".png")
    img.save(debug_png)
    print(f"Wrote {debug_png} (debug preview)")


if __name__ == "__main__":
    main()
