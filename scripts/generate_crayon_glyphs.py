#!/usr/bin/env python3
"""
Generate MapLibre / TileServer-GL Signed Distance Field (SDF) PBF glyph stacks
for the CC0 1.0 Public Domain "Crayon Libre" typeface:
  - Crayon Libre Regular
  - Crayon Libre Bold
  - Crayon Libre Italic

For Unicode code points outside Crayon Libre's character map (such as CJK, Arabic,
Cyrillic, Devanagari, etc.), glyphs seamlessly fall back to the bundled Noto Sans
PBF stacks so international labels never drop characters.
"""

import os
import shutil
import numpy as np
from fontTools.ttLib import TTFont
from PIL import ImageFont
from scipy.ndimage import binary_closing, binary_dilation, distance_transform_edt


def encode_varint(val: int) -> bytes:
    out = bytearray()
    while val > 0x7F:
        out.append((val & 0x7F) | 0x80)
        val >>= 7
    out.append(val & 0x7F)
    return bytes(out)


def encode_sint32(val: int) -> bytes:
    zz = (val << 1) ^ (val >> 31)
    return encode_varint(zz & 0xFFFFFFFF)


def encode_tag(field_num: int, wire_type: int) -> bytes:
    return encode_varint((field_num << 3) | wire_type)


def encode_bytes_field(field_num: int, data: bytes) -> bytes:
    return encode_tag(field_num, 2) + encode_varint(len(data)) + data


def encode_uint32_field(field_num: int, val: int) -> bytes:
    return encode_tag(field_num, 0) + encode_varint(val)


def encode_sint32_field(field_num: int, val: int) -> bytes:
    return encode_tag(field_num, 0) + encode_sint32(val)


def encode_glyph_msg(
    codepoint: int,
    bitmap: bytes | None,
    width: int,
    height: int,
    left: int,
    top: int,
    advance: int,
) -> bytes:
    parts = [encode_uint32_field(1, codepoint)]
    if bitmap is not None and width > 0 and height > 0:
        parts.append(encode_bytes_field(2, bitmap))
    parts.append(encode_uint32_field(3, width))
    parts.append(encode_uint32_field(4, height))
    parts.append(encode_sint32_field(5, left))
    parts.append(encode_sint32_field(6, top))
    parts.append(encode_uint32_field(7, advance))
    return b"".join(parts)


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    res = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7


def parse_existing_pbf_glyphs(pbf_path: str) -> dict[int, bytes]:
    """Return raw encoded glyph messages keyed by Unicode codepoint from an existing PBF file."""
    glyph_map: dict[int, bytes] = {}
    if not os.path.isfile(pbf_path):
        return glyph_map
    with open(pbf_path, "rb") as f:
        data = f.read()
    if not data:
        return glyph_map

    pos = 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        fn, wt = tag >> 3, tag & 7
        if wt != 2:
            break
        slen, pos = read_varint(data, pos)
        stack_end = pos + slen
        if fn == 1:
            while pos < stack_end:
                st_tag, pos = read_varint(data, pos)
                sfn, swt = st_tag >> 3, st_tag & 7
                if swt == 2:
                    l, pos = read_varint(data, pos)
                    sub = data[pos : pos + l]
                    pos += l
                    if sfn == 3:
                        # Parse codepoint id (field 1) from glyph message
                        gpos = 0
                        cp = None
                        while gpos < len(sub):
                            gt, gpos = read_varint(sub, gpos)
                            gf, gw = gt >> 3, gt & 7
                            if gw == 0:
                                val, gpos = read_varint(sub, gpos)
                                if gf == 1:
                                    cp = val
                                    break
                            elif gw == 2:
                                gl, gpos = read_varint(sub, gpos)
                                gpos += gl
                        if cp is not None:
                            glyph_map[cp] = sub
                elif swt == 0:
                    _, pos = read_varint(data, pos)
        else:
            pos = stack_end
    return glyph_map


def build_fontstack_pbf(font_name: str, range_str: str, glyph_msgs: list[bytes]) -> bytes:
    stack_parts = [
        encode_bytes_field(1, font_name.encode("utf-8")),
        encode_bytes_field(2, range_str.encode("utf-8")),
    ]
    for gm in glyph_msgs:
        stack_parts.append(encode_bytes_field(3, gm))
    stack_body = b"".join(stack_parts)
    return encode_bytes_field(1, stack_body)


def render_sdf_glyph(
    font24: ImageFont.FreeTypeFont,
    font_hi: ImageFont.FreeTypeFont,
    ascent24: int,
    codepoint: int,
    scale: int = 4,
    close_iters: int = 1,
    dilate_iters: int = 0,
) -> bytes | None:
    ch = chr(codepoint)
    try:
        mask24, (dx24, dy24) = font24.getmask2(ch, mode="L")
    except Exception:
        return None

    w24, h24 = mask24.size
    adv24 = max(1, int(round(font24.getlength(ch)))) + (1 if dilate_iters > 0 else 0)

    if w24 == 0 or h24 == 0 or ch.isspace():
        return encode_glyph_msg(
            codepoint=codepoint,
            bitmap=None,
            width=0,
            height=0,
            left=0,
            top=-25,
            advance=adv24,
        )

    mask_hi, (dx_hi, dy_hi) = font_hi.getmask2(ch, mode="L")
    mw, mh = mask_hi.size
    if mw == 0 or mh == 0:
        return None

    # Ensure hi-res canvas is an exact multiple of `scale` matching w24, h24
    extra_px = 1 if dilate_iters > 0 else 0
    out_w = w24 + extra_px
    out_h = h24 + extra_px
    target_hi_w = out_w * scale
    target_hi_h = out_h * scale

    arr_hi = np.array(mask_hi, dtype=np.uint8).reshape((mh, mw))
    canvas_hi = np.zeros((target_hi_h, target_hi_w), dtype=bool)
    copy_h = min(mh, target_hi_h)
    copy_w = min(mw, target_hi_w)
    canvas_hi[:copy_h, :copy_w] = arr_hi[:copy_h, :copy_w] >= 120

    border = 3
    pad_hi = border * scale
    bpad = np.pad(canvas_hi, pad_hi, mode="constant", constant_values=False)

    if close_iters > 0:
        struct_c = np.ones((close_iters * 2 + 1, close_iters * 2 + 1), dtype=bool)
        bpad = binary_closing(bpad, structure=struct_c)
    if dilate_iters > 0:
        struct_d = np.ones((dilate_iters * 2 + 1, dilate_iters * 2 + 1), dtype=bool)
        bpad = binary_dilation(bpad, structure=struct_d)

    dist_in = distance_transform_edt(bpad)
    dist_out = distance_transform_edt(~bpad)
    signed_dist = (dist_in - dist_out - 0.5) / float(scale)

    full_h = out_h + 2 * border
    full_w = out_w + 2 * border
    sd_small = signed_dist.reshape(full_h, scale, full_w, scale).mean(axis=(1, 3))
    sdf = np.clip(np.round(192.0 + sd_small * 32.0), 0, 255).astype(np.uint8)

    bearing_y = ascent24 - dy24
    top = bearing_y - 25
    left = dx24

    return encode_glyph_msg(
        codepoint=codepoint,
        bitmap=sdf.tobytes(),
        width=out_w,
        height=out_h,
        left=left,
        top=top,
        advance=adv24,
    )


def generate_font_stack(
    font_name: str,
    ttf_path: str,
    fallback_dir: str,
    out_dirs: list[str],
    close_iters: int = 1,
    dilate_iters: int = 0,
) -> None:
    tt = TTFont(ttf_path)
    cmap = set(tt.getBestCmap().keys())
    # Exclude control chars except 32 (space)
    cmap = {cp for cp in cmap if cp >= 32}

    scale = 4
    font24 = ImageFont.truetype(ttf_path, 24)
    font_hi = ImageFont.truetype(ttf_path, 24 * scale)
    ascent24, _ = font24.getmetrics()

    # Pre-render all supported Crayon Libre codepoints
    rendered_crayon: dict[int, bytes] = {}
    for cp in sorted(cmap):
        msg = render_sdf_glyph(
            font24=font24,
            font_hi=font_hi,
            ascent24=ascent24,
            codepoint=cp,
            scale=scale,
            close_iters=close_iters,
            dilate_iters=dilate_iters,
        )
        if msg is not None:
            rendered_crayon[cp] = msg

    for d in out_dirs:
        os.makedirs(d, exist_ok=True)

    for start in range(0, 65536, 256):
        end = start + 255
        range_str = f"{start}-{end}"
        fb_pbf = os.path.join(fallback_dir, f"{range_str}.pbf")
        fb_glyphs = parse_existing_pbf_glyphs(fb_pbf)

        merged_cps = sorted(set(fb_glyphs.keys()) | {cp for cp in rendered_crayon if start <= cp <= end})
        glyph_msgs = []
        for cp in merged_cps:
            if cp in rendered_crayon:
                glyph_msgs.append(rendered_crayon[cp])
            elif cp in fb_glyphs:
                glyph_msgs.append(fb_glyphs[cp])

        pbf_bytes = build_fontstack_pbf(font_name, range_str, glyph_msgs)
        for d in out_dirs:
            with open(os.path.join(d, f"{range_str}.pbf"), "wb") as f:
                f.write(pbf_bytes)

    print(f"Generated {font_name} ({len(rendered_crayon)} Crayon Libre glyphs + Noto fallback across 256 ranges)")


def main() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_font_dir = os.path.join(repo_root, "tileserver", "fonts", "crayon-libre")
    os.makedirs(src_font_dir, exist_ok=True)

    for fn in ("CrayonLibre-Regular.ttf", "CrayonLibre-Italic.ttf", "CRAYON_LIBRE_LICENSE.txt"):
        tmp_src = os.path.join("/tmp/fonts_test", fn)
        if os.path.isfile(tmp_src):
            shutil.copy2(tmp_src, os.path.join(src_font_dir, fn))

    ts_fonts = os.path.join(repo_root, "tileserver", "fonts")
    pm_fonts = os.path.join(repo_root, "app", "static", "vendor", "protomaps-assets", "fonts")

    specs = [
        (
            "Crayon Libre Regular",
            os.path.join(src_font_dir, "CrayonLibre-Regular.ttf"),
            os.path.join(ts_fonts, "Noto Sans Regular"),
            1,
            0,
        ),
        (
            "Crayon Libre Bold",
            os.path.join(src_font_dir, "CrayonLibre-Regular.ttf"),
            os.path.join(ts_fonts, "Noto Sans Medium"),
            2,
            1,
        ),
        (
            "Crayon Libre Italic",
            os.path.join(src_font_dir, "CrayonLibre-Italic.ttf"),
            os.path.join(ts_fonts, "Noto Sans Italic"),
            1,
            0,
        ),
    ]

    for font_name, ttf_path, fb_dir, close_it, dilate_it in specs:
        out_dirs = [
            os.path.join(ts_fonts, font_name),
            os.path.join(pm_fonts, font_name),
        ]
        generate_font_stack(
            font_name=font_name,
            ttf_path=ttf_path,
            fallback_dir=fb_dir,
            out_dirs=out_dirs,
            close_iters=close_it,
            dilate_iters=dilate_it,
        )


if __name__ == "__main__":
    main()
