"""Windows host: Win32 frameless chrome, DIB clipboard, WinForms UI thread."""

import ctypes
import io
import time
from ctypes import wintypes
from pathlib import Path

_user32 = ctypes.windll.user32
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCCALCSIZE = 0x0083
_WM_GETMINMAXINFO = 0x0024
_GWLP_WNDPROC = -4
_HTCAPTION = 2
_RESIZE_HT = {
    "left": 10, "right": 11, "top": 12, "topleft": 13, "topright": 14,
    "bottom": 15, "bottomleft": 16, "bottomright": 17,
}
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_FRAMECHANGED = 0x0020
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWCP_DEFAULT = 0
_DWMWCP_DONOTROUND = 1
_DWMWA_COLOR_DEFAULT = 0xFFFFFFFF
_DWMWA_COLOR_NONE = 0xFFFFFFFE
_MONITOR_DEFAULTTONEAREST = 0x00000002
_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_user32.ReleaseCapture.restype = wintypes.BOOL
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.MonitorFromWindow.restype = wintypes.HANDLE
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(_user32, "GetWindowLongPtrW"):
    _GetStyle, _SetStyle = _user32.GetWindowLongPtrW, _user32.SetWindowLongPtrW
    _GetStyle.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetStyle.restype = ctypes.c_ssize_t
    _SetStyle.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SetStyle.restype = ctypes.c_ssize_t
else:
    _GetStyle, _SetStyle = _user32.GetWindowLongW, _user32.SetWindowLongW
    _GetStyle.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetStyle.restype = wintypes.LONG
    _SetStyle.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    _SetStyle.restype = wintypes.LONG

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)
_user32.CallWindowProcW.restype = ctypes.c_ssize_t
_user32.CallWindowProcW.argtypes = [
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
]
if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(_user32, "SetWindowLongPtrW"):
    _SetWndProc = _user32.SetWindowLongPtrW
else:
    _SetWndProc = _user32.SetWindowLongW
_SetWndProc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_SetWndProc.restype = ctypes.c_ssize_t

_WNDPROC_REFS = []


def _write_dib_to_clipboard(dib):
    """写入标准 CF_DIB。成功后 Windows 接管 GlobalAlloc 的内存所有权。"""
    if not dib:
        raise ValueError("图片数据为空")
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.CloseClipboard.restype = wintypes.BOOL

    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(dib))
    if not handle:
        raise OSError("无法分配剪贴板内存")
    try:
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise OSError("无法锁定剪贴板内存")
        try:
            ctypes.memmove(ptr, dib, len(dib))
        finally:
            kernel32.GlobalUnlock(handle)

        opened = False
        for _ in range(12):
            if _user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.025)
        if not opened:
            raise OSError("剪贴板正被其他应用占用")
        try:
            if not _user32.EmptyClipboard():
                raise OSError("无法清空剪贴板")
            if not _user32.SetClipboardData(_CF_DIB, handle):
                raise OSError("无法写入图片到剪贴板")
            handle = None
        finally:
            _user32.CloseClipboard()
    finally:
        if handle:
            kernel32.GlobalFree(handle)


def _image_asset_to_dib(path):
    """将本地化图片编码为通用 24-bit DIB（不触碰系统剪贴板）。"""
    try:
        try:
            from PIL import Image
            with Image.open(path) as raw:
                raw.load()
                if "A" in raw.getbands() or (raw.mode == "P" and "transparency" in raw.info):
                    rgba = raw.convert("RGBA")
                    image = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    image.alpha_composite(rgba)
                    image = image.convert("RGB")
                else:
                    image = raw.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="BMP")
                bmp = output.getvalue()
        except ImportError:
            from System.Drawing import Bitmap, Color, Graphics
            from System.Drawing.Imaging import ImageFormat, PixelFormat
            from System.IO import MemoryStream
            source = Bitmap(str(path))
            bitmap = Bitmap(source.Width, source.Height, PixelFormat.Format24bppRgb)
            graphics = Graphics.FromImage(bitmap)
            stream = MemoryStream()
            try:
                graphics.Clear(Color.White)
                graphics.DrawImage(source, 0, 0, source.Width, source.Height)
                bitmap.Save(stream, ImageFormat.Bmp)
                bmp = bytes(stream.ToArray())
            finally:
                stream.Dispose()
                graphics.Dispose()
                bitmap.Dispose()
                source.Dispose()
        if len(bmp) <= 14 or bmp[:2] != b"BM":
            raise ValueError("无法生成有效图片数据")
        return bmp[14:]
    except Exception as exc:
        raise RuntimeError("复制图片失败：%s" % exc) from exc


def copy_image_to_clipboard(path: Path) -> None:
    """将本地化图片写入 Windows 剪贴板，供 Word、飞书等程序直接粘贴。"""
    _write_dib_to_clipboard(_image_asset_to_dib(path))


def _monitor_rects_for_window(hwnd):
    monitor = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info.rcMonitor, info.rcWork


def _apply_minmax_for_current_monitor(hwnd, lparam):
    rects = _monitor_rects_for_window(hwnd)
    if not rects:
        return False
    monitor, work = rects
    info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
    info.ptMaxPosition.x = work.left - monitor.left
    info.ptMaxPosition.y = work.top - monitor.top
    info.ptMaxSize.x = work.right - work.left
    info.ptMaxSize.y = work.bottom - work.top
    return True


def _apply_window_style(hwnd, style):
    try:
        if _GetStyle(hwnd, _GWL_STYLE) == style:
            return
        _SetStyle(hwnd, _GWL_STYLE, style)
        _user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )
    except Exception:
        pass


def _install_native_chrome(hwnd):
    old_proc = [0]

    @_WNDPROC
    def _proc(h, msg, wparam, lparam):
        if msg == _WM_GETMINMAXINFO and lparam:
            result = _user32.CallWindowProcW(old_proc[0], h, msg, wparam, lparam)
            _apply_minmax_for_current_monitor(h, lparam)
            return result
        if msg == _WM_NCCALCSIZE and wparam:
            return 0
        return _user32.CallWindowProcW(old_proc[0], h, msg, wparam, lparam)

    old_proc[0] = _SetWndProc(hwnd, _GWLP_WNDPROC, ctypes.cast(_proc, ctypes.c_void_p).value)
    _WNDPROC_REFS.append(_proc)
    style = _GetStyle(hwnd, _GWL_STYLE)
    _apply_window_style(hwnd, style | _WS_THICKFRAME | _WS_MAXIMIZEBOX)


def _set_native_frame_visual(hwnd, maximized):
    try:
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.restype = ctypes.c_long
        corner = ctypes.c_int(_DWMWCP_DONOTROUND if maximized else _DWMWCP_DEFAULT)
        border = ctypes.c_uint32(_DWMWA_COLOR_NONE if maximized else _DWMWA_COLOR_DEFAULT)
        dwm(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
        dwm(hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border))
    except Exception:
        pass


class Win32Backend:
    def __init__(self, api):
        self.api = api
        self._maximized = None
        self._native_state_handler = None
        self._chrome_installed = False
        self._normal_size = None

    def _hwnd(self):
        return self.api._window.native.Handle.ToInt32()

    def ui_invoke(self, fn):
        try:
            from System import Action
            self.api._window.native.BeginInvoke(Action(fn))
        except Exception:
            try:
                fn()
            except Exception:
                pass

    def _apply_maximized_bounds(self):
        try:
            from System.Drawing import Rectangle
            form = self.api._window.native
            rects = _monitor_rects_for_window(self._hwnd())
            if not rects:
                return
            _monitor, work = rects
            form.MaximizedBounds = Rectangle(
                work.left,
                work.top,
                work.right - work.left,
                work.bottom - work.top,
            )
        except Exception:
            pass

    def init_chrome(self):
        def init_state_sync():
            if not self._chrome_installed:
                _install_native_chrome(self._hwnd())
                self._chrome_installed = True
            self._apply_maximized_bounds()
            form = self.api._window.native

            def sync_frame(*_):
                maximized = self.is_maximized()
                if not maximized:
                    self._apply_maximized_bounds()
                    self._normal_size = (form.Width, form.Height)
                if maximized == self._maximized:
                    return
                self._maximized = maximized
                _set_native_frame_visual(self._hwnd(), maximized)

            if self._native_state_handler is None:
                self._native_state_handler = sync_frame
                form.SizeChanged += self._native_state_handler
                form.LocationChanged += self._native_state_handler
            sync_frame()

        self.ui_invoke(init_state_sync)

    def toggle_maximize(self):
        def fn():
            try:
                from System.Windows.Forms import FormWindowState
                from System.Drawing import Size
                form = self.api._window.native
                if form.WindowState == FormWindowState.Maximized:
                    form.WindowState = FormWindowState.Normal
                    if self._normal_size:
                        form.Size = Size(*self._normal_size)
                else:
                    self._normal_size = (form.Width, form.Height)
                    self._apply_maximized_bounds()
                    form.WindowState = FormWindowState.Maximized
            except Exception:
                pass
        self.ui_invoke(fn)

    def is_maximized(self):
        try:
            return bool(_user32.IsZoomed(self._hwnd()))
        except Exception:
            return False

    def native_drag(self):
        hwnd = self._hwnd()

        def fn():
            from System.Drawing import Size
            started_maximized = self.is_maximized()
            if not started_maximized:
                self._normal_size = (self.api._window.native.Width, self.api._window.native.Height)
            _user32.ReleaseCapture()
            _user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HTCAPTION, 0)
            if started_maximized and not self.is_maximized() and self._normal_size:
                self.api._window.native.Size = Size(*self._normal_size)
            elif not self.is_maximized():
                self._normal_size = (self.api._window.native.Width, self.api._window.native.Height)
            self._apply_maximized_bounds()

        self.ui_invoke(fn)

    def native_resize(self, edge):
        code = _RESIZE_HT.get(edge)
        if code is None:
            return
        hwnd = self._hwnd()

        def fn():
            _user32.ReleaseCapture()
            _user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, code, 0)
            if not self.is_maximized():
                self._normal_size = (self.api._window.native.Width, self.api._window.native.Height)

        self.ui_invoke(fn)

    def set_represented_file(self, path):
        return None
