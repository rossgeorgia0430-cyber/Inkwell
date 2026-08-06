#!/usr/bin/env python3
"""验证 Live Preview 块切分逻辑（与 app.js splitMarkdownBlocks 同构）。"""

import re
import sys


def match_fence_end(text: str, pos: int):
    if pos != 0 and text[pos - 1] != "\n":
        return None
    n = len(text)
    j = pos
    spaces = 0
    while spaces < 3 and j < n and text[j] == " ":
        spaces += 1
        j += 1
    if j >= n or text[j] not in "`~":
        return None
    ch = text[j]
    open_len = 0
    while j < n and text[j] == ch:
        open_len += 1
        j += 1
    if open_len < 3:
        return None
    while j < n and text[j] != "\n":
        j += 1
    if j < n and text[j] == "\n":
        j += 1
    while j < n:
        line_start = j
        ls = 0
        while ls < 3 and j < n and text[j] == " ":
            ls += 1
            j += 1
        close_len = 0
        while j < n and text[j] == ch:
            close_len += 1
            j += 1
        if close_len >= open_len:
            k = j
            while k < n and text[k] in " \t":
                k += 1
            if k >= n or text[k] == "\n":
                while k < n and text[k] != "\n":
                    k += 1
                if k < n and text[k] == "\n":
                    k += 1
                return k
        j = line_start
        while j < n and text[j] != "\n":
            j += 1
        if j < n and text[j] == "\n":
            j += 1
    return n


def match_math_end(text: str, pos: int):
    if pos != 0 and text[pos - 1] != "\n":
        return None
    n = len(text)
    j = pos
    while j < n and text[j] in " \t":
        j += 1
    if text[j : j + 2] != "$$":
        return None
    j += 2
    line_end = j
    while line_end < n and text[line_end] != "\n":
        line_end += 1
    after = text[j:line_end].rstrip()
    if len(after) >= 2 and after.endswith("$$"):
        end = line_end
        if end < n and text[end] == "\n":
            end += 1
        return end
    if line_end < n and text[line_end] == "\n":
        j = line_end + 1
    else:
        j = line_end
    while j < n:
        ls = j
        while j < n and text[j] != "\n":
            j += 1
        line = text[ls:j].strip()
        if line == "$$":
            if j < n and text[j] == "\n":
                j += 1
            return j
        if j < n and text[j] == "\n":
            j += 1
    return n


def split_markdown_blocks(text: str):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    n = len(text)
    blocks = []
    i = 0
    while i < n:
        while i < n and text[i] == "\n":
            i += 1
        if i >= n:
            break
        start = i
        fence_end = match_fence_end(text, i)
        if fence_end is not None:
            blocks.append(text[start:fence_end].rstrip("\n"))
            i = fence_end
            continue
        math_end = match_math_end(text, i)
        if math_end is not None:
            blocks.append(text[start:math_end].rstrip("\n"))
            i = math_end
            continue
        j = i
        while j < n:
            if text[j] == "\n":
                k = j + 1
                if k >= n or text[k] == "\n":
                    break
                if match_fence_end(text, k) is not None or match_math_end(text, k) is not None:
                    break
            j += 1
        blocks.append(text[start:j].rstrip("\n"))
        i = j
        while i < n and text[i] == "\n":
            i += 1
    if not blocks:
        blocks.append("")
    return blocks


def join_blocks(blocks):
    return "\n\n".join(blocks)


def main():
    samples = [
        ("", [""]),
        ("hello", ["hello"]),
        ("# Title\n\npara one\n\npara two", ["# Title", "para one", "para two"]),
        (
            "before\n\n```python\nprint(1)\n\nprint(2)\n```\n\nafter",
            ["before", "```python\nprint(1)\n\nprint(2)\n```", "after"],
        ),
        (
            "p\n\n$$\nE=mc^2\n$$\n\nq",
            ["p", "$$\nE=mc^2\n$$", "q"],
        ),
        (
            "$$x^2$$\n\nnext",
            ["$$x^2$$", "next"],
        ),
        (
            "```mermaid\nflowchart LR\n  A-->B\n```\n\ntext",
            ["```mermaid\nflowchart LR\n  A-->B\n```", "text"],
        ),
        (
            "- a\n- b\n\npara",
            ["- a\n- b", "para"],
        ),
    ]

    for src, expected in samples:
        got = split_markdown_blocks(src)
        assert got == expected, f"\nSRC={src!r}\nGOT={got!r}\nEXP={expected!r}"

    # round-trip join is lossy on extra blank lines but stable for normal docs
    doc = "# A\n\nHello **world**\n\n```js\n1\n\n2\n```\n\n$$\n1+1\n$$\n"
    blocks = split_markdown_blocks(doc)
    assert len(blocks) == 4
    rejoined = join_blocks(blocks)
    assert split_markdown_blocks(rejoined) == blocks

    # marker split simulation
    marked_parts = []
    for i, b in enumerate(blocks):
        if i:
            marked_parts.append(f"\n\n<!--inkwell-lp-block:{i}-->\n\n")
        marked_parts.append(b)
    marked = "".join(marked_parts)
    # fake HTML with comments preserved
    fake_html = (
        "<h1>A</h1>"
        "<!--inkwell-lp-block:1-->"
        "<p>Hello</p>"
        "<!--inkwell-lp-block:2-->"
        "<pre>code</pre>"
        "<!--inkwell-lp-block:3-->"
        "<div class='math'>eq</div>"
    )
    parts = re.split(r"<!--inkwell-lp-block:\d+-->", fake_html)
    assert len(parts) == 4
    assert "Hello" in parts[1]

    # fence must not split on internal blank lines
    fence_doc = "```\na\n\nb\n\nc\n```"
    assert split_markdown_blocks(fence_doc) == ["```\na\n\nb\n\nc\n```"]

    # 紧贴围栏（无空行）
    assert split_markdown_blocks("before\n```\nx\n```") == ["before", "```\nx\n```"]
    # 波浪围栏 + 缩进
    assert split_markdown_blocks("   ~~~\na\n   ~~~") == ["   ~~~\na\n   ~~~"]
    # 未闭合围栏吞到文末
    assert split_markdown_blocks("```\nnope") == ["```\nnope"]
    # CRLF
    assert split_markdown_blocks("# A\r\n\r\nB") == ["# A", "B"]
    # 未闭合公式
    assert split_markdown_blocks("$$\nE=mc^2") == ["$$\nE=mc^2"]

    print("verify_live_blocks: PASS")
    return 0



if __name__ == "__main__":
    sys.exit(main())
