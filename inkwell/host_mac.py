"""macOS 宿主：Cocoa/WKWebView 窗口外观、NSPasteboard 图片复制、Finder 打开事件。"""

import io
import os
import threading
import time
from pathlib import Path

from . import log_exception
from .host import WindowBackend

# 保持 Apple Event handler 存活；否则它的 trampoline 会被 GC 回收。
_OPEN_DOC_HANDLER = None
_OPEN_FILES_CALLBACK = None
_DELEGATE_PATCHED = False
_EXITING = False
_EXIT_WATCHDOG_STARTED = False


def _path_to_png_bytes(path: Path) -> bytes:
    from PIL import Image
    with Image.open(path) as raw:
        raw.load()
        if raw.mode not in ("RGB", "RGBA"):
            raw = raw.convert("RGBA")
        buf = io.BytesIO()
        raw.save(buf, format="PNG")
        return buf.getvalue()


def is_main_thread() -> bool:
    try:
        from Foundation import NSThread
        return bool(NSThread.isMainThread())
    except Exception:
        return False


def is_exiting() -> bool:
    return _EXITING


def mark_exiting():
    global _EXITING
    _EXITING = True


def _post_wake_event(app=None):
    """NSApplication.stop_ 只有在下一个事件到来后才会真正退出 run loop，这里补发一个事件把它唤醒。"""
    try:
        from AppKit import NSApp, NSEvent, NSPoint
    except ImportError:
        return
    app = app or NSApp
    if app is None:
        return
    try:
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            15,  # NSEventTypeApplicationDefined
            NSPoint(0.0, 0.0),
            0,
            0.0,
            0,
            None,
            0,
            0,
            0,
        )
        if event is not None:
            app.postEvent_atStart_(event, True)
    except Exception:
        log_exception()


def wake_app_loop():
    """在 stop_ 之后解除 NSApp.run() 的阻塞，让进程真正退出。"""
    try:
        from AppKit import NSApp
        app = NSApp
        try:
            app.stop_(None)
        except Exception:
            log_exception()
        try:
            app.abortModal()
        except Exception:
            log_exception()
        _post_wake_event(app)
    except Exception:
        log_exception()


def _start_exit_watchdog(seconds=2.0):
    global _EXIT_WATCHDOG_STARTED
    if _EXIT_WATCHDOG_STARTED:
        return
    _EXIT_WATCHDOG_STARTED = True

    def _nuke():
        time.sleep(seconds)
        os._exit(0)

    threading.Thread(target=_nuke, name="InkwellExitWatchdog", daemon=True).start()


def schedule_window_close(window, delay=0.1):
    """等 JS 桥的 evaluate_js 调用返回、WKWebView 仍存活时再关闭窗口。

    win_close 运行在 pywebview 的 worker 线程上，随后会调用 evaluate_js；如果这里
    先 destroy()，windowWillClose_ 会把 webview 置空，evaluate_js 等待的信号量就
    永远不会释放——解释器会卡在退出流程里。
    """
    mark_exiting()
    if window is None:
        wake_app_loop()
        return

    def go():
        mark_exiting()
        try:
            from PyObjCTools import AppHelper
            native = getattr(window, "native", None)

            def close():
                try:
                    if native is not None and hasattr(native, "performClose_"):
                        native.performClose_(None)
                        return
                except Exception:
                    log_exception()
                try:
                    window.destroy()
                except Exception:
                    wake_app_loop()

            AppHelper.callAfter(close)
        except Exception:
            try:
                window.destroy()
            except Exception:
                wake_app_loop()

    threading.Timer(max(0.0, float(delay)), go).start()


def run_off_main(fn, name="InkwellOffMain"):
    """若当前在 Cocoa 主线程上调用，把 fn 转到后台线程执行。

    pywebview 的 evaluate_js 在 AppHelper.callAfter 之后会等待一个信号量；在
    AppKit 线程上做这件事（或任何耗时的 Python 工作）会让 run loop 死锁，
    macOS 会把 Inkwell 报告为未响应。
    """
    if not callable(fn):
        return
    if is_main_thread():
        threading.Thread(target=fn, name=name, daemon=True).start()
        return
    fn()


def invoke_on_main(fn, wait=False, timeout=8):
    """把 fn 调度到 Cocoa 主线程执行；wait=True 时阻塞等待其返回（调用方本身若已
    在主线程，会直接同步执行 fn，不存在等待自己的问题）。"""
    if _EXITING and wait:
        raise RuntimeError("Inkwell is exiting")
    if is_main_thread():
        return fn()
    try:
        from Foundation import NSOperationQueue
    except ImportError:
        return fn()
    if not wait:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)
        return None
    box = {}
    done = threading.Event()

    def wrap():
        try:
            box["ok"] = fn()
        except Exception as exc:
            box["err"] = exc
        finally:
            done.set()

    NSOperationQueue.mainQueue().addOperationWithBlock_(wrap)
    if not done.wait(timeout):
        raise RuntimeError("macOS 主线程调用超时")
    if "err" in box:
        raise box["err"]
    return box.get("ok")


def evaluate_js_async(window, script) -> bool:
    """在 WKWebView 上执行 JS，不阻塞调用方。

    返回 True 表示已成功排入执行队列。pywebview 的 Window.run_js / evaluate_js
    最终都会走到 cocoa.BrowserView.evaluate_js，从 Cocoa 主线程上调用会死锁。
    """
    if window is None or not script or _EXITING:
        return False
    try:
        from PyObjCTools import AppHelper
        from webview.platforms import cocoa
    except ImportError:
        return False
    uid = getattr(window, "uid", None)
    view = cocoa.BrowserView.instances.get(uid) if uid is not None else None
    if view is None or getattr(view, "webview", None) is None:
        return False

    def eval_js():
        if _EXITING:
            return
        live = cocoa.BrowserView.instances.get(uid) if uid is not None else None
        webview = getattr(live, "webview", None) if live is not None else None
        if webview is None:
            return

        def _ignore(_result, _error):
            return None

        try:
            webview.evaluateJavaScript_completionHandler_(script, _ignore)
        except Exception:
            try:
                webview.evaluateJavaScript_completionHandler_(script, None)
            except Exception:
                log_exception()

    try:
        AppHelper.callAfter(eval_js)
        return True
    except Exception:
        return False


def copy_image_to_clipboard(path: Path) -> None:
    """把本地图片以 NSImage（TIFF/PNG）形式写入 macOS 的系统粘贴板。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    try:
        from AppKit import NSImage, NSPasteboard, NSPasteboardTypePNG
        from Foundation import NSData
    except ImportError as exc:
        raise RuntimeError("缺少 PyObjC/AppKit，无法复制图片") from exc

    png_bytes = None

    def write_pasteboard():
        nonlocal png_bytes
        image = NSImage.alloc().initWithContentsOfFile_(str(path))
        png_data = None
        if image is None:
            if png_bytes is None:
                png_bytes = _path_to_png_bytes(path)
            png_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
            image = NSImage.alloc().initWithData_(png_data)
            if image is None:
                raise RuntimeError("无法解码图片")

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        if pb.writeObjects_([image]):
            return
        if png_data is None:
            if png_bytes is None:
                png_bytes = _path_to_png_bytes(path)
            png_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        pb.declareTypes_owner_([NSPasteboardTypePNG], None)
        if not pb.setData_forType_(png_data, NSPasteboardTypePNG):
            raise RuntimeError("无法写入图片到剪贴板")

    invoke_on_main(write_pasteboard, wait=True)


class CocoaBackend(WindowBackend):
    def init_chrome(self):
        def fn():
            try:
                from AppKit import (
                    NSWindowStyleMaskClosable,
                    NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskResizable,
                )
                ns = self.api._window.native
                mask = ns.styleMask()
                ns.setStyleMask_(
                    mask
                    | NSWindowStyleMaskResizable
                    | NSWindowStyleMaskMiniaturizable
                    | NSWindowStyleMaskClosable
                )
            except Exception:
                log_exception()
        self.ui_invoke(fn)

    def is_maximized(self):
        window = self.api._window
        if window is None:
            return False
        native = getattr(window, "native", None)
        try:
            if native is not None and hasattr(native, "isZoomed") and is_main_thread():
                zoomed = bool(native.isZoomed())
                self._maximized = zoomed
                return zoomed
        except Exception:
            log_exception()
        return super().is_maximized()

    def toggle_maximize(self):
        window = self.api._window
        if window is None:
            return
        native = getattr(window, "native", None)

        def fn():
            try:
                if native is not None and hasattr(native, "zoom_"):
                    native.zoom_(None)
                    self._maximized = bool(native.isZoomed())
                    return
            except Exception:
                log_exception()
            WindowBackend.toggle_maximize(self)

        self.ui_invoke(fn)

    def native_drag(self):
        def fn():
            try:
                from AppKit import NSApp
                ns = self.api._window.native
                event = NSApp.currentEvent()
                if event is not None:
                    ns.performWindowDragWithEvent_(event)
            except Exception:
                log_exception()
        self.ui_invoke(fn)

    def ui_invoke(self, fn):
        try:
            from PyObjCTools import AppHelper
            if is_main_thread():
                fn()
                return
            AppHelper.callAfter(fn)
        except Exception:
            try:
                fn()
            except Exception:
                log_exception()

    def set_represented_file(self, path):
        window = self.api._window
        if window is None:
            return
        native = getattr(window, "native", None)
        if native is None:
            return

        def fn():
            try:
                if path:
                    native.setRepresentedFilename_(str(path))
                else:
                    native.setRepresentedFilename_("")
            except Exception:
                log_exception()
        self.ui_invoke(fn)


def _paths_from_open_docs_event(event):
    from Foundation import NSURL
    key_direct_object = 0x2D2D2D2D  # '----'
    docs = event.paramDescriptorForKeyword_(key_direct_object)
    if docs is None:
        return []
    paths = []

    def _one(desc):
        if desc is None:
            return
        url = None
        try:
            url = desc.fileURLValue()
        except Exception:
            log_exception()
        text = None
        try:
            text = desc.stringValue()
        except Exception:
            log_exception()
        if url is not None:
            p = url.path()
            if p:
                paths.append(str(p))
                return
        if text:
            if text.startswith("file:"):
                parsed = NSURL.URLWithString_(text)
                p = parsed.path() if parsed is not None else None
                if p:
                    paths.append(str(p))
                    return
            if text.startswith("/"):
                paths.append(text)

    count = 0
    try:
        count = int(docs.numberOfItems())
    except Exception:
        log_exception()
        count = 0
    if count:
        for i in range(1, count + 1):
            _one(docs.descriptorAtIndex_(i))
    else:
        _one(docs)
    return paths


def configure_app_identity():
    """把进程名设为 Inkwell，避免 AppKit 弹窗把发起者显示成 python。"""
    try:
        from Foundation import NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_("Inkwell")
    except Exception:
        log_exception()


def _dispatch_open_files(paths):
    if _EXITING:
        return
    cb = _OPEN_FILES_CALLBACK
    if not cb or not paths:
        return
    snapshot = list(paths)

    def work():
        try:
            cb(snapshot)
        except Exception:
            log_exception()

    run_off_main(work, name="InkwellOpenDocs")


def _paths_from_urls(urls):
    paths = []
    for u in urls or []:
        try:
            if hasattr(u, "isFileURL") and not u.isFileURL():
                continue
        except Exception:
            log_exception()
        p = None
        try:
            p = u.path()
        except Exception:
            log_exception()
            p = str(u) if u else None
        if p:
            paths.append(str(p))
    return paths


def _reply_open(app):
    try:
        app.replyToOpenOrPrint_(0)
    except Exception:
        log_exception()


def _install_apple_event_handler():
    """备用的 'odoc' Apple Event 处理器。NSApplication 可能会覆盖这个注册；真正
    能阻止 NSDocumentController 弹出格式错误提示的是 AppDelegate 的
    openURLs/openFile 方法。"""
    global _OPEN_DOC_HANDLER
    try:
        from Foundation import NSObject, NSAppleEventManager
    except ImportError:
        return

    k_core_event_class = 0x61657674  # 'aevt'
    k_ae_open_documents = 0x6F646F63  # 'odoc'

    class _OpenDocHandler(NSObject):
        def handleOpenDocuments_withReplyEvent_(self, event, replyEvent):
            try:
                paths = _paths_from_open_docs_event(event)
            except Exception:
                log_exception()
                return
            if paths:
                _dispatch_open_files(paths)

    handler = _OpenDocHandler.alloc().init()
    _OPEN_DOC_HANDLER = handler
    manager = NSAppleEventManager.sharedAppleEventManager()
    manager.setEventHandler_andSelector_forEventClass_andEventID_(
        handler,
        "handleOpenDocuments:withReplyEvent:",
        k_core_event_class,
        k_ae_open_documents,
    )


def _call_base(base, name, *args):
    method = getattr(base, name, None)
    if method is None:
        return
    try:
        method(*args)
    except Exception:
        log_exception()


def _patch_webview_app_delegate():
    """pywebview 自带的 AppDelegate 没有实现 application:openFile:。Info.plist 里
    配置了 CFBundleDocumentTypes 后，AppKit 会弹出『python 无法打开 Markdown
    文本格式的文件』这样的提示。"""
    global _DELEGATE_PATCHED
    if _DELEGATE_PATCHED:
        return
    try:
        from webview.platforms import cocoa
    except ImportError:
        return

    Base = cocoa.BrowserView.AppDelegate
    WindowBase = cocoa.BrowserView.WindowDelegate
    HostBase = cocoa.BrowserView.WebKitHost

    class InkwellAppDelegate(Base):
        def applicationWillFinishLaunching_(self, notification):
            _call_base(Base, "applicationWillFinishLaunching_", self, notification)
            _install_apple_event_handler()

        def applicationDidFinishLaunching_(self, notification):
            _call_base(Base, "applicationDidFinishLaunching_", self, notification)
            _install_apple_event_handler()

        def application_openURLs_(self, app, urls):
            paths = _paths_from_urls(urls)
            if paths:
                _dispatch_open_files(paths)
            _reply_open(app)

        def application_openFile_(self, app, filename):
            if filename:
                _dispatch_open_files([str(filename)])
            _reply_open(app)
            return True

        def application_openFiles_(self, app, filenames):
            paths = [str(f) for f in (filenames or []) if f]
            if paths:
                _dispatch_open_files(paths)
            _reply_open(app)

        def applicationShouldOpenUntitledFile_(self, app):
            return False

        def applicationShouldTerminateAfterLastWindowClosed_(self, app):
            return True

        def applicationShouldTerminate_(self, app):
            mark_exiting()
            method = getattr(Base, "applicationShouldTerminate_", None)
            if method is not None:
                try:
                    return method(self, app)
                except Exception:
                    log_exception()
            return True

    _orig_will_close = WindowBase.windowWillClose_

    def windowWillClose_(self, notification):
        mark_exiting()
        try:
            _orig_will_close(self, notification)
        except Exception:
            log_exception()
        wake_app_loop()
        _start_exit_watchdog()

    WindowBase.windowWillClose_ = windowWillClose_

    _orig_keydown = HostBase.keyDown_

    def keyDown_(self, event):
        try:
            from AppKit import NSApp, NSCommandKeyMask
            if event.modifierFlags() & NSCommandKeyMask:
                chars = str(event.characters() or "")
                if chars == "q":
                    mark_exiting()
                    NSApp.terminate_(None)
                    return
        except Exception:
            log_exception()
        return _orig_keydown(self, event)

    HostBase.keyDown_ = keyDown_

    cocoa.BrowserView.AppDelegate = InkwellAppDelegate
    _DELEGATE_PATCHED = True


def install_open_file_handler(on_files):
    """注册 Finder / "打开方式" 的处理器：文档被拖到本应用图标上，或应用运行期间
    再次被打开时，都会调用 on_files(list[str])。"""
    global _OPEN_FILES_CALLBACK
    _OPEN_FILES_CALLBACK = on_files
    configure_app_identity()
    _patch_webview_app_delegate()
    _install_apple_event_handler()
