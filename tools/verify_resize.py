#!/usr/bin/env python3
"""直接验证无边框窗口缩放管线：调用 Api.win_resize 后检查窗口尺寸/位置是否符合锚点预期。"""
import sys, os, time, json, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview
from inkwell.app import Api
from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_resize_result.json")

MEASURE = ("(function(){var d=window.devicePixelRatio||1;return {"
           "x:Math.round(window.screenX*d),y:Math.round(window.screenY*d),"
           "w:Math.round(window.innerWidth*d),h:Math.round(window.innerHeight*d),dpr:d};})()")


def job(win):
    res = {"steps": []}
    try:
        time.sleep(1.2)
        m0 = win.evaluate_js(MEASURE)
        res["m0"] = m0

        # 1) 右下增大（锚 西/北：左上固定）-> 期望 w≈1300,h≈900, x,y 不变
        api.win_resize(1300, 900, False, False)
        time.sleep(0.5)
        m1 = win.evaluate_js(MEASURE)
        res["m1"] = m1
        res["steps"].append({
            "name": "grow WN (top-left fixed)",
            "w_ok": abs(m1["w"] - 1300) <= 6, "h_ok": abs(m1["h"] - 900) <= 6,
            "x_fixed": abs(m1["x"] - m0["x"]) <= 6, "y_fixed": abs(m1["y"] - m0["y"]) <= 6,
        })

        # 2) 左边外扩（锚 东：右边固定）-> 期望右边 (x+w) 不变, w≈1560, x 减小
        right1 = m1["x"] + m1["w"]
        api.win_resize(1560, 900, True, False)
        time.sleep(0.5)
        m2 = win.evaluate_js(MEASURE)
        res["m2"] = m2
        right2 = m2["x"] + m2["w"]
        res["steps"].append({
            "name": "grow E-anchored (right edge fixed)",
            "w_ok": abs(m2["w"] - 1560) <= 6,
            "right_fixed": abs(right2 - right1) <= 8,
            "x_moved_left": m2["x"] < m1["x"] - 50,
        })

        # 3) 上边外扩（锚 南：下边固定）-> 期望下边 (y+h) 不变, h≈1040, y 减小
        bottom2 = m2["y"] + m2["h"]
        api.win_resize(1560, 1040, True, True)
        time.sleep(0.5)
        m3 = win.evaluate_js(MEASURE)
        res["m3"] = m3
        bottom3 = m3["y"] + m3["h"]
        res["steps"].append({
            "name": "grow S-anchored (bottom edge fixed)",
            "h_ok": abs(m3["h"] - 1040) <= 6,
            "bottom_fixed": abs(bottom3 - bottom2) <= 8,
            "y_moved_up": m3["y"] < m2["y"] - 50,
        })

        res["all_pass"] = all(all(v for k, v in s.items() if k != "name") for s in res["steps"])
        res["stage"] = "ok"
    except Exception as e:
        res["stage"] = "error"; res["error"] = repr(e); res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try: win.destroy()
        except Exception: pass


api = Api()
content, toc = R.render_markdown("# Resize 验证\n\n拖拽边/角应可缩放。", base_dir=os.getcwd())
S.set_page(build_page(content, toc, "resize-verify"))
httpd, url = S.start_server()
window = webview.create_window(
    title="resize-verify", url=url, js_api=api,
    width=1000, height=720, min_size=(520, 360),
    frameless=True, easy_drag=False, text_select=True, background_color="#FFFFFF",
)
api._window = window
webview.start(job, window, gui="edgechromium", private_mode=True)
print("RESIZE VERIFY DONE")
