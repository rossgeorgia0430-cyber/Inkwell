#!/usr/bin/env python3
"""验证当前无边框窗口的真实缩放行为。

原脚本调用早已删除的 ``Api.win_resize``，因此从未覆盖现有实现。这里通过
pywebview/WinForms 实际移动和缩放窗口，检查最小尺寸、锚点、客户区边框，
并验证前端八向 resize handle 与当前 ``win_native_resize`` 桥契约一致。

不自动进入 ``WM_NCLBUTTONDOWN`` 的模态鼠标循环；那需要真实的系统鼠标拖动，
会让无人值守验证挂起。原生循环本身由 ``verify_native.py`` 检查。
"""
import ctypes
import json
import os
import sys
import time
import traceback
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview

from inkwell import render as R
from inkwell import server as S
from inkwell.app import Api
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_resize_result.json")

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


def _geometry(hwnd):
    window = wintypes.RECT()
    client = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetWindowRect(hwnd, ctypes.byref(window)):
        raise ctypes.WinError()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise ctypes.WinError()
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError()
    return {
        "window": [window.left, window.top, window.right, window.bottom],
        "client": [origin.x, origin.y,
                   origin.x + client.right, origin.y + client.bottom],
        "width": window.right - window.left,
        "height": window.bottom - window.top,
    }


def _move(hwnd, x, y):
    """移动真实 HWND；规避当前 pywebview 在严格 ctypes 签名下传 None 的兼容问题。"""
    if not user32.SetWindowPos(
        hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    ):
        raise ctypes.WinError()


api = Api()
content, toc = R.render_markdown(
    "# Resize 验证\n\n窗口边缘和四角应支持原生缩放。", base_dir=HERE
)
S.set_page(build_page(content, toc, "resize-verify"))
httpd, url = S.start_server()


def job(win):
    result = {"steps": []}
    try:
        time.sleep(1.0)
        api.init_native_chrome()
        time.sleep(0.2)
        hwnd = win.native.Handle.ToInt32()

        handles = win.evaluate_js("""
          (function () {
            var expected = ['n','s','w','e','nw','ne','sw','se'];
            return expected.map(function (edge) {
              var el = document.querySelector('.resize-handle.rh-' + edge);
              return {edge: edge, exists: !!el,
                      visible: !!el && getComputedStyle(el).display !== 'none'};
            });
          })()
        """)
        result["handles"] = handles
        result["handles_ok"] = (
            len(handles) == 8
            and all(item["exists"] and item["visible"] for item in handles)
            and hasattr(api, "win_native_resize")
        )

        # 真实移动并缩放一次；正常态下无边框客户区应与窗口矩形完全重合。
        _move(hwnd, 120, 100)
        win.resize(1000, 700)
        time.sleep(0.4)
        normal = _geometry(hwnd)
        result["normal"] = normal
        result["steps"].append({
            "name": "real resize keeps top-left anchor",
            "anchor_ok": abs(normal["window"][0] - 120) <= 2
                         and abs(normal["window"][1] - 100) <= 2,
            "size_ok": abs(normal["width"] - 1000) <= 2
                       and abs(normal["height"] - 700) <= 2,
            "client_fills_window": normal["client"] == normal["window"],
        })

        # 请求小于 min_size 的真实尺寸；高 DPI 下比较 WinForms 的物理最小值。
        win.resize(300, 200)
        time.sleep(0.4)
        minimum = _geometry(hwnd)
        native_minimum = win.native.MinimumSize
        result["minimum"] = minimum
        result["native_minimum"] = [native_minimum.Width, native_minimum.Height]
        result["steps"].append({
            "name": "minimum size enforced",
            "width_ok": minimum["width"] >= native_minimum.Width,
            "height_ok": minimum["height"] >= native_minimum.Height,
            "client_fills_window": minimum["client"] == minimum["window"],
        })

        # 未知 edge 必须是安全 no-op，不能意外进入原生模态循环。
        before_invalid = _geometry(hwnd)
        api.win_native_resize("invalid-edge")
        time.sleep(0.1)
        after_invalid = _geometry(hwnd)
        result["invalid_edge_noop"] = before_invalid == after_invalid

        result["all_pass"] = (
            result["handles_ok"]
            and result["invalid_edge_noop"]
            and all(
                all(value for key, value in step.items() if key != "name")
                for step in result["steps"]
            )
        )
        result["stage"] = "ok"
    except Exception as exc:
        result["stage"] = "error"
        result["error"] = repr(exc)
        result["trace"] = traceback.format_exc()
        result["all_pass"] = False
    finally:
        with open(RESULT, "w", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
        try:
            win.destroy()
        except Exception:
            pass


window = webview.create_window(
    title="resize-verify", url=url, js_api=api,
    width=1000, height=720, min_size=(520, 360),
    frameless=True, easy_drag=False, text_select=True,
    background_color="#FFFFFF", zoomable=False,
)
api._window = window
webview.start(job, window, gui="edgechromium", private_mode=True)
print("RESIZE VERIFY DONE")
