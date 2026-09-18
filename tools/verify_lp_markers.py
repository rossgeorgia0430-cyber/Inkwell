#!/usr/bin/env python3
"""验证 Live Preview 块分隔注释能穿过 render / sanitize 管线。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inkwell.render import render_markdown
from inkwell.sanitize import sanitize_html


def main():
    md = (
        "# Title\n\n"
        "<!--inkwell-lp-block:1-->\n\n"
        "Hello **world**\n\n"
        "<!--inkwell-lp-block:2-->\n\n"
        "```python\nprint(1)\n```\n\n"
        "<!--inkwell-lp-block:3-->\n\n"
        "$$\nE=mc^2\n$$\n"
    )
    html, toc = render_markdown(md)
    for i in (1, 2, 3):
        assert f"inkwell-lp-block:{i}" in html, (i, html[:500])
    assert "evil" not in sanitize_html("a<!--evil script-->b")
    assert "inkwell-lp-block:9" in sanitize_html("x<!--inkwell-lp-block:9-->y")
    # 分片
    parts = __import__("re").split(r"<!--inkwell-lp-block:\d+-->", html)
    assert len(parts) == 4, len(parts)
    assert "Title" in parts[0] or "title" in parts[0].lower() or "h1" in parts[0].lower()
    assert "world" in parts[1]
    assert "print" in parts[2] or "python" in parts[2]
    print("verify_lp_markers: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
