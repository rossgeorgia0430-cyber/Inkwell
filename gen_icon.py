#!/usr/bin/env python3
"""生成 Inkwell 应用图标（圆角陶土底 + 白色 markdown 风格标记）。

输出：
  inkwell/assets/icon.ico   Windows
  inkwell/assets/icon.png   通用 1024px
  inkwell/assets/icon.icns  macOS（本机有 iconutil 时）
"""
import os
import shutil
import subprocess
import sys
import tempfile
from PIL import Image, ImageDraw

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkwell", "assets")
OUT_ICO = os.path.join(ASSET_DIR, "icon.ico")
OUT_PNG = os.path.join(ASSET_DIR, "icon.png")
OUT_ICNS = os.path.join(ASSET_DIR, "icon.icns")


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


def write_icns(png_1024):
    """用 macOS iconutil 从 1024 PNG 生成 .icns。"""
    if sys.platform != "darwin" or not shutil.which("iconutil"):
        return False
    mapping = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    tmp = tempfile.mkdtemp(prefix="inkwell-iconset-")
    iconset = os.path.join(tmp, "icon.iconset")
    os.makedirs(iconset)
    try:
        for name, size in mapping.items():
            png_1024.resize((size, size), Image.LANCZOS).save(os.path.join(iconset, name), format="PNG")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", OUT_ICNS], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


def main():
    os.makedirs(ASSET_DIR, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = make(256)
    base.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in sizes])
    print("wrote", OUT_ICO)
    png = make(1024)
    png.save(OUT_PNG, format="PNG")
    print("wrote", OUT_PNG)
    if write_icns(png):
        print("wrote", OUT_ICNS)


if __name__ == "__main__":
    main()
