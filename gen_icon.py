#!/usr/bin/env python3
"""生成 Inkwell 应用图标（圆角蓝底 + 白色 markdown 风格标记），输出 inkwell/assets/icon.ico。"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkwell", "assets", "icon.ico")


def rounded(draw, box, r, fill):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=r, fill=fill)


def make(size):
    S = size * 4  # 超采样
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 渐变蓝底圆角方块
    pad = int(S * 0.06)
    # 简单两段竖向渐变
    top = (217, 119, 87)      # #D97757 Anthropic 陶土橘
    bot = (198, 97, 63)       # #C6613F 深陶土（与 --accent 呼应）
    grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        c = (int(top[0] * (1 - t) + bot[0] * t),
             int(top[1] * (1 - t) + bot[1] * t),
             int(top[2] * (1 - t) + bot[2] * t), 255)
        gd.line([(0, y), (S, y)], fill=c)
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # 白色 “M” + 向下箭头（markdown 风格）
    w = (255, 255, 255, 255)
    cx0 = int(S * 0.22)
    cy0 = int(S * 0.34)
    cy1 = int(S * 0.66)
    stroke = int(S * 0.055)
    # M 的四个折点
    mx = [cx0, cx0, int(S * 0.40), int(S * 0.50)]
    # 画 M：左竖、左斜、右斜、右竖
    Mw = int(S * 0.30)
    x_l = int(S * 0.20)
    x_r = x_l + Mw
    x_m = (x_l + x_r) // 2
    d.line([(x_l, cy1), (x_l, cy0)], fill=w, width=stroke)
    d.line([(x_l, cy0), (x_m, int(S * 0.50))], fill=w, width=stroke)
    d.line([(x_m, int(S * 0.50)), (x_r, cy0)], fill=w, width=stroke)
    d.line([(x_r, cy0), (x_r, cy1)], fill=w, width=stroke)
    # 向下箭头
    ax = int(S * 0.70)
    d.line([(ax, cy0), (ax, cy1)], fill=w, width=stroke)
    aw = int(S * 0.085)
    d.polygon([(ax - aw, cy1 - aw), (ax + aw, cy1 - aw), (ax, cy1 + int(aw * 0.7))], fill=w)

    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = make(256)
    base.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
