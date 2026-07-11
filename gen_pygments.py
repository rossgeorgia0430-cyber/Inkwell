#!/usr/bin/env python3
"""生成 Pygments 浅/深两套代码高亮 CSS（作用域 .codehilite）。"""
import os
from pygments.formatters import HtmlFormatter
from pygments.style import Style
from pygments.styles import get_all_styles
from pygments.token import (
    Comment, Error, Generic, Keyword, Literal, Name, Number,
    Operator, Punctuation, String, Text,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkwell", "assets")


def pick(preferred):
    available = set(get_all_styles())
    for s in preferred:
        if s in available:
            return s
    return "default"


class InkwellDarkStyle(Style):
    """低饱和暖中性的夜间代码配色，避免大面积蓝色造成视觉疲劳。"""

    background_color = "#20201e"
    highlight_color = "#35332e"
    styles = {
        Text:                  "#d8d5cc",
        Text.Whitespace:       "#5f5d57",
        Error:                 "#df8585",
        Comment:               "italic #7f7d75",
        Keyword:               "#d6a56f",
        Keyword.Constant:      "#c6a7cf",
        Keyword.Type:          "#cfbd82",
        Operator:              "#bdb9ae",
        Operator.Word:         "#d6a56f",
        Punctuation:           "#aaa79f",
        Name:                  "#d8d5cc",
        Name.Builtin:          "#cfbd82",
        Name.Class:            "bold #d4bf83",
        Name.Constant:         "#c6a7cf",
        Name.Decorator:        "#c6a7cf",
        Name.Exception:        "#d4bf83",
        Name.Function:         "bold #aac18d",
        Name.Tag:              "#aac18d",
        Name.Variable:         "#d8d5cc",
        Literal:               "#d8d5cc",
        String:                "#a9c18e",
        String.Escape:         "#d6a56f",
        Number:                "#c6a7cf",
        Generic.Deleted:       "#df8585",
        Generic.Inserted:      "#aac18d",
        Generic.Heading:       "bold #d6a56f",
        Generic.Subheading:    "#cfbd82",
        Generic.Emph:          "italic",
        Generic.Strong:        "bold",
    }


def gen(style, filename, label=None):
    fmt = HtmlFormatter(style=style, cssclass="codehilite")
    css = fmt.get_style_defs(".codehilite")
    style_label = label or str(style)
    header = f"/* pygments style: {style_label} (scope .codehilite) */\n"
    path = os.path.join(OUT, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + css + "\n")
    print(f"wrote {path}  (style={style_label})")


if __name__ == "__main__":
    # 与应用的冷静蓝灰底色配合；避免 stata-light 的高饱和蓝绿红让代码区显脏。
    light = pick(["xcode", "friendly", "default"])
    dark = InkwellDarkStyle
    gen(light, "pygments-light.css")
    gen(dark, "pygments-dark.css", "inkwell-dark")
