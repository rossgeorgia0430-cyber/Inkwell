#!/usr/bin/env python3
"""主题配色回归：读取两种主题下关键元素的计算样式，确认新色板真正生效。

（截图在某些会话里抓不到窗口；计算样式是客观、可断言的验证。）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview

from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_theme_result.json")

SAMPLE = """# 主题校验

正文 [链接](https://example.com) 与 `code`。

```python
import os
def f(x):
    return x + 1  # comment
```
"""

SNAPSHOT_JS = r"""
(function(){
  function cs(sel, prop){ var el = document.querySelector(sel); return el ? getComputedStyle(el)[prop] : null; }
  function v(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  return JSON.stringify({
    theme: document.documentElement.getAttribute('data-theme'),
    bg: v('--bg'), text: v('--text'), accent: v('--accent'), codeBg: v('--code-bg'),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    articleColor: cs('.article', 'color'),
    linkColor: cs('.article a', 'color'),
    inlineCodeBg: cs('.article code', 'backgroundColor'),
    kwColor: cs('.codehilite .k', 'color'),
    strColor: cs('.codehilite .s, .codehilite .s2', 'color'),
    brandFont: cs('.brand', 'fontFamily'),
    pygmentsSheet: (document.getElementById('pygments-style')||{}).href
  });
})()
"""

EXPECT = {
    "light": {
        "bg": "#f7f5ef", "text": "#33302a", "accent": "#3f6e5b",
        "bodyBg": "rgb(247, 245, 239)",
        "linkColor": "rgb(49, 90, 73)",       # accent-hover #315a49
        "kwColor": "rgb(62, 107, 89)",        # 关键字松绿
    },
    "dark": {
        "bg": "#1e1d1a", "text": "#d5d1c5", "accent": "#c9a45f",
        "bodyBg": "rgb(30, 29, 26)",
        "linkColor": "rgb(220, 185, 120)",    # accent-hover #dcb978
        "kwColor": "rgb(214, 165, 111)",      # 关键字金 #d6a56f
    },
}


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
        assert wait_js(window, "!!(window.__ink && document.querySelector('.codehilite .k'))", 40)
        light = json.loads(window.evaluate_js(SNAPSHOT_JS))
        result["light"] = light
        window.evaluate_js("document.getElementById('themeBtn').click()")
        assert wait_js(window, "document.documentElement.getAttribute('data-theme') === 'dark'")
        time.sleep(0.3)   # 等 pygments-dark.css 换上
        dark = json.loads(window.evaluate_js(SNAPSHOT_JS))
        result["dark"] = dark

        problems = []
        for theme, snap in (("light", light), ("dark", dark)):
            exp = EXPECT[theme]
            if snap["theme"] != theme:
                problems.append("%s: theme attr %r" % (theme, snap["theme"]))
            for key in ("bg", "text", "accent", "bodyBg", "linkColor", "kwColor"):
                if snap.get(key) != exp[key]:
                    problems.append("%s.%s: got %r want %r" % (theme, key, snap.get(key), exp[key]))
        if "Georgia" not in (light["brandFont"] or ""):
            problems.append("brand font missing Georgia: %r" % light["brandFont"])
        if "pygments-dark" not in (dark["pygmentsSheet"] or "") and "pygments-dark" not in (dark.get("pygmentsSheet") or ""):
            problems.append("dark pygments sheet not swapped: %r" % dark.get("pygmentsSheet"))
        result["problems"] = problems
        assert not problems, problems
        result["errors"] = window.evaluate_js("JSON.stringify(window.__errors || [])")
        assert json.loads(result["errors"]) == []
        result["stage"] = "done"
        result["ok"] = True
    except Exception as exc:
        result["ok"] = False
        result["error"] = repr(exc)
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        window.destroy()


def main():
    content, toc = R.render_markdown(SAMPLE, base_dir=HERE)
    S.set_page(build_page(content, toc, "主题校验"))
    httpd, url = S.start_server()
    try:
        window = webview.create_window(
            title="Inkwell Theme Verify", url=url, width=900, height=700,
            frameless=True, easy_drag=False, text_select=True,
        )
        webview.start(probe, window, gui="edgechromium", private_mode=True)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
