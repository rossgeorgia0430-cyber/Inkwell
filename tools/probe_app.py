#!/usr/bin/env python3
"""测试真实 app.Api 桥、窗口控制与 __applyPayload 热重载（自动关闭）。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview
from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page
from inkwell.app import Api

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_probe_app_result.json")
SAMPLE = os.path.abspath(os.path.join(HERE, "..", "tests", "sample.md"))
SAMPLE2 = os.path.join(HERE, "_reload2.md")


def probe(window, api):
    res = {}
    try:
        for _ in range(60):
            if window.evaluate_js("document.readyState") == "complete" and \
               window.evaluate_js("document.querySelectorAll('.katex').length") > 0:
                break
            time.sleep(0.25)

        res["api_keys"] = window.evaluate_js(
            "window.pywebview && window.pywebview.api ? Object.keys(window.pywebview.api).sort() : null")
        res["title_before"] = window.evaluate_js("document.getElementById('docTitle').textContent")

        # 热重载：渲染第二个文件并 applyPayload
        with open(SAMPLE2, "w", encoding="utf-8") as f:
            f.write("# 重载后的标题\n\n新的内容 $a^2+b^2=c^2$。\n\n## 小节\n\n```python\nx = 42\n```\n")
        payload = api._render_payload(SAMPLE2)
        window.evaluate_js("window.__applyPayload(%s)" % json.dumps(payload, ensure_ascii=False))
        time.sleep(0.5)
        res["title_after"] = window.evaluate_js("document.getElementById('docTitle').textContent")
        res["after_katex"] = window.evaluate_js("document.querySelectorAll('.katex').length")
        res["after_h1"] = window.evaluate_js(
            "(document.querySelector('#content h1')||{}).textContent")
        res["after_toc"] = window.evaluate_js("document.querySelectorAll('#toc a').length")

        # 窗口控制（不崩即可）
        res["max1"] = api.win_toggle_maximize()
        time.sleep(0.3)
        res["max2"] = api.win_toggle_maximize()
        res["errors"] = window.evaluate_js("(window.__errors||[]).slice(0,10)")
        res["ok"] = True
    except Exception as e:
        import traceback
        res["ok"] = False
        res["error"] = repr(e)
        res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try:
            os.remove(SAMPLE2)
        except OSError:
            pass
        try:
            window.destroy()
        except Exception:
            pass


def main():
    api = Api()
    md = open(SAMPLE, encoding="utf-8").read()
    content, toc = R.render_markdown(md, base_dir=os.path.dirname(SAMPLE))
    api.current_file = SAMPLE
    S.set_page(build_page(content, toc, os.path.basename(SAMPLE)))
    httpd, url = S.start_server()

    window = webview.create_window(
        title="Inkwell", url=url, js_api=api,
        width=1100, height=780, frameless=True, easy_drag=False, text_select=True,
    )
    api._window = window
    webview.start(probe, (window, api), gui="edgechromium", private_mode=True)
    print("APP PROBE DONE")


if __name__ == "__main__":
    main()
