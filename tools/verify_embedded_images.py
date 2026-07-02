#!/usr/bin/env python3
"""无需 WebView2 的内嵌图片（data: URI）本地化回归检查。"""

import os
import sys
import base64
import hashlib
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inkwell import render
from inkwell.render import render_markdown

# 1x1 像素的合法 PNG。
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()

_SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
_SVG_B64 = base64.b64encode(_SVG_BYTES).decode()


def _asset_bytes(url):
    """从 /__img__/<name> URL 反查落盘文件并读取字节。"""
    assert url.startswith(render.IMG_URL_PREFIX), url
    name = url[len(render.IMG_URL_PREFIX):]
    path = render.ASSETS_DIR / name
    assert path.is_file(), (url, path)
    return path.read_bytes()


def check_inline_data_uri_image():
    md = f"![t](data:image/png;base64,{_PNG_B64})"
    html, _ = render_markdown(md)
    assert render.IMG_URL_PREFIX in html, html
    assert "data:image" not in html.lower(), html
    start = html.index('src="') + len('src="')
    url = html[start:html.index('"', start)]
    assert _asset_bytes(url) == _PNG_BYTES


def check_reference_style_data_uri_image():
    md = f"![t][i]\n\n[i]: data:image/png;base64,{_PNG_B64}\n"
    html, _ = render_markdown(md)
    assert render.IMG_URL_PREFIX in html, html
    assert "data:image" not in html.lower(), html
    start = html.index('src="') + len('src="')
    url = html[start:html.index('"', start)]
    assert _asset_bytes(url) == _PNG_BYTES


def check_html_svg_data_uri_image():
    md = f'<img src="data:image/svg+xml;base64,{_SVG_B64}">'
    html, _ = render_markdown(md)
    assert render.IMG_URL_PREFIX in html, html
    assert "data:image" not in html.lower(), html
    start = html.index('src="') + len('src="')
    url = html[start:html.index('"', start)]
    assert url.endswith(".svg"), url
    assert _asset_bytes(url) == _SVG_BYTES


def check_percent_encoded_svg_data_uri():
    md = (
        '<img src="data:image/svg+xml,'
        '%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E">'
    )
    html, _ = render_markdown(md)
    assert render.IMG_URL_PREFIX in html, html
    assert "data:image" not in html.lower(), html
    start = html.index('src="') + len('src="')
    url = html[start:html.index('"', start)]
    assert url.endswith(".svg"), url


def check_non_image_data_uri_rejected():
    md = '<img src="data:text/html;base64,PHNjcmlwdD4=">'
    html, _ = render_markdown(md)
    assert render.IMG_URL_PREFIX not in html, html
    assert "data:text/html" not in html.lower(), html


def check_malformed_base64_does_not_crash():
    md = '<img src="data:image/png;base64,!!!">'
    html, _ = render_markdown(md)  # 不应抛异常
    # 解码失败：_localize_data_uri 返回 None，不会落盘为 /__img__/；sanitizer
    # 只校验 data URI 的 header 前缀（不解码 payload），所以原始 data URI 会
    # 保留在 src 上——浏览器加载时会显示为一张失效图片，但不会执行任何脚本。
    assert render.IMG_URL_PREFIX not in html, html


def check_srcset_with_data_uri_and_real_file():
    with tempfile.TemporaryDirectory() as tmp:
        other = Path(tmp) / "other.png"
        other.write_bytes(b"not-a-real-image-is-fine-for-localization")
        md = (
            '<img src="x" '
            f'srcset="data:image/png;base64,{_PNG_B64} 1x, other.png 2x">'
        )
        html, _ = render_markdown(md, tmp)
        assert html.count(render.IMG_URL_PREFIX) >= 2, html
        assert "data:image" not in html.lower(), html


def check_inline_code_protection():
    # `$x$` 是行内代码，不应被当成公式。
    html, _ = render_markdown('use `$x$` here')
    assert "<code>" in html and "math-inline" not in html, html

    # `![a](p.png)` 在行内代码里应保持字面文本，不应被本地化。
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "p.png").write_bytes(b"fake-png-bytes")
        html, _ = render_markdown('syntax: `![a](p.png)`', tmp)
        assert render.IMG_URL_PREFIX not in html, html
        assert "<code>" in html and "![a](p.png)" in html, html

        # 同一文档中，行内代码之外的公式和图片应继续正常渲染。
        md = 'inline `![a](p.png)` and code `$x$`, but real: $E=mc^2$ and ![正常图](p.png)'
        html, _ = render_markdown(md, tmp)
        assert "math-inline" in html, html
        assert render.IMG_URL_PREFIX in html, html


def check_inline_code_inside_latex():
    # 回归：公式内容里的反引号曾把 INLINECODE 占位符泄漏进 data-latex/公式体。
    html, _ = render_markdown('math: $$a `b` c$$')
    assert "INLINECODE" not in html, html
    assert 'data-latex="a `b` c"' in html, html

    html, _ = render_markdown('inline $a `b` c$ end')
    assert "INLINECODE" not in html, html
    assert "math-inline" in html, html


def check_uppercase_mime():
    md = f"![t](data:IMAGE/PNG;base64,{_PNG_B64})"
    html, _ = render_markdown(md)
    assert render.IMG_URL_PREFIX in html, html
    assert "data:image" not in html.lower(), html
    start = html.index('src="') + len('src="')
    url = html[start:html.index('"', start)]
    assert url.endswith(".png"), url
    assert _asset_bytes(url) == _PNG_BYTES


def main():
    check_inline_data_uri_image()
    check_reference_style_data_uri_image()
    check_html_svg_data_uri_image()
    check_percent_encoded_svg_data_uri()
    check_non_image_data_uri_rejected()
    check_malformed_base64_does_not_crash()
    check_srcset_with_data_uri_and_real_file()
    check_inline_code_protection()
    check_inline_code_inside_latex()
    check_uppercase_mime()
    print("embedded images: PASS")


if __name__ == "__main__":
    main()
