#!/usr/bin/env python3
"""真实 WebView2 回归：Mermaid 图示/源码切换、主题刷新与 SVG 安全收口。"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview

from inkwell import render as R
from inkwell import server as S
from inkwell.app import Api
from inkwell.page import build_page


SAMPLE = """# Mermaid 回归

```mermaid
flowchart TB
    A[\"任意 Client / Agent\"] --> B[\"Compact Front Door\"]
    B --> C[\"Policy Gate\"]
    C --> D[\"Native Write Broker\"]
    D --> E[\"Operation Ledger\"]
```
"""


def wait_js(window, expression, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if window.evaluate_js(expression):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def probe(window):
    result = {"stage": "start"}
    try:
        result["stage"] = "wait-shell"
        assert wait_js(window, "!!(window.__ink && document.querySelector('.mermaid-block'))")
        result["stage"] = "wait-diagram"
        assert wait_js(window, "!!document.querySelector('.mermaid-block.is-diagram .mermaid-diagram svg')")

        result["stage"] = "initial-state"
        result["initial"] = json.loads(window.evaluate_js("JSON.stringify(window.__ink.mermaid.state())"))
        assert result["initial"]["diagram"] and result["initial"]["rendered"]
        assert result["initial"]["source"]
        assert window.evaluate_js("document.querySelector('.mermaid-toggle-label').textContent") == "原始代码"
        assert "flowchart TB" in window.evaluate_js(
            "document.querySelector('.mermaid-block pre').textContent"
        )
        diagram_text = window.evaluate_js("document.querySelector('.mermaid-diagram svg').textContent")
        assert "Client" in diagram_text and "Native" in diagram_text
        result["initial_label_containers"] = window.evaluate_js(
            "document.querySelectorAll('.mermaid-diagram foreignObject').length"
        )
        assert result["initial_label_containers"] > 0

        # 图示 -> 原始代码 -> 图示：源码必须仍在 DOM 中，复制按钮才能始终复制原文。
        result["stage"] = "source-toggle"
        window.evaluate_js("document.querySelector('.mermaid-toggle-btn').click()")
        assert wait_js(window, "!document.querySelector('.mermaid-block').classList.contains('is-diagram')")
        result["source_view"] = json.loads(window.evaluate_js("JSON.stringify(window.__ink.mermaid.state())"))
        assert not result["source_view"]["diagram"] and result["source_view"]["source"]
        assert window.evaluate_js("document.querySelector('.mermaid-toggle-label').textContent") == "图示"

        result["stage"] = "diagram-toggle"
        window.evaluate_js("document.querySelector('.mermaid-toggle-btn').click()")
        assert wait_js(window, "!!document.querySelector('.mermaid-block.is-diagram .mermaid-diagram svg')")

        # 切换到深色主题会重绘当前图示。必须等待本次异步渲染完成，
        # 不能只看到上一帧 SVG：Mermaid v11 的节点标签位于 foreignObject 中。
        result["stage"] = "theme-refresh"
        if window.evaluate_js("document.documentElement.getAttribute('data-theme')") == "dark":
            window.evaluate_js("document.getElementById('themeBtn').click()")
            assert wait_js(
                window,
                "document.querySelector('.mermaid-block').getAttribute('data-mermaid-rendered-theme') === 'neutral'",
            )
        window.evaluate_js("document.getElementById('themeBtn').click()")
        assert wait_js(window, "document.documentElement.getAttribute('data-theme') === 'dark'")
        assert wait_js(
            window,
            "document.querySelector('.mermaid-block').getAttribute('data-mermaid-rendered-theme') === 'dark'",
        )
        result["dark_visible_labels"] = window.evaluate_js(
            "Array.prototype.filter.call(document.querySelectorAll('.mermaid-diagram foreignObject'), "
            "function(node) { var box = node.getBoundingClientRect(); return (node.textContent || '').trim() && "
            "box.width > 0 && box.height > 0; }).length"
        )
        assert result["dark_visible_labels"] > 0
        dark_diagram_text = window.evaluate_js("document.querySelector('.mermaid-diagram svg').textContent")
        assert "Client" in dark_diagram_text and "Native" in dark_diagram_text

        # 输出 SVG 中不能残留可执行或可导航节点。
        result["stage"] = "svg-safety"
        result["unsafe_svg_nodes"] = window.evaluate_js(
            "document.querySelectorAll('.mermaid-diagram script, "
            ".mermaid-diagram iframe').length + Array.prototype.filter.call("
            "document.querySelectorAll('.mermaid-diagram *'), function(n) { return "
            "n.hasAttribute('onclick') || n.hasAttribute('href') || n.hasAttribute('xlink:href'); }).length"
        )
        assert result["unsafe_svg_nodes"] == 0
        result["errors"] = window.evaluate_js("JSON.stringify(window.__errors || [])")
        assert json.loads(result["errors"]) == []
        result["stage"] = "done"
        result["ok"] = True
    except Exception as exc:
        result["ok"] = False
        result["error"] = repr(exc)
    finally:
        if not result.get("ok"):
            try:
                result["debug"] = json.loads(window.evaluate_js(
                    "JSON.stringify({"
                    "errors: window.__errors || [], mermaid: !!window.mermaid, "
                    "block: (function(){var b=document.querySelector('.mermaid-block'); return b ? {"
                    "className:b.className, error:b.getAttribute('data-mermaid-error'), "
                    "rendering:b.getAttribute('data-mermaid-rendering'), "
                    "rendered:b.getAttribute('data-mermaid-rendered-theme')} : null})()"
                    "})"
                ))
            except Exception as debug_exc:
                result["debug_error"] = repr(debug_exc)
        print(json.dumps(result, ensure_ascii=False))
        window.destroy()


def main():
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if source_path and source_path.is_file():
        markdown = source_path.read_text(encoding="utf-8")
        title = source_path.name
        base_dir = str(source_path.parent)
    else:
        markdown = SAMPLE
        title = "Mermaid 回归"
        base_dir = os.path.dirname(os.path.abspath(__file__))

    content, toc = R.render_markdown(markdown, base_dir=base_dir)
    assert "mermaid-block" in content and "data-mermaid-source" in content
    S.set_page(build_page(content, toc, title))
    httpd, url = S.start_server()
    try:
        window = webview.create_window(
            title="Inkwell Mermaid Verify", url=url, width=1180, height=820,
            frameless=True, easy_drag=False, text_select=True,
        )
        webview.start(probe, window, gui="edgechromium", private_mode=True)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
