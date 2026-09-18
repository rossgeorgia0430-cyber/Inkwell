#!/usr/bin/env python3
"""真实 WebView2 回归：Mermaid 图示灯箱（打开/缩放/右键平移/关闭）+ 图片灯箱回归。

覆盖本轮新增：
  - Mermaid 头部「放大」按钮与点击图示打开灯箱（kind='svg'，小图自动 fit 放大）
  - 灯箱内 Ctrl 级缩放（zoom 按钮）与右键按住拖动平移（图片与 SVG 通用）
  - 灯箱内右键菜单被抑制
  - 图片灯箱在通用化重构后仍正常（kind='image'、缩放、平移）
"""
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview

from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_viewer_result.json")


def make_sample():
    from PIL import Image
    img = Image.new("RGB", (900, 560), (63, 110, 91))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return """# 灯箱回归

```mermaid
flowchart LR
    A[开始] --> B{判断}
    B -->|是| C[执行]
    B -->|否| D[跳过]
    C --> E[结束]
    D --> E
```

## 图片

![样图](data:image/png;base64,%s)
""" % b64


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


def js(window, expression):
    return window.evaluate_js(expression)


def zoom_until_overflow(window, tries=8):
    """反复放大直到内容在两个方向都溢出舞台，供右键平移测试使用；
    单次固定次数的 zoom 不保证真的溢出（内容宽高比、fit 起点各不相同）。"""
    cur = None
    for _ in range(tries):
        cur = json.loads(js(window, "JSON.stringify(window.__ink.image.state())"))
        if cur["width"] > cur["stageWidth"] * 1.2 and cur["height"] > cur["stageHeight"] * 1.2:
            break
        js(window, "window.__ink.image.zoom(1)")
        time.sleep(0.15)
    return cur


def probe(window):
    result = {"stage": "start"}
    try:
        result["stage"] = "wait-shell"
        assert wait_js(window, "!!(window.__ink && document.querySelector('.mermaid-block.is-diagram .mermaid-diagram svg'))", 40)

        # ---- 1) Mermaid：头部「放大」按钮存在，点击打开灯箱 ----
        result["stage"] = "mermaid-zoom-open"
        result["zoom_btn"] = js(window, "!!document.querySelector('.mermaid-zoom-btn[data-mermaid-action=\"zoom\"]')")
        assert result["zoom_btn"]
        js(window, "document.querySelector('.mermaid-zoom-btn').click()")
        assert wait_js(window, "window.__ink.image.state().open && window.__ink.image.state().kind === 'svg'"
                               " && window.__ink.image.state().scale !== 1")
        st = json.loads(js(window, "JSON.stringify(window.__ink.image.state())"))
        result["mermaid_open"] = st
        assert st["width"] > 0 and st["height"] > 0
        # 小图示在灯箱内应按 fit 自动放大（解决「默认很小」）
        assert st["fit"] > 1, "small diagram should be upscaled to fit, got %r" % st["fit"]
        assert js(window, "!!document.querySelector('.image-viewer-stage svg.image-viewer-svg')")

        # ---- 2) SVG 缩放：zoom-in 后尺寸增大 ----
        result["stage"] = "mermaid-zoom-scale"
        w0 = st["width"]
        js(window, "window.__ink.image.zoom(1)")
        time.sleep(0.2)
        st2 = json.loads(js(window, "JSON.stringify(window.__ink.image.state())"))
        result["mermaid_zoomed"] = st2
        assert st2["width"] > w0, "zoom-in should enlarge svg"

        # ---- 3) 右键拖动平移（SVG）----
        result["stage"] = "mermaid-pan"
        zoom_until_overflow(window)
        pan = js(window, r"""
(function(){
  var stage = document.querySelector('.image-viewer-stage');
  var r = stage.getBoundingClientRect();
  var cx = r.left + r.width/2, cy = r.top + r.height/2;
  var before = { left: stage.scrollLeft, top: stage.scrollTop };
  stage.dispatchEvent(new MouseEvent('mousedown', {button: 2, clientX: cx, clientY: cy, bubbles: true, cancelable: true}));
  window.dispatchEvent(new MouseEvent('mousemove', {clientX: cx + 140, clientY: cy + 90, bubbles: true, cancelable: true}));
  var during = { left: stage.scrollLeft, top: stage.scrollTop,
                 panning: window.__ink.image.state().panning,
                 cursorClass: stage.classList.contains('panning') };
  window.dispatchEvent(new MouseEvent('mouseup', {button: 2, clientX: cx + 140, clientY: cy + 90, bubbles: true, cancelable: true}));
  var after = { left: stage.scrollLeft, top: stage.scrollTop,
                panning: window.__ink.image.state().panning,
                cursorClass: stage.classList.contains('panning') };
  return JSON.stringify({ before: before, during: during, after: after });
})()
""")
        pan = json.loads(pan)
        result["mermaid_pan"] = pan
        assert pan["during"]["panning"] and pan["during"]["cursorClass"]
        assert abs(pan["during"]["left"] - (pan["before"]["left"] - 140)) < 2, "drag right should decrease scrollLeft"
        assert abs(pan["during"]["top"] - (pan["before"]["top"] - 90)) < 2, "drag down should decrease scrollTop"
        assert not pan["after"]["panning"] and not pan["after"]["cursorClass"]

        # ---- 4) 灯箱内右键菜单被抑制 ----
        result["stage"] = "contextmenu-suppressed"
        result["contextmenu_prevented"] = js(window, r"""
(function(){
  var ev = new MouseEvent('contextmenu', {bubbles: true, cancelable: true});
  document.querySelector('.image-viewer').dispatchEvent(ev);
  return ev.defaultPrevented;
})()
""")
        assert result["contextmenu_prevented"]

        # 截图：SVG 灯箱开着的状态
        try:
            from PIL import ImageGrab
            time.sleep(0.3)
            ImageGrab.grab().save(os.path.join(HERE, "_viewer_mermaid.png"))
            result["shot_mermaid"] = True
        except Exception as exc:
            result["shot_mermaid_err"] = repr(exc)

        js(window, "window.__ink.image.close()")
        assert wait_js(window, "!window.__ink.image.state().open")

        # ---- 5) 图片灯箱回归：kind='image'、缩放、右键平移 ----
        result["stage"] = "image-viewer"
        js(window, "document.querySelector('.image-block img').click()")
        # 用 scale === fit 而非 scale !== 1：close() 不重置 scale，上一次灯箱（Mermaid）
        # 残留的高倍率会让 !== 1 在这次 fit 真正落地前就提前满足。
        assert wait_js(window, "window.__ink.image.state().open && window.__ink.image.state().kind === 'image'"
                               " && window.__ink.image.state().scale === window.__ink.image.state().fit")
        ist = json.loads(js(window, "JSON.stringify(window.__ink.image.state())"))
        result["image_open"] = ist
        assert ist["width"] > 0
        # 图片已 900x560，fit 后可能未溢出；放大到两个方向都溢出舞台后再平移
        zoom_until_overflow(window)
        ipan = js(window, r"""
(function(){
  var stage = document.querySelector('.image-viewer-stage');
  var r = stage.getBoundingClientRect();
  var cx = r.left + r.width/2, cy = r.top + r.height/2;
  var before = { left: stage.scrollLeft, top: stage.scrollTop };
  stage.dispatchEvent(new MouseEvent('mousedown', {button: 2, clientX: cx, clientY: cy, bubbles: true, cancelable: true}));
  window.dispatchEvent(new MouseEvent('mousemove', {clientX: cx - 100, clientY: cy - 60, bubbles: true, cancelable: true}));
  var during = { left: stage.scrollLeft, top: stage.scrollTop, panning: window.__ink.image.state().panning };
  window.dispatchEvent(new MouseEvent('mouseup', {button: 2, clientX: cx - 100, clientY: cy - 60, bubbles: true, cancelable: true}));
  return JSON.stringify({ before: before, during: during,
                          after: { panning: window.__ink.image.state().panning } });
})()
""")
        ipan = json.loads(ipan)
        result["image_pan"] = ipan
        assert ipan["during"]["panning"]
        assert abs(ipan["during"]["left"] - (ipan["before"]["left"] + 100)) < 2, "drag left should increase scrollLeft"
        assert not ipan["after"]["panning"]
        js(window, "window.__ink.image.close()")
        assert wait_js(window, "!window.__ink.image.state().open")

        # ---- 6) 点击正文图示也能打开灯箱 ----
        result["stage"] = "diagram-click-open"
        js(window, "document.querySelector('.mermaid-diagram').click()")
        # 同上：上一次（图片）灯箱残留的高倍率同样会让 !== 1 提前满足。
        assert wait_js(window, "window.__ink.image.state().open && window.__ink.image.state().kind === 'svg'"
                               " && window.__ink.image.state().scale === window.__ink.image.state().fit")
        js(window, "window.__ink.image.close()")

        result["errors"] = js(window, "JSON.stringify(window.__errors || [])")
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
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if source_path and source_path.is_file():
        markdown = source_path.read_text(encoding="utf-8")
        title = source_path.name
    else:
        markdown = make_sample()
        title = "灯箱回归"
    content, toc = R.render_markdown(markdown, base_dir=HERE)
    assert "mermaid-block" in content and "image-block" not in content  # image-block 是前端运行时包装的
    S.set_page(build_page(content, toc, title))
    httpd, url = S.start_server()
    try:
        window = webview.create_window(
            title="Inkwell Viewer Verify", url=url, width=1180, height=820,
            frameless=True, easy_drag=False, text_select=True,
        )
        webview.start(probe, window, gui="edgechromium", private_mode=True)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
