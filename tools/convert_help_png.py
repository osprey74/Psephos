"""convert_help_png.py -- Psephos の 2 色 / 4 色 PNG を GS4_HMSB バイナリへ変換する.

入力: 320x320 px の PNG（セマンティック 4 色: BG / FG / DIM / ACC）
出力: 320x320x4bit = 51,200 byte の GS4_HMSB バイナリ。各ピクセル値は
      0=BG, 1=FG, 2=DIM, 3=ACC (実機側でパレット経由でテーマ色に展開)。

実行: py -3.12 tools/convert_help_png.py
依存: Pillow

実機側のパッキング規約:
- GS4_HMSB 仕様上は 1 byte = 2 pixels、high nibble が左ピクセル
- ただし LofiFren MicroPython 1.25 on Pico 2W は MONO_HMSB が LSB-first 動作する
  バグがあったため、GS4 でも同様の可能性あり。両方の packing を出力できるよう
  オプション NIBBLE_ORDER で切替可能 ("high" / "low")。
"""
from pathlib import Path
from PIL import Image

W, H = 320, 320
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
INPUTS = ["help_ja_p1.png", "help_ja_p2.png"]

# セマンティック 4 色 (RGB) -> インデックス (0..3)
COLOR_TO_INDEX = {
    (0, 0, 0):       0,    # BG
    (255, 255, 255): 1,    # FG
    (128, 128, 128): 2,    # DIM
    (255, 200, 0):   3,    # ACC
}

# 実機 framebuf のニブル順。"high" = 仕様通り、"low" = MONO で観測した LSB-first 動作と同様
# 実機で正しく表示されるまで両方試す。初回は "high" (仕様) を試行。
NIBBLE_ORDER = "high"


def png_to_index_array(path: Path) -> list[list[int]]:
    """PNG を 2 次元の index 配列 [y][x] に変換 (値は 0..3)。"""
    img = Image.open(path).convert("RGB")
    if img.size != (W, H):
        raise ValueError(f"{path.name}: 期待サイズ ({W},{H}) と異なる: {img.size}")
    pixels = list(img.getdata())
    indices = []
    misses = 0
    for y in range(H):
        row = []
        for x in range(W):
            rgb = pixels[y * W + x]
            idx = COLOR_TO_INDEX.get(rgb)
            if idx is None:
                # 完全一致しない場合は最も近い 4 色へスナップ
                idx = nearest_index(rgb)
                misses += 1
            row.append(idx)
        indices.append(row)
    if misses:
        print(f"  WARNING: {misses} px が 4 色以外、最近色へスナップしました")
    return indices


def nearest_index(rgb):
    best_idx = 0
    best_d = float("inf")
    for color, idx in COLOR_TO_INDEX.items():
        d = sum((a - b) ** 2 for a, b in zip(color, rgb))
        if d < best_d:
            best_d = d
            best_idx = idx
    return best_idx


def pack_gs4(indices: list[list[int]], nibble_order: str = "high") -> bytes:
    """index 配列を GS4_HMSB バイト列にパック。"""
    buf = bytearray(W * H // 2)
    for y in range(H):
        for x in range(0, W, 2):
            left = indices[y][x] & 0x0F
            right = indices[y][x + 1] & 0x0F
            if nibble_order == "high":
                # 仕様通り: high nibble = pixel 0 (左)
                b = (left << 4) | right
            else:
                # 反転: low nibble = pixel 0 (左)
                b = (right << 4) | left
            buf[y * (W // 2) + x // 2] = b
    return bytes(buf)


def main():
    print(f"NIBBLE_ORDER = {NIBBLE_ORDER}")
    for name in INPUTS:
        path = ASSETS / name
        if not path.exists():
            print(f"  SKIP {name} (missing)")
            continue
        print(f"Reading {name}")
        indices = png_to_index_array(path)
        data = pack_gs4(indices, NIBBLE_ORDER)
        out_name = path.stem + ".bin"
        out_path = ASSETS / out_name
        out_path.write_bytes(data)
        print(f"  Wrote {out_name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
