#!/usr/bin/env python3
"""
Inkwell - 应用入口（pywebview 原生窗口）
- 无边框窗口 + 自定义标题栏（Windows: Win32/WebView2；macOS: Cocoa/WKWebView）
- 内置服务器提供页面与资源；窗口加载 http://127.0.0.1:<port>/
- js_api 桥：打开文件、渲染、窗口控制
- 文件监视：源文件 mtime 变化时自动刷新正文
"""

import os
import sys

# console=False 打包后 stdout/stderr 可能为 None，提前兜底，避免 print/库写日志崩溃
if sys.stdout is None or sys.stderr is None:
    _null = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = _null
    if sys.stderr is None:
        sys.stderr = _null

import html as html_module
import threading
import time
from pathlib import Path

import webview

from . import server as _server
from .page import build_page, script_safe_json
from . import APP_NAME, DEBUG, log_exception
from . import documents
from . import images
from .api import Api
from .host import IS_MACOS, app_data_dir, webview_gui


def _js_call(fn_name, payload):
    # script_safe_json 的转义比这里原先手写的更全（额外挡 & < >），对 evaluate_js
    # 执行的 JS 源码同样安全，复用它以免两处维护同一件事。
    return "window.%s(%s)" % (fn_name, script_safe_json(payload))


def _run_js_guarded(window, js):
    """执行 run_js；失败（通常意味着窗口已销毁）不能让调用它的监视/Finder 线程退出。"""
    try:
        window.run_js(js)
    except Exception:
        log_exception()


def _inject_js(window, js):
    """把 JS 推给页面执行，不走 pywebview evaluate_js() 的返回值等待（WKWebView 的
    CSP 会拦截其内部 eval 包装）。

    macOS 上 pywebview 的 run_js/evaluate_js 在调度到 Cocoa 主线程后会等待信号量；
    若调用方本身就在主线程上，会与 AppKit 的事件循环互相等待造成死锁（窗口表现为
    转圈/无响应）。
    """
    if window is None:
        return
    if IS_MACOS:
        from .host_mac import evaluate_js_async, is_exiting, run_off_main
        if is_exiting():
            return
        if evaluate_js_async(window, js):
            return
        run_off_main(lambda: _run_js_guarded(window, js), name="InkwellInjectJS")
        return
    _run_js_guarded(window, js)


def _ensure_loopback_noproxy():
    """把回环地址加入 NO_PROXY，避免环境变量里的代理设置拦截本机回环请求。"""
    extra = ("127.0.0.1", "localhost", "::1")
    for key in ("NO_PROXY", "no_proxy"):
        cur = os.environ.get(key, "")
        parts = [p.strip() for p in cur.split(",") if p.strip()]
        for item in extra:
            if item not in parts:
                parts.append(item)
        os.environ[key] = ",".join(parts)


def _watch_file(api: Api):
    """后台轮询当前文件 mtime，变化时推送新内容到前端。"""
    while True:
        time.sleep(1.0)
        if api._closing:
            return
        payload = api._poll_file_change()
        if payload is not None:
            _inject_js(api._window, _js_call("__applyPayload", payload))


def _initial_file(argv):
    for a in argv[1:]:
        if a and not a.startswith("-"):
            try:
                return str(documents.document_path(a))
            except (OSError, ValueError):
                continue
    return None


def _loading_content(title):
    loading_name = html_module.escape(title)
    return (f'<div class="initial-loading" role="status" aria-live="polite">'
            f'<span class="initial-loading-spinner"></span>'
            f'<span>正在打开 {loading_name}…</span></div>')


def _prepare_storage_dir():
    storage = str(app_data_dir() / "webview")
    try:
        os.makedirs(storage, exist_ok=True)
    except OSError:
        log_exception()
        return None
    return storage


def _make_finder_handler(api: Api):
    """macOS Finder/Open With 把文件投递给已在运行的窗口。

    这里只负责挑一个可读的路径、离开 Cocoa 主线程、把结果注入页面；『首屏是否已
    交付』『要不要渲染』这些状态判断都交给 Api._accept_finder_path。
    """

    def work(paths):
        if api._closing:
            return
        chosen = None
        for p in paths:
            try:
                chosen = str(documents.document_path(p))
                break
            except (OSError, ValueError):
                continue
        if not chosen:
            return
        payload = api._accept_finder_path(chosen)
        if payload is not None:
            _inject_js(api._window, _js_call("__openFromFinder", payload))

    def handler(paths):
        if IS_MACOS:
            from .host_mac import run_off_main
            run_off_main(lambda: work(paths), name="InkwellOpenFromFinder")
        else:
            work(paths)

    return handler


def _create_window(api: Api, url):
    return webview.create_window(
        title=APP_NAME,
        url=url,
        js_api=api,
        width=1180,
        height=820,
        min_size=(520, 360),
        frameless=True,
        easy_drag=False,
        text_select=True,
        background_color="#1E1D1A" if api.preferences.get("theme") == "dark" else "#F7F5EF",
        confirm_close=False,
        zoomable=False,
    )


def _stop_http_server(httpd):
    try:
        httpd.shutdown()
    except OSError:
        log_exception()
    try:
        httpd.server_close()
    except OSError:
        log_exception()


def _wire_window_events(api: Api, window, httpd):
    def _on_closed():
        api._closing = True
        if IS_MACOS:
            from .host_mac import mark_exiting
            mark_exiting()
        images.cleanup_assets()

        stopper = threading.Thread(target=_stop_http_server, args=(httpd,),
                                   name="InkwellHttpShutdown", daemon=True)
        stopper.start()
        stopper.join(1.5)

    window.events.closed += _on_closed
    window.events.shown += lambda *a: api._init_native_chrome()


def _start_webview(storage):
    start_kwargs = dict(debug=DEBUG, private_mode=False, storage_path=storage)
    gui = webview_gui()
    if gui:
        start_kwargs["gui"] = gui
    if IS_MACOS:
        icns = Path(__file__).resolve().parent / "assets" / "icon.icns"
        if icns.is_file():
            start_kwargs["icon"] = str(icns)
    webview.start(**start_kwargs)


def main():
    _ensure_loopback_noproxy()
    if IS_MACOS:
        from .host_mac import configure_app_identity
        configure_app_identity()
    api = Api()

    init_path = _initial_file(sys.argv)
    api._init_path = init_path
    title = Path(init_path).name if init_path else APP_NAME

    # 先给 WebView 一个极轻的首帧；Markdown/Pygments 导入、base64 解码和正文
    # 渲染全部推迟到前端调用 pull_initial() 之后，尤其能改善大图片文档的感知启动速度。
    _server.set_page(build_page(_loading_content(title), "", title, init_path,
                                preferences=api.preferences))
    httpd, url = _server.start_server()
    storage = _prepare_storage_dir()

    if IS_MACOS:
        from .host_mac import install_open_file_handler
        install_open_file_handler(_make_finder_handler(api))

    window = _create_window(api, url)
    api._window = window

    threading.Thread(target=_watch_file, args=(api,), daemon=True).start()
    _wire_window_events(api, window, httpd)

    _start_webview(storage)


if __name__ == "__main__":
    main()
