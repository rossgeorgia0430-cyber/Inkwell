#!/usr/bin/env python3
"""macOS 主线程不得阻塞：Finder 打开 / JS 注入必须离开 AppKit 线程。"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check_host_isolation():
    from inkwell.host import create_window_backend

    class _Api:
        _window = None

    create_window_backend(_Api())
    assert "inkwell.host_win" not in sys.modules


def check_off_main_and_inject():
    from inkwell.app import _inject_js, _js_call
    from inkwell.host_mac import (
        evaluate_js_async, is_exiting, is_main_thread, run_off_main,
        schedule_window_close,
    )

    assert is_main_thread() is True

    ran = threading.Event()
    names = []

    def work():
        names.append(threading.current_thread().name)
        ran.set()

    t0 = time.perf_counter()
    run_off_main(work, name="InkwellOpenDocs")
    assert ran.wait(2.0), "run_off_main did not leave the main thread"
    assert names == ["InkwellOpenDocs"]
    assert time.perf_counter() - t0 < 1.0

    class Dummy:
        uid = "missing-window"

    t0 = time.perf_counter()
    assert evaluate_js_async(None, "1+1") is False
    assert evaluate_js_async(Dummy(), "1+1") is False
    assert evaluate_js_async(Dummy(), "") is False
    assert time.perf_counter() - t0 < 1.0

    js = _js_call("__openFromFinder", {
        "ok": True, "path": "/tmp/x.md", "content": "<p>x</p>",
    })
    assert js.startswith("window.__openFromFinder(")
    t0 = time.perf_counter()
    _inject_js(Dummy(), js)
    assert time.perf_counter() - t0 < 1.0, "_inject_js blocked on the Cocoa main thread"

    assert is_exiting() is False
    t0 = time.perf_counter()
    schedule_window_close(None, delay=0.0)
    assert time.perf_counter() - t0 < 1.0, "schedule_window_close blocked"
    from inkwell.app import Api
    api = Api()
    assert api._closing is False
    t0 = time.perf_counter()
    api.win_close()
    assert time.perf_counter() - t0 < 1.0, "win_close blocked on the Cocoa main thread"
    assert api._closing is True


def main():
    assert sys.platform == "darwin", "this check is macOS-only"
    check_host_isolation()
    check_off_main_and_inject()
    print("MAC UI THREAD VERIFY PASS")


if __name__ == "__main__":
    main()
