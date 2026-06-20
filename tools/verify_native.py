#!/usr/bin/env python3
"""验证原生窗口设置：厚边框样式只在原生循环期间临时启用；
   win_toggle_maximize 应能最大化/还原并同步前端状态。注意：不实际调用 win_native_drag/resize
   （它们会进入需要真实鼠标的系统模态循环，自动化中会挂起）。"""
import sys, os, time, json, ctypes, traceback
from ctypes import wintypes
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from inkwell.app import Api, _apply_window_style, _enable_native_chrome
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
        res["clean_after_init"] = not bool(after & WS_THICKFRAME)
        # 拖动/缩放期间临时具备原生样式，循环结束后必须移除，避免非客户区内缩。
        original_style = _enable_native_chrome(hwnd)
        during = _get(hwnd, GWL_STYLE)
        res["temporary_thickframe"] = bool(during & WS_THICKFRAME)
        res["temporary_maximizebox"] = bool(during & WS_MAXIMIZEBOX)
        _apply_window_style(hwnd, original_style)
        cleaned = _get(hwnd, GWL_STYLE)
        res["style_restored_exactly"] = cleaned == original_style == after
        normal_rects = _rects(hwnd)
        res["normal_client_fills_window"] = normal_rects["client"] == normal_rects["window"]
        res["had_methods"] = all(hasattr(api, m) for m in
                                 ("win_native_drag", "win_native_resize", "win_toggle_maximize",
                                  "win_is_maximized"))
        # 最大化 / 还原（安全，无模态循环）
        api.win_toggle_maximize(); time.sleep(0.5)
        res["maximized_state"] = int(win.native.WindowState)   # 期望 2
        res["maximized_api"] = api.win_is_maximized()
        res["maximized_css"] = bool(win.evaluate_js(
            "document.documentElement.classList.contains('window-maximized')"))
        res["handles_hidden"] = bool(win.evaluate_js(
            "Array.from(document.querySelectorAll('.resize-handle')).every(function(e){"
            "return getComputedStyle(e).display==='none';})"))
        max_rects = _rects(hwnd)
        wa = win.native.MaximizedBounds
        res["maximized_client_fills_window"] = max_rects["client"] == max_rects["window"]
        res["maximized_fills_work_area"] = max_rects["window"] == [wa.Left, wa.Top, wa.Right, wa.Bottom]
        api.win_toggle_maximize(); time.sleep(0.5)
        res["restored_state"] = int(win.native.WindowState)    # 期望 0
        res["restored_api"] = not api.win_is_maximized()
        res["restored_css"] = not bool(win.evaluate_js(
            "document.documentElement.classList.contains('window-maximized')"))
        res["all_pass"] = (res["clean_after_init"] and res["temporary_thickframe"]
                           and res["temporary_maximizebox"] and res["style_restored_exactly"]
                           and res["normal_client_fills_window"]
                           and res["had_methods"]
                           and res["maximized_state"] == 2 and res["maximized_api"]
                           and res["maximized_css"] and res["handles_hidden"]
                           and res["maximized_client_fills_window"] and res["maximized_fills_work_area"]
                           and res["restored_state"] == 0 and res["restored_api"]
                           and res["restored_css"])
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
