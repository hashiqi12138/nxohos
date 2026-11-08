#!/usr/bin/env python3
# verify_pixel.py — 截图像素断言（B 文档 §测试基础设施约定）
#
# 用法（TC0-2-2 示例）：
#   python verify_pixel.py s.jpeg center 15 220 120 --tol 16
#   python verify_pixel.py s.png fullframe
#   python verify_pixel.py frame.bin readback 15 220 120 --width 1080 --height 2400 --tol 2
#   python verify_pixel.py s.png compare-baseline baseline.png --tol 16
#   python verify_pixel.py --selftest
#
# 模式：
#   center            中心 16x16 块均值 ≈ 目标 RGB（±tol/通道）
#   fullframe         非纯色判定：亮度 std > 8/255 且非主色像素 > 5%
#   readback          裸 RGBA8 转储整帧逐像素断言（G0.2 兜底，容差 ±2）
#   compare-baseline  与基准图同尺寸逐像素比对（≥99.5% 像素在容差内）
#
# 支持格式：PNG（纯标准库解码，8bit 非隔行 color type 0/2/3/4/6）；
#           JPEG 需 Pillow（缺失时明确报错）；readback 为裸 RGBA8 .bin。
import argparse
import math
import os
import struct
import sys
import zlib
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "configs"))
import pixel_thresholds as TH  # noqa: E402


# ---------------------------------------------------------------- PNG 解码
def decode_png(data):
    """纯标准库 PNG 解码。返回 (width, height, pixels)，pixels 为
    bytes 长度 = w*h*4 的 RGBA8（灰度/索引自动展开为灰值 RGB）。
    畸形输入统一抛 ValueError（避免 struct.error/zlib.error 泄漏给调用方）。"""
    try:
        return _decode_png_inner(data)
    except (struct.error, zlib.error, IndexError, KeyError) as e:
        raise ValueError("malformed PNG: %s" % e)


def _decode_png_inner(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w = 8, bytearray(), None
    h = depth = ctype = interlace = None
    plte = None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        if pos + 8 + length > len(data):
            raise ValueError("truncated chunk (len=%d)" % length)
        ctype4 = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype4 == b"IHDR":
            w, h, depth, ctype, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunk)
            if depth != 8 or interlace != 0 or comp != 0 or filt != 0:
                raise ValueError("unsupported PNG: depth=%s interlace=%s"
                                 % (depth, interlace))
            if ctype not in (0, 2, 3, 4, 6):
                raise ValueError("unsupported color type %s" % ctype)
        elif ctype4 == b"PLTE":
            plte = chunk
        elif ctype4 == b"IDAT":
            idat += chunk
        elif ctype4 == b"IEND":
            break
    if w is None:
        raise ValueError("missing IHDR")
    raw = zlib.decompress(bytes(idat))
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * ch
    if len(raw) != (stride + 1) * h:
        raise ValueError("IDAT size mismatch")
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ft == 1:      # Sub
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif ft == 2:    # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:    # Average
            for i in range(stride):
                left = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:    # Paeth
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ft != 0:
            raise ValueError("bad filter %d" % ft)
        prev = line
        for x in range(w):
            o = (y * w + x) * 4
            if ctype == 0:
                g = line[x]
                out[o:o + 3] = bytes((g, g, g))
            elif ctype == 2:
                out[o:o + 3] = line[x * 3:x * 3 + 3]
            elif ctype == 3:
                idx = line[x] * 3
                if not plte or idx + 3 > len(plte):
                    raise ValueError("bad PLTE reference")
                out[o:o + 3] = plte[idx:idx + 3]
            elif ctype == 4:
                out[o:o + 3] = bytes((line[x * 2],) * 3)
            else:
                out[o:o + 3] = line[x * 4:x * 4 + 3]
            out[o + 3] = 255
    return w, h, bytes(out)


def load_image(path):
    """返回 (w, h, rgba_bytes)。JPEG 走 Pillow（缺失时明确报错）。"""
    with open(path, "rb") as f:
        head = f.read(8)
        f.seek(0)
        data = f.read()
    if head.startswith(b"\x89PNG"):
        return decode_png(data)
    if data[:3] == b"\xff\xd8\xff":
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            raise SystemExit("ERROR: JPEG input requires Pillow: pip install pillow")
        im = Image.open(path).convert("RGBA")
        return im.width, im.height, im.tobytes()
    raise SystemExit("ERROR: unsupported image format: %s" % path)


def load_raw_rgba(path, width, height):
    expect = width * height * 4
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < expect:
        raise SystemExit("ERROR: raw file too small: %d < %d" % (len(data), expect))
    return width, height, data[:expect]


# ---------------------------------------------------------------- 模式实现
def center_avg(w, h, px, half=8):
    """中心 (2*half)^2 块的 RGB 均值。"""
    cx, cy = w // 2, h // 2
    sr = sg = sb = n = 0
    for y in range(cy - half, cy + half):
        for x in range(cx - half, cx + half):
            o = (y * w + x) * 4
            sr += px[o]
            sg += px[o + 1]
            sb += px[o + 2]
            n += 1
    return sr / n, sg / n, sb / n


def mode_center(w, h, px, target, tol):
    r, g, b = center_avg(w, h, px)
    diffs = (abs(r - target[0]), abs(g - target[1]), abs(b - target[2]))
    ok = all(d <= tol for d in diffs)
    print("center avg RGB = (%.1f, %.1f, %.1f), target=%s, diffs=(%.1f, %.1f, %.1f), tol=%d"
          % (r, g, b, target, diffs[0], diffs[1], diffs[2], tol))
    return ok


def mode_fullframe(w, h, px):
    n = w * h
    lum = []
    cnt = Counter()
    for i in range(n):
        o = i * 4
        r, g, b = px[o], px[o + 1], px[o + 2]
        lum.append(0.299 * r + 0.587 * g + 0.114 * b)
        cnt[(r, g, b)] += 1
    mean = sum(lum) / n
    std = (sum((v - mean) ** 2 for v in lum) / n) ** 0.5
    dominant = cnt.most_common(1)[0][1] / n
    nondominant = 1.0 - dominant
    ok = std > TH.FULLFRAME_MIN_STD and nondominant > TH.FULLFRAME_MIN_NONDOMINANT
    print("fullframe: lum std=%.2f (>%d), non-dominant=%.3f (>%.2f)"
          % (std, TH.FULLFRAME_MIN_STD, nondominant, TH.FULLFRAME_MIN_NONDOMINANT))
    return ok


def mode_readback(w, h, px, target, tol):
    n = w * h
    maxd = 0
    over = 0
    for i in range(n):
        o = i * 4
        d = max(abs(px[o] - target[0]), abs(px[o + 1] - target[1]),
                abs(px[o + 2] - target[2]))
        if d > tol:
            over += 1
        maxd = max(maxd, d)
    ok = over == 0
    print("readback: %d/%d pixels over tol=%d, max diff=%d" % (over, n, tol, maxd))
    return ok


def mode_compare_baseline(w, h, px, base_w, base_h, bpx, tol, psnr_min=None):
    """双门槛：≥99.5% 像素在容差内 **且** PSNR > psnr_min（B 文档 TC0-3-2
    ">40dB"——严格大于；psnr_min=None 时取阈值配置）。"""
    if (w, h) != (base_w, base_h):
        print("size mismatch: %dx%d vs %dx%d" % (w, h, base_w, base_h))
        return False
    if psnr_min is None:
        psnr_min = TH.BASELINE_MIN_PSNR_DB
    n = w * h
    within = 0
    se = 0.0
    for i in range(n):
        o = i * 4
        d = max(abs(px[o] - bpx[o]), abs(px[o + 1] - bpx[o + 1]),
                abs(px[o + 2] - bpx[o + 2]))
        se += (d * d)
        if d <= tol:
            within += 1
    ratio = within / n
    mse = se / n
    psnr = 10.0 * math.log10(255.0 ** 2 / mse) if mse > 0 else float("inf")
    ok = ratio >= TH.BASELINE_PASS_RATIO and psnr > psnr_min   # B：">40dB" 严格大于
    print("baseline: %.4f pixels within tol=%d (need >=%.4f), "
          "PSNR=%.2fdB (need >%.1f)"
          % (ratio, tol, TH.BASELINE_PASS_RATIO, psnr, psnr_min))
    return ok


# ---------------------------------------------------------------- selftest
def _chunk(t, d):
    c = t + d
    return struct.pack(">I", len(d)) + c + struct.pack(
        ">I", zlib.crc32(c) & 0xFFFFFFFF)


def _png_encode(w, h, rgba, ctype=6):
    """RGBA8 → PNG（filter 0 逐行）。ctype=6 原样；其余路径用 _png_raw。"""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)
    raw = bytearray()
    if ctype == 6:
        for y in range(h):
            raw.append(0)
            raw += rgba[y * w * 4:(y + 1) * w * 4]
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw))) + _chunk(b"IEND", b""))


def _png_raw(w, h, ctype, scanlines):
    """预滤波 scanlines（各含 filter 字节）→ PNG（selftest 专用）。"""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(b"".join(scanlines)))
            + _chunk(b"IEND", b""))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_line(orig, prev, ft, ch):
    """构造 filter 类型 ft 的差分行（与解码端互逆）。"""
    out = bytearray()
    for i in range(len(orig)):
        a = orig[i - ch] if i >= ch else 0
        b = prev[i]
        c = prev[i - ch] if i >= ch else 0
        if ft == 1:
            v = orig[i] - a
        elif ft == 2:
            v = orig[i] - b
        elif ft == 3:
            v = orig[i] - ((a + b) >> 1)
        else:
            v = orig[i] - _paeth(a, b, c)
        out.append(v & 0xFF)
    return bytes(out)


def selftest():
    failed = 0

    def check(name, cond):
        nonlocal failed
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))
        if not cond:
            failed += 1

    w, h = 64, 64
    solid = bytes((15, 220, 120, 255)) * (w * h)
    png_solid = _png_encode(w, h, solid)

    # decode + center 命中/不命中
    dw, dh, px = decode_png(png_solid)
    check("png decode dims", (dw, dh) == (w, h))
    check("center hit", mode_center(dw, dh, px, (15, 220, 120), TH.CENTER_DEFAULT_TOL))
    check("center miss", not mode_center(dw, dh, px, (20, 220, 120), 0))

    # fullframe：渐变图非纯色；纯色图被拒
    grad = bytearray()
    for y in range(h):
        for x in range(w):
            grad += bytes((x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256, 255))
    _, _, gpx = decode_png(_png_encode(w, h, bytes(grad)))
    check("fullframe gradient", mode_fullframe(w, h, gpx))
    check("fullframe solid rejected", not mode_fullframe(w, h, px))

    # readback
    check("readback hit", mode_readback(w, h, solid, (15, 220, 120),
                                        TH.READBACK_DEFAULT_TOL))
    off = bytearray(solid)
    off[4] = 200   # 第 2 个像素 R 通道越界
    check("readback miss", not mode_readback(w, h, bytes(off), (15, 220, 120),
                                             TH.READBACK_DEFAULT_TOL))

    # compare-baseline（PSNR 门槛默认 TH.BASELINE_MIN_PSNR_DB）
    check("baseline same", mode_compare_baseline(w, h, px, w, h, px, 16))
    # 比例门槛专项：单像素大偏差也在 tol=200 内，比例=1.0 通过——
    # 显式 psnr_min=0 只测比例逻辑（该扰动 PSNR≈38.9dB < 40，默认门槛会拒）
    check("baseline within tol", mode_compare_baseline(w, h, px, w, h,
                                                       bytes(off), 200, 0.0))
    # PSNR 门槛专项：同批扰动默认门槛下必须拒绝（TC0-3-2 >40dB 判定生效）
    check("baseline psnr rejected", not mode_compare_baseline(w, h, px, w, h,
                                                              bytes(off), 200))

    # 畸形 PNG：统一 ValueError，不泄漏 struct.error/zlib.error
    for bad in (b"", b"\x89PNG\r\n\x1a\n",            # 空 / 仅签名
                png_solid[:40],                        # 截断 chunk
                png_solid.replace(b"IDAT", b"JDAT")):  # 无 IDAT → zlib.error
        try:
            decode_png(bad)
            check("malformed png %r raises ValueError" % bad[:8], False)
        except ValueError:
            pass
        except Exception as e:                         # noqa: BLE001
            check("malformed png %r wrong exc %r" % (bad[:8], e), False)

    # 滤波器 Sub/Up/Average/Paeth 逐一覆盖（解码端互逆性）
    w2, h2, ch = 16, 16, 3
    orig_rows = []
    for y in range(h2):
        orig_rows.append(bytes(((x * 7 + y * 13 + 11) % 256
                                for x in range(w2 * ch))))
    decoded_ok = True
    for ft in (1, 2, 3, 4):
        lines = []
        for y in range(h2):
            prev = orig_rows[y - 1] if (ft in (2, 3, 4) and y > 0) else bytes(w2 * ch)
            lines.append(bytes([ft]) + _filter_line(orig_rows[y], prev, ft, ch))
        _, _, fpx = decode_png(_png_raw(w2, h2, 2, lines))
        want = bytearray()
        for y in range(h2):
            for x in range(w2):
                want += orig_rows[y][x * 3:x * 3 + 3] + b"\xff"
        if bytes(fpx) != bytes(want):
            decoded_ok = False
    check("filters 1-4 roundtrip", decoded_ok)
    return failed


def main():
    ap = argparse.ArgumentParser(description="screenshot pixel assertion")
    ap.add_argument("image", nargs="?", help="image path (png/jpeg) or raw .bin")
    ap.add_argument("mode", nargs="?", default=None,
                    help="center | fullframe | readback | compare-baseline")
    ap.add_argument("extra", nargs="*", help="R G B / baseline path")
    ap.add_argument("--mode", dest="mode_opt", default=None)
    ap.add_argument("--tol", type=int, default=None)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--psnr-min", type=float, default=None,
                    help="compare-baseline 最低 PSNR dB（默认取阈值配置）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(1 if selftest() else 0)

    if not args.image:
        ap.error("image required (or --selftest)")
    mode = args.mode_opt or args.mode
    if not mode:
        ap.error("mode required")

    if mode == "readback":
        if not (args.width and args.height):
            ap.error("readback needs --width/--height")
        w, h, px = load_raw_rgba(args.image, args.width, args.height)
    else:
        w, h, px = load_image(args.image)

    if mode == "center":
        if len(args.extra) < 3:
            ap.error("center needs R G B")
        target = tuple(int(v) for v in args.extra[:3])
        tol = TH.CENTER_DEFAULT_TOL if args.tol is None else args.tol
        ok = mode_center(w, h, px, target, tol)
    elif mode == "fullframe":
        ok = mode_fullframe(w, h, px)
    elif mode == "readback":
        if len(args.extra) < 3:
            ap.error("readback needs R G B")
        target = tuple(int(v) for v in args.extra[:3])
        tol = TH.READBACK_DEFAULT_TOL if args.tol is None else args.tol
        ok = mode_readback(w, h, px, target, tol)
    elif mode == "compare-baseline":
        baseline = args.baseline or (args.extra[0] if args.extra else None)
        if not baseline:
            ap.error("compare-baseline needs baseline path")
        bw, bh, bpx = load_image(baseline)
        tol = TH.BASELINE_DEFAULT_TOL if args.tol is None else args.tol
        ok = mode_compare_baseline(w, h, px, bw, bh, bpx, tol, args.psnr_min)
    else:
        ap.error("unknown mode: %s" % mode)

    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
