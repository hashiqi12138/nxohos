#!/usr/bin/env python3
# find_icons.py — 从云调试投屏截图里找出红色控件框，输出可点击的图标中心坐标
#
# 背景：AGC 云调试开启"辅助控件绘制"后，设备画面被隐私替换为红色控件轮廓，
# 无法读出文字/图标内容；要自动启动探针只能按几何位置试探点击。
# 本脚本用 verify_pixel.decode_png（无第三方依赖）解析 PNG，做红色像素连通域
# 聚类，筛出"接近正方形/图标尺寸"的框，换算成 canvas 与 iframe 坐标。
#
# 用法：
#   python tools/verify/find_icons.py out/desk.png --canvas 321x722 \
#       --origin 126,307          # canvas 在 iframe 内的左上角
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_pixel as vp   # noqa: E402


def is_red(r, g, b):
    return r > 150 and g < 110 and b < 110


def boxes(px, w, h, min_side):
    """按行扫描找红色像素，做简单连通域（4 邻域 BFS），返回外接矩形。"""
    seen = [[False] * w for _ in range(h)]
    out = []
    for y in range(h):
        for x in range(w):
            if seen[y][x]:
                continue
            r, g, b = px[(y * w + x) * 4: (y * w + x) * 4 + 3]
            if not is_red(r, g, b):
                continue
            stack = [(x, y)]
            seen[y][x] = True
            x0 = x1 = x
            y0 = y1 = y
            while stack:
                cx, cy = stack.pop()
                x0, x1 = min(x0, cx), max(x1, cx)
                y0, y1 = min(y0, cy), max(y1, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx]:
                        rr, gg, bb = px[(ny * w + nx) * 4:(ny * w + nx) * 4 + 3]
                        if is_red(rr, gg, bb):
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            if bw >= min_side and bh >= min_side:
                out.append((x0, y0, bw, bh))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--canvas", default=None, help="canvas 尺寸 WxH（用于换算）")
    ap.add_argument("--origin", default=None, help="canvas 在 iframe 内的左上角 x,y")
    ap.add_argument("--min-side", type=float, default=40,
                    help="最小框边长（像素，过滤文字标签小框）")
    a = ap.parse_args()

    data = open(a.png, "rb").read()
    w, h, px = vp.decode_png(data)
    print("# image %dx%d" % (w, h))
    bs = boxes(px, w, h, a.min_side)
    # 只保留近似方形（图标框）与横条（卡片），按面积排序输出
    for (x0, y0, bw, bh) in sorted(bs, key=lambda t: -t[2] * t[3])[:40]:
        cx, cy = x0 + bw / 2.0, y0 + bh / 2.0
        line = "box %4d,%4d %4dx%-4d center(%.0f,%.0f)" % (x0, y0, bw, bh, cx, cy)
        if a.canvas and a.origin:
            cw, ch = (float(v) for v in a.canvas.split("x"))
            ox, oy = (float(v) for v in a.origin.split(","))
            ix = ox + cx * cw / w
            iy = oy + cy * ch / h
            line += "  iframe(%.0f,%.0f)" % (ix, iy)
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
