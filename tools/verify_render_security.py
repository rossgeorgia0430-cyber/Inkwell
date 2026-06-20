#!/usr/bin/env python3
"""无需 WebView2 的渲染安全与围栏语法回归检查。"""

import os
import sys
import re
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inkwell.page import build_page
from inkwell.render import protect_code_blocks, render_markdown


def check_security():
    markdown = r"""
# 安全标题 <img src="x" onerror="alert(1)">

<script>window.pywebview.api.render_path("secret")</script>
<style>body { display: none }</style>
<iframe src="https://example.com"></iframe>

<a href="javascript:alert(1)" onclick="alert(2)" style="color:red">危险链接</a>
<img src="data:text/html;base64,PHNjcmlwdD4=" onerror="alert(3)">

<details open><summary>保留安全 HTML</summary><strong>正文</strong></details>

$x</span><img src=x onerror=alert(4)>$
"""
    html, toc = render_markdown(markdown)
    lowered = (html + toc).lower()
    for forbidden in ("<script", "<style", "<iframe", "onclick=",
                      "javascript:", "data:text/html", "window.pywebview"):
        assert forbidden not in lowered, (forbidden, html, toc)
    assert not re.search(r"<(?:img|a)\b[^>]*\bonerror\s*=", lowered)
    assert "<details open>" in html and "<strong>正文</strong>" in html
    assert "data-copy-action=\"latex\"" not in html  # 行内公式没有复制按钮
    assert "&lt;/span&gt;&lt;img" in html

    malformed, _ = render_markdown(
        '<xmp><script>window.pywebview.api.render_path("xmp")</script></xmp>'
        '<plaintext><img src=x onerror=alert(9)>')
    assert "<script" not in malformed.lower()
    assert "onerror=" not in malformed.lower()

    block, _ = render_markdown(r"$$x</div><script>alert(1)</script>$$")
    assert "onclick=" not in block
    assert 'data-copy-action="latex"' in block
    assert "<script" not in block
    assert "&lt;/div&gt;&lt;script&gt;" in block


def check_fences_and_images():
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "pixel.png"
        image.write_bytes(b"not-a-real-image-is-fine-for-localization")
        markdown = """````markdown
![代码中的图片](pixel.png)
```
`````

![正文图片](pixel.png)
"""
        html, _ = render_markdown(markdown, tmp)
        assert html.count("/__img__/") == 1, html
        assert "code-block-wrapper" in html
        assert "代码中的图片" in html
        assert 'data-copy-action="code"' in html
        assert "onclick=" not in html

    protected, blocks = protect_code_blocks("~~~~text\na\n~~~\nb\n~~~~~~")
    assert len(blocks) == 1 and "data-codeblk" in protected
    assert "~~~" in next(iter(blocks.values()))

    # 短于 opening run 的围栏不能闭合。
    protected, blocks = protect_code_blocks("````python\nx = 1\n```")
    assert not blocks and protected.startswith("````python")


def check_page_sentinels():
    page = build_page("<p>__TOC__ __CONTENT__</p>", "<b>UNIQUE_TOC</b>", "sentinel")
    assert page.count("UNIQUE_TOC") == 1
    assert "<p>__TOC__ __CONTENT__</p>" in page

    hostile = '</script><script>window.pywebview.api.render_path("secret")</script>\u2028&<>'
    page = build_page("<p>safe</p>", "", hostile, hostile, {"font": hostile})
    assert page.count("<script") == 5  # 3 inline + 2 external scripts
    assert "</script><script>window.pywebview" not in page
    assert "\\u003c/script\\u003e" in page
    boot_script = re.search(r"window\.__BOOT__ = (.*?);</script>", page).group(1)
    assert "\\u2028" in boot_script and "\u2028" not in boot_script
    assert "Content-Security-Policy" in page and "script-src 'self' 'nonce-" in page


def main():
    check_security()
    check_fences_and_images()
    check_page_sentinels()
    print("render security: PASS")


if __name__ == "__main__":
    main()
