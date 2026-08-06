#!/usr/bin/env python3
"""无 GUI：用 Node 不可用时以纯 Python 复刻图片边界查找关键规则，验证内嵌边界。"""

import re
import sys


def find_protected_ranges(text: str):
    """与 app.js findProtectedRanges 同构的简化版（围栏 + 行内代码）。"""
    ranges = []
    n = len(text)
    i = 0
    while i < n:
        if i == 0 or text[i - 1] == "\n":
            j = i
            spaces = 0
            while spaces < 3 and j < n and text[j] == " ":
                spaces += 1
                j += 1
            ch = text[j] if j < n else ""
            if ch in ("`", "~"):
                marker = ch
                open_len = 0
                while j < n and text[j] == marker:
                    open_len += 1
                    j += 1
                if open_len >= 3:
                    while j < n and text[j] != "\n":
                        j += 1
                    if j < n and text[j] == "\n":
                        j += 1
                    closed = False
                    while j < n:
                        line_start = j
                        ls = 0
                        while ls < 3 and j < n and text[j] == " ":
                            ls += 1
                            j += 1
                        close_len = 0
                        while j < n and text[j] == marker:
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
                                ranges.append((i, k))
                                i = k
                                closed = True
                                break
                        j = line_start
                        while j < n and text[j] != "\n":
                            j += 1
                        if j < n and text[j] == "\n":
                            j += 1
                    if closed:
                        continue
                    ranges.append((i, n))
                    break
        if text[i] == "`":
            ticks = 0
            t = i
            while t < n and text[t] == "`":
                ticks += 1
                t += 1
            if ticks > 0:
                needle = "`" * ticks
                close = text.find(needle, t)
                if close != -1:
                    mid = text[t:close]
                    if "\n" not in mid:
                        ranges.append((i, close + ticks))
                        i = close + ticks
                        continue
        i += 1
    return ranges


def in_protected(ranges, pos):
    for a, b in ranges:
        if a <= pos < b:
            return True
    return False


def find_image_at(text: str, pos: int):
    ranges = find_protected_ranges(text)
    for m in re.finditer(r"!\[([^\]]*)\]\(", text):
        start = m.start()
        dest_start = m.end()
        if in_protected(ranges, start):
            continue
        depth = 1
        p = dest_start
        in_angle = False
        quote = None
        while p < len(text) and depth > 0:
            c = text[p]
            if quote:
                if c == quote:
                    quote = None
                p += 1
                continue
            if in_angle:
                if c == ">":
                    in_angle = False
                p += 1
                continue
            if c == "<":
                in_angle = True
                p += 1
                continue
            if c in ('"', "'"):
                quote = c
                p += 1
                continue
            if c == "(":
                depth += 1
                p += 1
                continue
            if c == ")":
                depth -= 1
                p += 1
                if depth == 0:
                    break
                continue
            if c == "\n" and depth == 1 and not quote and not in_angle:
                break
            p += 1
        if depth != 0:
            continue
        end = p
        if start <= pos <= end:
            return {
                "start": start,
                "end": end,
                "alt": m.group(1),
                "dest": text[dest_start:end - 1],
                "markdown": text[start:end],
            }
    return None


def main():
    # 1) 普通相对路径
    t1 = "hello\n\n![cat](images/a.png)\n\nend\n"
    hit = find_image_at(t1, t1.index("!["))
    assert hit and hit["dest"] == "images/a.png", hit

    # 2) data URI 内嵌（含 base64 中的 +/=，无裸括号）
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    t2 = f"x\n\n![shot](data:image/png;base64,{b64})\n\ny\n"
    hit = find_image_at(t2, t2.index("data:"))
    assert hit and hit["dest"].startswith("data:image/png;base64,"), hit
    assert hit["markdown"].endswith(")")

    # 3) 代码围栏内的伪图片不可删除
    t3 = "```\n![nope](x.png)\n```\n\n![yes](y.png)\n"
    fake_pos = t3.index("![nope]")
    real_pos = t3.index("![yes]")
    assert find_image_at(t3, fake_pos) is None
    hit = find_image_at(t3, real_pos)
    assert hit and hit["dest"] == "y.png"

    # 4) 行内代码内伪图片
    t4 = "use `![a](b.png)` then ![c](d.png)"
    assert find_image_at(t4, t4.index("![a]")) is None
    hit = find_image_at(t4, t4.index("![c]"))
    assert hit and hit["dest"] == "d.png"

    # 5) title 引号
    t5 = '![t](foo.png "my title")'
    hit = find_image_at(t5, 0)
    assert hit and 'foo.png "my title"' in hit["dest"]

    # 6) 删除后源码干净
    t6 = "a\n\n![x](images/x.png)\n\nb\n"
    hit = find_image_at(t6, t6.index("!["))
    line_start = t6.rfind("\n", 0, hit["start"]) + 1
    line_end = t6.find("\n", hit["end"])
    line = t6[line_start:line_end]
    only = line.strip() == hit["markdown"]
    assert only
    del_start = line_start
    del_end = line_end + 1
    out = t6[:del_start] + t6[del_end:]
    assert "![x]" not in out
    assert "a\n" in out and "b\n" in out

    print("EDIT IMAGE BOUNDS VERIFY PASS")


if __name__ == "__main__":
    main()
