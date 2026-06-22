#!/usr/bin/env python3
"""Verify maximizing a frameless window on a portrait secondary monitor."""
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
RESULT = os.path.join(HERE, "_multimonitor_maximize_result.json")

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


def _rect_to_list(rect):
    return [rect.Left, rect.Top, rect.Right, rect.Bottom]


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    return [rect.left, rect.top, rect.right, rect.bottom]


def _move_and_size(hwnd, left, top, width, height):
    if not user32.SetWindowPos(
        hwnd, None, left, top, width, height, SWP_NOZORDER | SWP_NOACTIVATE
    ):
        raise ctypes.WinError()


def _choose_target_screen():
    from System.Windows.Forms import Screen

    screens = list(Screen.AllScreens)
    target = next(
        (
            screen
            for screen in screens
            if not screen.Primary and screen.WorkingArea.Height > screen.WorkingArea.Width
        ),
        None,
    )
    if target is None:
        target = next((screen for screen in screens if not screen.Primary), None)
    return screens, target


api = Api()
content, toc = R.render_markdown(
    "# Multi-monitor maximize verify\n\n"
    "The window should maximize inside the selected monitor work area.",
    base_dir=HERE,
)
S.set_page(build_page(content, toc, "multimonitor-maximize-verify"))
httpd, url = S.start_server()


def job(win):
    result = {}
    try:
        time.sleep(1.0)
        api.init_native_chrome()
        time.sleep(0.4)

        screens, target = _choose_target_screen()
        result["screens"] = [
            {
                "device": screen.DeviceName,
                "primary": bool(screen.Primary),
                "bounds": _rect_to_list(screen.Bounds),
                "work_area": _rect_to_list(screen.WorkingArea),
            }
            for screen in screens
        ]

        if target is None:
            result["stage"] = "skipped"
            result["reason"] = "no secondary monitor"
            result["all_pass"] = True
            return

        hwnd = win.native.Handle.ToInt32()
        wa = target.WorkingArea
        width = max(520, min(980, wa.Width - 120))
        height = max(360, min(900, wa.Height - 160))
        left = wa.Left + max(20, (wa.Width - width) // 2)
        top = wa.Top + max(20, (wa.Height - height) // 2)

        _move_and_size(hwnd, left, top, width, height)
        time.sleep(0.5)
        before = _window_rect(hwnd)

        api.win_toggle_maximize()
        time.sleep(0.8)

        after = _window_rect(hwnd)
        expected = _rect_to_list(wa)
        tolerance = 2
        matches_target = all(abs(a - b) <= tolerance for a, b in zip(after, expected))
        intersects_target = (
            after[0] < expected[2]
            and after[2] > expected[0]
            and after[1] < expected[3]
            and after[3] > expected[1]
        )

        result.update(
            {
                "stage": "ok",
                "target": {
                    "device": target.DeviceName,
                    "bounds": _rect_to_list(target.Bounds),
                    "work_area": expected,
                },
                "before": before,
                "after": after,
                "window_state": int(win.native.WindowState),
                "maximized_api": bool(api.win_is_maximized()),
                "matches_target_work_area": matches_target,
                "intersects_target_work_area": intersects_target,
                "all_pass": (
                    int(win.native.WindowState) == 2
                    and bool(api.win_is_maximized())
                    and matches_target
                    and intersects_target
                ),
            }
        )
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
    title="multimonitor-maximize-verify",
    url=url,
    js_api=api,
    width=1000,
    height=720,
    min_size=(520, 360),
    frameless=True,
    easy_drag=False,
    text_select=True,
    background_color="#FFFFFF",
    zoomable=False,
)
api._window = window
webview.start(job, window, gui="edgechromium", private_mode=True)

with open(RESULT, "r", encoding="utf-8") as file:
    result = json.load(file)

print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result.get("all_pass") else 1)
