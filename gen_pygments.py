#!/usr/bin/env python3
"""生成 Pygments 浅/深两套代码高亮 CSS（作用域 .codehilite）。

浅色 inkwell-light：暖纸面上的日间配色——墨绿关键字、赭石字符串、暗紫常量，
替代 xcode 的冷白底 + 品红/亮蓝（“IDE 感”过重，与纸面阅读气质不符）。
深色 inkwell-dark：低饱和暖中性的夜间配色，避免大面积蓝色造成视觉疲劳。
"""
import os
from pygments.formatters import HtmlFormatter
from pygments.style import Style
from pygments.token import (
    Comment, Error, Generic, Keyword, Literal, Name, Number,
    Operator, Punctuation, String, Text,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkwell", "assets")


class InkwellLightStyle(Style):
    """暖纸面（#f3f1e9）上的日间代码配色，与 app.css 浅色主题同源。"""

    background_color = "#f3f1e9"
    highlight_color = "#e9e5d6"
    styles = {
        Text:                  "#33302a",
        Text.Whitespace:       "#b9b4a4",
        Error:                 "#a63a30",
        Comment:               "italic #8a8577",
        Keyword:               "#3e6b59",
        Keyword.Constant:      "#6b5ca5",
        Keyword.Type:          "#8a6d3b",
        Operator:              "#5f5a4e",
        Operator.Word:         "#3e6b59",
        Punctuation:           "#6e695b",
        Name:                  "#33302a",
        Name.Builtin:          "#8a6d3b",
        Name.Class:            "bold #6b4fa2",
        Name.Constant:         "#6b5ca5",
        Name.Decorator:        "#6b4fa2",
        Name.Exception:        "#6b4fa2",
        Name.Function:         "bold #31597f",
        Name.Tag:              "#3e6b59",
        Name.Variable:         "#33302a",
        Literal:               "#7a5c2e",
        String:                "#8f4e33",
        String.Escape:         "#b07a2a",
        Number:                "#6b5ca5",
        Generic.Deleted:       "#a63a30",
        Generic.Inserted:      "#4e7a45",
        Generic.Heading:       "bold #3e6b59",
        Generic.Subheading:    "#8a6d3b",
        Generic.Emph:          "italic",
        Generic.Strong:        "bold",
    }


class InkwellDarkStyle(Style):
    """低饱和暖中性的夜间代码配色，避免大面积蓝色造成视觉疲劳。"""

    background_color = "#23221d"
    highlight_color = "#36332b"
    styles = {
        Text:                  "#d6d2c6",
        Text.Whitespace:       "#5e5b52",
        Error:                 "#df8585",
        Comment:               "italic #8f8b7e",
        Keyword:               "#d6a56f",
        Keyword.Constant:      "#c6a7cf",
        Keyword.Type:          "#cfbd82",
        Operator:              "#bdb9ae",
        Operator.Word:         "#d6a56f",
        Punctuation:           "#aaa79f",
        Name:                  "#d6d2c6",
        Name.Builtin:          "#cfbd82",
        Name.Class:            "bold #d4bf83",
        Name.Constant:         "#c6a7cf",
        Name.Decorator:        "#c6a7cf",
        Name.Exception:        "#d4bf83",
        Name.Function:         "bold #aac18d",
        Name.Tag:              "#aac18d",
        Name.Variable:         "#d6d2c6",
        Literal:               "#d6d2c6",
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
