#!/usr/bin/env python3
"""验证原生窗口：init 后永久具备 WS_THICKFRAME|WS_MAXIMIZEBOX（原生缩放+Snap），
   且经 WM_NCCALCSIZE 子类化后客户区始终铺满整个窗口矩形（无可见缩放边框）；
   win_toggle_maximize 能最大化/还原并同步前端状态、最大化铺满工作区。
   不实际调用 win_native_drag/resize（会进入需真实鼠标的系统模态循环）。"""
import sys, os, time, json, ctypes, traceback
from ctypes import wintypes
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
u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
u.GetWindowRect.restype = wintypes.BOOL
u.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
u.GetClientRect.restype = wintypes.BOOL
u.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
u.ClientToScreen.restype = wintypes.BOOL
if ctypes.sizeof(ctypes.c_void_p) == 8:
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _get = u.GetWindowLongPtrW
else:
    _get = u.GetWindowLongW


def _rects(hwnd):
    wr = wintypes.RECT()
    cr = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    u.GetWindowRect(hwnd, ctypes.byref(wr))
    u.GetClientRect(hwnd, ctypes.byref(cr))
    u.ClientToScreen(hwnd, ctypes.byref(origin))
    return {
        "window": [wr.left, wr.top, wr.right, wr.bottom],
        "client": [origin.x, origin.y, origin.x + cr.right, origin.y + cr.bottom],
    }


def _wait_js(win, expr, timeout=2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = bool(win.evaluate_js(expr))
        if last:
            return True
        time.sleep(0.1)
    return bool(last)

api = Api()
content, toc = R.render_markdown("# 原生窗口验证\n\n拖标题栏可 Snap；拖边/角可缩放。", base_dir=HERE)
S.set_page(build_page(content, toc, "native-verify"))
httpd, url = S.start_server()
res = {}


def job(win):
    try:
        time.sleep(1.0)
        hwnd = win.native.Handle.ToInt32()
        api.init_native_chrome()
        time.sleep(0.3)
        after = _get(hwnd, GWL_STYLE)
        # 永久样式：原生缩放 + Snap 资格
        res["thickframe_persistent"] = bool(after & WS_THICKFRAME)
        res["maximizebox_persistent"] = bool(after & WS_MAXIMIZEBOX)
        # WM_NCCALCSIZE 子类化：无边框窗口客户区铺满整窗（任何状态都不冒缩放边框）
        normal_rects = _rects(hwnd)
        res["normal_client_fills_window"] = normal_rects["client"] == normal_rects["window"]
        res["had_methods"] = all(hasattr(api, m) for m in
                                 ("win_native_drag", "win_native_resize", "win_toggle_maximize",
                                  "win_is_maximized"))
        # 最大化 / 还原（安全，无模态循环）
        api.win_toggle_maximize(); time.sleep(0.5)
        res["maximized_state"] = int(win.native.WindowState)   # 期望 2
        res["maximized_api"] = api.win_is_maximized()
        res["maximized_css"] = _wait_js(
            win, "document.documentElement.classList.contains('window-maximized')")
        res["handles_hidden"] = _wait_js(
            win,
            "Array.from(document.querySelectorAll('.resize-handle')).every(function(e){"
            "return getComputedStyle(e).display==='none';})")
        max_rects = _rects(hwnd)
        wa = win.native.MaximizedBounds
        res["maximized_client_fills_window"] = max_rects["client"] == max_rects["window"]
        res["maximized_fills_work_area"] = max_rects["window"] == [wa.Left, wa.Top, wa.Right, wa.Bottom]
        # 最大化后样式仍在（不会因状态切换丢失）
        res["thickframe_still_after_max"] = bool(_get(hwnd, GWL_STYLE) & WS_THICKFRAME)
        api.win_toggle_maximize(); time.sleep(0.5)
        res["restored_state"] = int(win.native.WindowState)    # 期望 0
        res["restored_api"] = not api.win_is_maximized()
        res["restored_css"] = _wait_js(
            win, "!document.documentElement.classList.contains('window-maximized')")
        restored_rects = _rects(hwnd)
        res["restored_client_fills_window"] = restored_rects["client"] == restored_rects["window"]
        res["all_pass"] = (res["thickframe_persistent"] and res["maximizebox_persistent"]
                           and res["normal_client_fills_window"] and res["had_methods"]
                           and res["maximized_state"] == 2 and res["maximized_api"]
                           and res["maximized_css"] and res["handles_hidden"]
                           and res["maximized_client_fills_window"] and res["maximized_fills_work_area"]
                           and res["thickframe_still_after_max"]
                           and res["restored_state"] == 0 and res["restored_api"]
                           and res["restored_css"] and res["restored_client_fills_window"])
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
