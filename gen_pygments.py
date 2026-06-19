#!/usr/bin/env python3
"""生成 Pygments 浅/深两套代码高亮 CSS（作用域 .codehilite）。"""
import os
from pygments.formatters import HtmlFormatter
from pygments.styles import get_all_styles

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkwell", "assets")


def pick(preferred):
    available = set(get_all_styles())
    for s in preferred:
        if s in available:
            return s
    return "default"


def gen(style_name, filename):
    fmt = HtmlFormatter(style=style_name, cssclass="codehilite")
    css = fmt.get_style_defs(".codehilite")
    header = f"/* pygments style: {style_name} (scope .codehilite) */\n"
    path = os.path.join(OUT, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + css + "\n")
    print(f"wrote {path}  (style={style_name})")


if __name__ == "__main__":
    light = pick(["stata-light", "friendly", "default"])
    dark = pick(["one-dark", "lightbulb", "monokai", "native"])
    gen(light, "pygments-light.css")
    gen(dark, "pygments-dark.css")
