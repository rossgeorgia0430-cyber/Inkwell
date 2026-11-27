#!/usr/bin/env python3
"""生成 Pygments 浅/深两套代码高亮 CSS（作用域 .codehilite）。

两套配色与 base.css 的「墨蓝」主题同源：五个克制的色相（紫、青绿、琥珀、蓝、洋红）
加一个青色类型色，不使用背景色块（diff 增删行除外），文字对代码底色均 >= 4.5:1。
"""
import os
from pygments.formatters import HtmlFormatter
from pygments.style import Style
from pygments.token import (
    Comment, Error, Generic, Keyword, Literal, Name, Number,
    Operator, Punctuation, String, Text,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkwell", "assets")


def _tint(fg: str, bg: str, alpha: float) -> str:
    """把 fg 以 alpha 不透明度叠在 bg 上，得到 Pygments 可用的实色底（它不支持半透明）。"""
    mix = [round(int(fg[i:i + 2], 16) * alpha + int(bg[i:i + 2], 16) * (1 - alpha)) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(mix)


class InkwellLightStyle(Style):
    """浅色代码配色，底色与 base.css 浅色主题的 --code-bg 一致。"""

    background_color = "#f1f3f7"
    highlight_color = "#e0e4ea"
    styles = {
        Text:                  "#252a31",
        Text.Whitespace:       "#cbd0d8",
        Error:                 "#c0352b",
        Comment:               "italic #66707c",
        Keyword:               "#6f42c1",
        Keyword.Constant:      "#a8500a",
        Keyword.Type:          "#0b7285",
        Operator:              "#3d444d",
        Operator.Word:         "#6f42c1",
        Punctuation:           "#3d444d",
        Name:                  "#252a31",
        Name.Attribute:        "#b02a62",
        Name.Builtin:          "#0b7285",
        Name.Class:            "bold #2156c4",
        Name.Constant:         "#a8500a",
        Name.Decorator:        "#b02a62",
        Name.Exception:        "#2156c4",
        Name.Function:         "bold #2156c4",
        Name.Tag:              "#b02a62",
        Name.Variable:         "#252a31",
        Literal:               "#a8500a",
        String:                "#0e7c6b",
        String.Escape:         "#a8500a",
        Number:                "#a8500a",
        Generic.Deleted:       "#252a31 bg:" + _tint("#c0352b", "#f1f3f7", .12),
        Generic.Inserted:      "#252a31 bg:" + _tint("#187a33", "#f1f3f7", .12),
        Generic.Heading:       "bold #6f42c1",
        Generic.Subheading:    "#2156c4",
        Generic.Emph:          "italic",
        Generic.Strong:        "bold",
    }


class InkwellDarkStyle(Style):
    """深色代码配色，底色与 base.css 深色主题的 --code-bg 一致。"""

    background_color = "#1a1d23"
    highlight_color = "#2a2e37"
    styles = {
        Text:                  "#cfd4dc",
        Text.Whitespace:       "#363b46",
        Error:                 "#ff8f85",
        Comment:               "italic #7b8594",
        Keyword:               "#c4a7ff",
        Keyword.Constant:      "#f0b674",
        Keyword.Type:          "#7cc7e0",
        Operator:              "#aab1bc",
        Operator.Word:         "#c4a7ff",
        Punctuation:           "#aab1bc",
        Name:                  "#cfd4dc",
        Name.Attribute:        "#ff9ec0",
        Name.Builtin:          "#7cc7e0",
        Name.Class:            "bold #8fb0ff",
        Name.Constant:         "#f0b674",
        Name.Decorator:        "#ff9ec0",
        Name.Exception:        "#8fb0ff",
        Name.Function:         "bold #8fb0ff",
        Name.Tag:              "#ff9ec0",
        Name.Variable:         "#cfd4dc",
        Literal:               "#f0b674",
        String:                "#7fd1b9",
        String.Escape:         "#f0b674",
        Number:                "#f0b674",
        Generic.Deleted:       "#cfd4dc bg:" + _tint("#ff8f85", "#1a1d23", .14),
        Generic.Inserted:      "#cfd4dc bg:" + _tint("#7ed492", "#1a1d23", .14),
        Generic.Heading:       "bold #c4a7ff",
        Generic.Subheading:    "#8fb0ff",
        Generic.Emph:          "italic",
        Generic.Strong:        "bold",
    }


def gen(style, filename, label):
    fmt = HtmlFormatter(style=style, cssclass="codehilite")
    css = fmt.get_style_defs(".codehilite")
    header = f"/* pygments style: {label} (scope .codehilite) */\n"
    path = os.path.join(OUT, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + css + "\n")
    print(f"wrote {path}  (style={label})")


if __name__ == "__main__":
    gen(InkwellLightStyle, "pygments-light.css", "inkwell-light")
    gen(InkwellDarkStyle, "pygments-dark.css", "inkwell-dark")
