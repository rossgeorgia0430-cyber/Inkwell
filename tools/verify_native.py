#!/usr/bin/env python3
"""验证原生窗口设置：init_native_chrome 后窗口应带 WS_THICKFRAME|WS_MAXIMIZEBOX；
   win_toggle_maximize 应能最大化/还原。注意：不实际调用 win_native_drag/resize
   （它们会进入需要真实鼠标的系统模态循环，自动化中会挂起）。"""
import sys, os, time, json, ctypes, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from inkwell.app import Api
from inkwell import render as R, server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_native_result.json")
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
u = ctypes.windll.user32
if ctypes.sizeof(ctypes.c_void_p) == 8:
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _get = u.GetWindowLongPtrW
else:
    _get = u.GetWindowLongW

api = Api()
content, toc = R.render_markdown("# 原生窗口验证\n\n拖标题栏可 Snap；拖边/角可缩放。", base_dir=HERE)
S.set_page(build_page(content, toc, "native-verify"))
httpd, url = S.start_server()
res = {}


def job(win):
    try:
        time.sleep(1.0)
        hwnd = win.native.Handle.ToInt32()
        before = _get(hwnd, GWL_STYLE)
        api.init_native_chrome()
        time.sleep(0.3)
        after = _get(hwnd, GWL_STYLE)
        res["thickframe"] = bool(after & WS_THICKFRAME)
        res["maximizebox"] = bool(after & WS_MAXIMIZEBOX)
        res["had_methods"] = all(hasattr(api, m) for m in
                                 ("win_native_drag", "win_native_resize", "win_toggle_maximize"))
        # 最大化 / 还原（安全，无模态循环）
        api.win_toggle_maximize(); time.sleep(0.5)
        res["maximized_state"] = int(win.native.WindowState)   # 期望 2
        api.win_toggle_maximize(); time.sleep(0.5)
        res["restored_state"] = int(win.native.WindowState)    # 期望 0
        res["all_pass"] = (res["thickframe"] and res["maximizebox"] and res["had_methods"]
                           and res["maximized_state"] == 2 and res["restored_state"] == 0)
        res["stage"] = "ok"
    except Exception as e:
        res["stage"] = "error"; res["error"] = repr(e); res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try: win.destroy()
        except Exception: pass


win = webview.create_window(title="native-verify", url=url, js_api=api,
                            width=1000, height=720, min_size=(520, 360),
                            frameless=True, easy_drag=False, text_select=True,
                            background_color="#FFFFFF", zoomable=False)
api._window = win
webview.start(job, win, gui="edgechromium", private_mode=True)
print("NATIVE VERIFY DONE")
