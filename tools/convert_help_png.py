"""convert_help_png.py -- Psephos のセマンティック 4 色 PNG を GS4_HMSB バイナリへ変換する.

入力: 320x320 px の PNG（セマンティック 4 色: BG / FG / DIM / ACC）
出力: 320x320x4bit = 51,200 byte の GS4_HMSB バイナリ。各ピクセル値は
      0=BG, 1=FG, 2=DIM, 3=ACC (実機側でパレット経由でテーマ色に展開)。

`INPUTS` に列挙した PNG を一括変換する。ファイル未存在は警告のみで継続。

実行: py -3.12 tools/convert_help_png.py
依存: Pillow

実機側のパッキング規約 (実機検証 2026-06-18):
- GS4_HMSB は **仕様通り** high nibble first (左ピクセル = high nibble)
- MONO_HMSB のような LSB-first quirk は GS4 では発生しない
"""
from pathlib import Path
from PIL import Image

W, H = 320, 320
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# 変換対象ファイルのリスト。chrome.png は総司様が Claude Design で作成中、
# 未存在の場合は警告のみで継続する。
INPUTS = [
    "help_ja_p1.png",
    "help_ja_p2.png",
    "chrome.png",
]

# セマンティック 4 色 (RGB) -> インデックス (0..3)
COLOR_TO_INDEX = {
    (0, 0, 0):       0,    # BG
    (255, 255, 255): 1,    # FG
    (128, 128, 128): 2,    # DIM
    (255, 200, 0):   3,    # ACC
}


def png_to_index_array(path):
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
                idx = _nearest_index(rgb)
                misses += 1
            row.append(idx)
        indices.append(row)
    if misses:
        print(f"  WARNING: {misses} px が 4 色以外、最近色へスナップしました")
    return indices


def _nearest_index(rgb):
    best_idx = 0
    best_d = float("inf")
    for color, idx in COLOR_TO_INDEX.items():
        d = sum((a - b) ** 2 for a, b in zip(color, rgb))
        if d < best_d:
            best_d = d
            best_idx = idx
    return best_idx


def pack_gs4(indices):
    """index 配列を GS4_HMSB バイト列にパック。
    1 byte = 2 pixel, high nibble が左ピクセル (仕様通り)。"""
    buf = bytearray(W * H // 2)
    for y in range(H):
        for x in range(0, W, 2):
            left = indices[y][x] & 0x0F
            right = indices[y][x + 1] & 0x0F
            buf[y * (W // 2) + x // 2] = (left << 4) | right
    return bytes(buf)


def convert_one(name):
    path = ASSETS / name
    if not path.exists():
        print(f"  SKIP {name} (missing)")
        return False
    print(f"Reading {name}")
    indices = png_to_index_array(path)
    data = pack_gs4(indices)
    out_path = ASSETS / (path.stem + ".bin")
    out_path.write_bytes(data)
    print(f"  Wrote {out_path.name}: {len(data)} bytes")
    return True


def main():
    converted = 0
    for name in INPUTS:
        if convert_one(name):
            converted += 1
    print(f"\n{converted} / {len(INPUTS)} files converted.")


if __name__ == "__main__":
    main()
