"""Cross-platform host helpers: data dir, file open, clipboard, window backend."""

import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def app_data_dir() -> Path:
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "Inkwell"
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Inkwell"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "Inkwell"
    return Path.home() / ".config" / "Inkwell"


def open_local_file(path: str) -> None:
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606
        return
    if IS_MACOS:
        subprocess.Popen(["open", path], start_new_session=True)
        return
    subprocess.Popen(["xdg-open", path], start_new_session=True)


def copy_image_to_clipboard(path) -> None:
    path = Path(path)
    if IS_WINDOWS:
        from .host_win import copy_image_to_clipboard as _copy
        _copy(path)
        return
    if IS_MACOS:
        from .host_mac import copy_image_to_clipboard as _copy
        _copy(path)
        return
    raise RuntimeError("当前平台暂不支持把图片复制到剪贴板")


def webview_gui():
    if IS_WINDOWS:
        return "edgechromium"
    if IS_MACOS:
        return "cocoa"
    return None


def create_window_backend(api):
    if IS_WINDOWS:
        from .host_win import Win32Backend
        return Win32Backend(api)
    if IS_MACOS:
        from .host_mac import CocoaBackend
        return CocoaBackend(api)
    return WindowBackend(api)


class WindowBackend:
    """pywebview-only fallback used on Linux / unknown platforms."""

    def __init__(self, api):
        self.api = api
        self._maximized = False

    def init_chrome(self):
        return None

    def toggle_maximize(self):
        window = self.api._window
        if window is None:
            return
        try:
            if self.is_maximized():
                window.restore()
                self._maximized = False
            else:
                window.maximize()
                self._maximized = True
        except Exception:
            try:
                window.toggle_fullscreen()
            except Exception:
                pass

    def is_maximized(self):
        window = self.api._window
        if window is None:
            return False
        for attr in ("maximized",):
            val = getattr(window, attr, None)
            if isinstance(val, bool):
                return val
        return bool(self._maximized)

    def native_drag(self):
        return None

    def native_resize(self, edge):
        return None

    def ui_invoke(self, fn):
        try:
            fn()
        except Exception:
            pass

    def set_represented_file(self, path):
        return None
