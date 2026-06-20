#!/usr/bin/env python3
"""验证文档间跳转：a.md -> b.md -> c.md（递归深入）、后退两次回 a、前进回 b、再跳 c 截断分支。"""
import sys, os, time, json, tempfile, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from inkwell.app import Api
from inkwell import render as R, server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_nav_result.json")
D = os.path.join(tempfile.gettempdir(), "inkwell_navtest")
os.makedirs(D, exist_ok=True)
open(os.path.join(D, "a.md"), "w", encoding="utf-8").write("# Doc A\n\n[到 B](b.md)\n\n[到 C](c.md)\n")
open(os.path.join(D, "b.md"), "w", encoding="utf-8").write("# Doc B\n\n[到 C](c.md)\n\n[回 A](a.md)\n")
open(os.path.join(D, "c.md"), "w", encoding="utf-8").write("# Doc C\n\n[到 B](b.md)\n")
A = os.path.join(D, "a.md")

class DelayedApi(Api):
    """故意让 a.md 的异步响应慢于 c.md，用于验证旧响应不会覆盖新导航。"""

    def open_md_link(self, href, base_path=None):
        if (href or "").split("#", 1)[0].lower().endswith("a.md"):
            time.sleep(0.35)
        elif (href or "").split("#", 1)[0].lower().endswith("c.md"):
            time.sleep(0.03)
        return super().open_md_link(href, base_path)


api = DelayedApi()
res = {"steps": []}


def setup():
    payload = api._render_payload(A)
    api.activate_path(A)
    S.set_page(build_page(payload["content"], payload["toc"], payload["title"], A,
                          preferences=api.preferences))


def job(win):
    try:
        for _ in range(40):
            if win.evaluate_js("document.readyState") == "complete" and win.evaluate_js("!!window.__ink"):
                break
            time.sleep(0.2)
        time.sleep(0.3)

        def state():
            return json.loads(win.evaluate_js("JSON.stringify(window.__ink.nav.state())"))

        def wait_path(end, tries=40):
            for _ in range(tries):
                st = state()
                if (st.get("path") or "").lower().replace("\\", "/").endswith(end):
                    return st
                time.sleep(0.15)
            return state()

        def step(name, action, expect_end, expect_index):
            if action:
                win.evaluate_js(action)
            st = wait_path(expect_end)
            ok = ((st.get("path") or "").lower().replace("\\", "/").endswith(expect_end)
                  and st.get("index") == expect_index)
            res["steps"].append({"name": name, "ok": ok, "path_end": expect_end,
                                 "got_index": st.get("index"), "len": st.get("len")})

        step("init=a", None, "a.md", 0)
        step("a->b", "window.__ink.nav.to('b.md')", "b.md", 1)
        step("b->c", "window.__ink.nav.to('c.md')", "c.md", 2)
        step("back->b", "window.__ink.nav.back()", "b.md", 1)
        step("back->a", "window.__ink.nav.back()", "a.md", 0)
        step("fwd->b", "window.__ink.nav.forward()", "b.md", 1)
        step("b->c(truncate)", "window.__ink.nav.to('c.md')", "c.md", 2)

        # watcher 中途送达旧文档 payload 时，必须完全忽略。
        stale = json.dumps({"ok": True, "path": A, "title": "STALE",
                            "content": "<h1>STALE</h1>", "toc": ""})
        win.evaluate_js(f"window.__applyPayload({stale})")
        st = state()
        stale_ok = ((st.get("path") or "").lower().replace("\\", "/").endswith("c.md")
                    and win.evaluate_js("!document.querySelector('#content h1').textContent.includes('STALE')"))
        res["steps"].append({"name": "ignore-stale-watcher", "ok": stale_ok,
                             "path_end": "c.md", "got_index": st.get("index"), "len": st.get("len")})

        # 从 b 同时发起慢 a / 快 c，最后发起的 c 必须胜出。
        step("race-setup->b", "window.__ink.nav.back()", "b.md", 1)
        win.evaluate_js("window.__ink.nav.to('a.md'); window.__ink.nav.to('c.md')")
        time.sleep(0.8)
        st = state()
        race_ok = ((st.get("path") or "").lower().replace("\\", "/").endswith("c.md")
                   and st.get("index") == 2 and st.get("len") == 3
                   and win.evaluate_js("document.querySelector('#content h1').textContent.includes('Doc C')"))
        res["steps"].append({"name": "latest-navigation-wins", "ok": race_ok,
                             "path_end": "c.md", "got_index": st.get("index"), "len": st.get("len")})

        res["all_pass"] = all(s["ok"] for s in res["steps"])
        res["final_len"] = state().get("len")
        res["stage"] = "ok"
    except Exception as e:
        res["stage"] = "error"; res["error"] = repr(e); res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try: win.destroy()
        except Exception: pass


setup()
httpd, url = S.start_server()
win = webview.create_window(title="nav-verify", url=url, js_api=api,
                            width=1000, height=720, frameless=True, easy_drag=False,
                            text_select=True, background_color="#FFFFFF", zoomable=False)
api._window = win
webview.start(job, win, gui="edgechromium", private_mode=True)
print("NAV VERIFY DONE")
