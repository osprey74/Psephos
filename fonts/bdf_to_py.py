"""BDF → Python bitmap データ変換器。
ASCII 範囲 (0x20..0x7E) の 95 文字を MSB-first パック形式で出力する。

各文字のデータ: bytes(rows_per_char * bytes_per_row) で、
ピクセル (x, y) は data[y * bytes_per_row + (x >> 3)] & (0x80 >> (x & 7))。

出力例:
    _SPLEEN_8X16 = (
        b'\\x00\\x00\\x18\\x18...',  # ' '
        ...
    )
    _SPLEEN_8X16_W = 8
    _SPLEEN_8X16_H = 16
"""
import sys


def parse_bdf(path):
    """BDF を読み込み {codepoint: (bbx_w, bbx_h, xoff, yoff, [hex_row, ...])}, font_w, font_h を返す。"""
    chars = {}
    with open(path) as f:
        lines = f.readlines()

    font_w = font_h = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("FONTBOUNDINGBOX"):
            parts = line.split()
            font_w = int(parts[1])
            font_h = int(parts[2])
        elif line.startswith("STARTCHAR"):
            # Parse a char block
            enc = None
            bbx = None
            bitmap = []
            in_bitmap = False
            while i < len(lines) and not lines[i].strip().startswith("ENDCHAR"):
                ln = lines[i].strip()
                if ln.startswith("ENCODING"):
                    enc = int(ln.split()[1])
                elif ln.startswith("BBX"):
                    parts = ln.split()
                    bbx = (int(parts[1]), int(parts[2]),
                           int(parts[3]), int(parts[4]))
                elif ln == "BITMAP":
                    in_bitmap = True
                elif in_bitmap:
                    bitmap.append(ln)
                i += 1
            if enc is not None and bbx is not None:
                chars[enc] = (bbx, bitmap)
        i += 1

    return chars, font_w, font_h


def render_to_canvas(bbx, bitmap, font_w, font_h, baseline_offset):
    """個別文字の BBX 範囲を、フォント全体の W×H キャンバス内に正しい位置で配置する。
    BDF の (xoff, yoff) は char の origin (baseline 左下) を基準にしたオフセット。
    返り値: rows のリスト、各 row は font_w ビットの整数 (MSB=col0)。"""
    bbx_w, bbx_h, xoff, yoff = bbx
    # キャンバスは font_w × font_h、左上原点
    canvas = [0] * font_h

    # BDF の baseline は char box の下端から上に yoff だけ上の位置。
    # 単純化のため: font の bottom row を baseline = font_h - 1 と仮定し、
    # baseline_offset で descent を吸収。
    # 通常は yoff は負（descender 用）または 0。
    # char の y_top（キャンバス上の上端 row）= font_h - baseline_offset - bbx_h - yoff
    # 各 row は bitmap[i] (hex)、これは bbx_w ビットの MSB-first。
    y_top = font_h - baseline_offset - bbx_h - yoff
    for i, hex_row in enumerate(bitmap):
        # bitmap row を整数化（左詰め）
        nbytes = (bbx_w + 7) // 8
        val = int(hex_row, 16)
        # val は (nbytes*8) ビット、MSB から bbx_w ビットが有効
        # font_w ビットの canvas 行に xoff だけシフトして配置
        # 注意: xoff は char box の左下を基準にした x オフセット
        # canvas の x=0 は font の左端なので、val を xoff だけ右にシフト
        shift_left_in_canvas = xoff
        # val を bbx_w ビットの整数として正規化
        row_val = val >> (nbytes * 8 - bbx_w)
        # font_w ビット幅の中で row_val を xoff の位置に配置
        positioned = row_val << (font_w - bbx_w - shift_left_in_canvas)
        canvas_y = y_top + i
        if 0 <= canvas_y < font_h:
            canvas[canvas_y] |= positioned
    return canvas


def pack_canvas(canvas, font_w):
    """canvas (rows of int) を bytes に MSB-first でパック。"""
    bytes_per_row = (font_w + 7) // 8
    out = bytearray()
    for row in canvas:
        for byte_idx in range(bytes_per_row):
            shift = font_w - 8 - byte_idx * 8
            if shift >= 0:
                b = (row >> shift) & 0xFF
            else:
                b = (row << (-shift)) & 0xFF
            out.append(b)
    return bytes(out)


def convert(bdf_path, font_name, baseline_offset=0):
    """BDF を Python module ソースに変換して文字列を返す。"""
    chars, font_w, font_h = parse_bdf(bdf_path)
    print(f"Parsed {bdf_path}: {len(chars)} chars, font {font_w}x{font_h}", file=sys.stderr)

    lines = []
    lines.append(f"# Auto-generated from {bdf_path.split('/')[-1]}")
    lines.append(f"# Font: {font_name}")
    lines.append(f"# Size: {font_w}x{font_h}, {len(chars)} chars in BDF")
    lines.append("")
    lines.append(f"_{font_name}_W = {font_w}")
    lines.append(f"_{font_name}_H = {font_h}")
    lines.append(f"_{font_name} = (")

    for code in range(0x20, 0x7F):
        if code not in chars:
            # blank
            data = bytes((font_w + 7) // 8 * font_h)
        else:
            bbx, bitmap = chars[code]
            canvas = render_to_canvas(bbx, bitmap, font_w, font_h, baseline_offset)
            data = pack_canvas(canvas, font_w)
        # Pretty print as Python bytes literal
        hex_str = ''.join(f'\\x{b:02x}' for b in data)
        ch_repr = chr(code) if 0x20 <= code <= 0x7E and code != 0x5C else f'\\x{code:02x}'
        lines.append(f"    b'{hex_str}',  # 0x{code:02x} '{ch_repr}'")

    lines.append(")")
    lines.append("")
    # 共通名 alias (sampler 等から FONT / W / H で参照できるように)
    lines.append(f"FONT = _{font_name}")
    lines.append(f"W = _{font_name}_W")
    lines.append(f"H = _{font_name}_H")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: bdf_to_py.py <bdf_file> <const_name> <descent> [out_file]")
        print("  descent: baseline offset from bottom (number of rows of descender)")
        sys.exit(1)
    bdf = sys.argv[1]
    name = sys.argv[2]
    desc = int(sys.argv[3])
    out = sys.argv[4] if len(sys.argv) > 4 else None
    src = convert(bdf, name, desc)
    if out:
        with open(out, "w") as f:
            f.write(src)
        print(f"Wrote {out}", file=sys.stderr)
    else:
        print(src)
