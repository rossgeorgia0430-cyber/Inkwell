#!/usr/bin/env python3
"""
Inkwell - 应用入口（pywebview / WebView2 原生窗口）
- 无边框窗口 + 自定义标题栏（拖拽区用 .pywebview-drag-region）
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

import json
import html as html_module
import time
import threading
import traceback
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

# 允许通过系统默认程序打开的本地文件类型（白名单）——避免不可信文档里的链接
# 直接拉起 .exe/.bat/.ps1 等可执行/脚本文件。
_SAFE_OPEN_EXTS = {
    ".pdf", ".txt", ".csv", ".json", ".xml", ".log", ".rtf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff", ".ico",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".htm", ".html", ".mp3", ".wav", ".mp4", ".mov", ".webm",
}
_MD_EXTS = {".md", ".markdown", ".mdown", ".mkd"}
_READABLE_EXTS = _MD_EXTS | {".txt"}
_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_PREFERENCE_KEYS = {"theme", "font"}

import ctypes
from ctypes import wintypes

import webview

from . import render as _render
from . import server as _server
from .page import build_page
from . import APP_NAME, __version__


# ---------------------------------------------------------------------------
# 原生窗口行为（Win32）：给无边框窗口「永久」加上 WS_THICKFRAME|WS_MAXIMIZEBOX，
# 启用原生 8 向缩放 + Aero Snap 并列 + 拖回还原；同时用 ctypes 子类化窗口过程，在
# WM_NCCALCSIZE 返回 0，让客户区铺满整个窗口矩形——这样无边框窗口在拖动/缩放时
# 不会冒出系统画的非客户区缩放边框（之前临时加 WS_THICKFRAME 会闪一圈白边，且反复
# 增删样式偶发丢边框）。pythonnet 覆写 Form.WndProc 不生效，但裸 Win32 子类化
# （SetWindowLongPtr(GWLP_WNDPROC)）完全可用。所有安装/手势调用须在 UI 线程执行。
# ---------------------------------------------------------------------------
_user32 = ctypes.windll.user32
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCCALCSIZE = 0x0083
_GWLP_WNDPROC = -4
_HTCAPTION = 2
_RESIZE_HT = {
    'left': 10, 'right': 11, 'top': 12, 'topleft': 13, 'topright': 14,
    'bottom': 15, 'bottomleft': 16, 'bottomright': 17,
}
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000      # = WS_SIZEBOX：启用原生缩放 + Snap 资格
_WS_MAXIMIZEBOX = 0x00010000     # Snap 布局/拖动最大化所需
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

_user32.ReleaseCapture.restype = wintypes.BOOL
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
# 64 位用 *Ptr 变体避免 WS_POPUP(0x80000000) 的有符号溢出；32 位回退到 W 变体
if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(_user32, 'GetWindowLongPtrW'):
    _GetStyle, _SetStyle = _user32.GetWindowLongPtrW, _user32.SetWindowLongPtrW
    _GetStyle.argtypes = [wintypes.HWND, ctypes.c_int]; _GetStyle.restype = ctypes.c_ssize_t
    _SetStyle.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]; _SetStyle.restype = ctypes.c_ssize_t
else:
    _GetStyle, _SetStyle = _user32.GetWindowLongW, _user32.SetWindowLongW
    _GetStyle.argtypes = [wintypes.HWND, ctypes.c_int]; _GetStyle.restype = wintypes.LONG
    _SetStyle.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]; _SetStyle.restype = wintypes.LONG

# 子类化窗口过程所需：WNDPROC 回调原型 + CallWindowProc + SetWindowLongPtr(GWLP_WNDPROC)
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              ctypes.c_size_t, ctypes.c_ssize_t)
_user32.CallWindowProcW.restype = ctypes.c_ssize_t
_user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                    ctypes.c_size_t, ctypes.c_ssize_t]
if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(_user32, 'SetWindowLongPtrW'):
    _SetWndProc = _user32.SetWindowLongPtrW
else:
    _SetWndProc = _user32.SetWindowLongW
_SetWndProc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_SetWndProc.restype = ctypes.c_ssize_t

# 子类化回调必须保活，否则 trampoline 被 GC 后窗口收到消息即崩溃。
_WNDPROC_REFS = []


def _apply_window_style(hwnd, style):
    """写入完整窗口样式并让 Win32 立即重算非客户区。"""
    try:
        if _GetStyle(hwnd, _GWL_STYLE) == style:
            return
        _SetStyle(hwnd, _GWL_STYLE, style)
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
    except Exception:
        pass


def _install_native_chrome(hwnd):
    """一次性：子类化窗口过程（WM_NCCALCSIZE→0，客户区铺满整窗、无可见缩放边框），
    并永久加上 WS_THICKFRAME|WS_MAXIMIZEBOX（原生 8 向缩放 + Aero Snap）。
    须在拥有该窗口的 UI 线程调用一次。"""
    old_proc = [0]

    @_WNDPROC
    def _proc(h, msg, wparam, lparam):
        # 移除全部非客户区：无边框窗口因此不会显示系统的缩放边框/内缩白边。
        if msg == _WM_NCCALCSIZE and wparam:
            return 0
        return _user32.CallWindowProcW(old_proc[0], h, msg, wparam, lparam)

    # 先登记旧过程，再切换——SetWindowPos(FRAMECHANGED) 会同步回调 _proc。
    old_proc[0] = _SetWndProc(hwnd, _GWLP_WNDPROC, ctypes.cast(_proc, ctypes.c_void_p).value)
    _WNDPROC_REFS.append(_proc)            # 保活
    style = _GetStyle(hwnd, _GWL_STYLE)
    _apply_window_style(hwnd, style | _WS_THICKFRAME | _WS_MAXIMIZEBOX)


def _set_native_frame_visual(hwnd, maximized):
    """最大化时关闭 DWM 圆角和描边，还原时交回系统默认策略。"""
    try:
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.restype = ctypes.c_long
        corner = ctypes.c_int(_DWMWCP_DONOTROUND if maximized else _DWMWCP_DEFAULT)
        border = ctypes.c_uint32(_DWMWA_COLOR_NONE if maximized else _DWMWA_COLOR_DEFAULT)
        dwm(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
        dwm(hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border))
    except Exception:
        # Windows 10 等旧系统不支持这些 Windows 11 DWM 属性，保持原行为即可。
        pass


WELCOME_MD = """# 欢迎使用 Inkwell

这是一个本地运行的 **Markdown 阅读器**。

- 点击左上角 **目录** 按钮可折叠/展开侧栏
- 按 **Ctrl+F** 搜索，**Ctrl+B** 切换目录，**Ctrl+O** 打开文件
- 右上角可切换 **浅色 / 深色** 主题
- 代码块里 **双击**任意标识符会高亮同名 token
- 选中文字复制到飞书等文档**不会带底色和彩色**；公式可直接复制为 LaTeX

> 用「打开文件」按钮选择一个 `.md` 文件开始，或直接双击任意 Markdown 文件。

```python
def hello(name: str) -> str:
    return f"Hello, {name}!"
```

行内公式 $E = mc^2$，块级公式：

$$\\int_{-\\infty}^{\\infty} e^{-x^2}\\,dx = \\sqrt{\\pi}$$
"""


def _document_path(path) -> Path:
    if not path:
        raise ValueError("文件路径为空")
    p = Path(path).resolve(strict=True)
    if not p.is_file():
        raise ValueError("文件不存在")
    if p.suffix.lower() not in _READABLE_EXTS:
        raise ValueError("仅支持 Markdown 或纯文本文件")
    if p.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("文件过大（上限 16 MB）")
    return p


def _read_text(path: str) -> str:
    p = _document_path(path)
    data = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise UnicodeError("无法识别文件编码")


def _settings_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Inkwell"
    return root / "settings.json"


def _load_preferences():
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        prefs = {}
        if data.get("theme") in ("light", "dark"):
            prefs["theme"] = data["theme"]
        try:
            font = float(data.get("font"))
            if 10 <= font <= 26:
                prefs["font"] = font
        except (TypeError, ValueError):
            pass
        return prefs
    except Exception:
        return {}


def _save_preferences(preferences):
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Api:
    """暴露给前端 window.pywebview.api 的方法。"""

    def __init__(self):
        self._window = None
        self.current_file = None
        self._maximized = None
        self._native_state_handler = None
        self._chrome_installed = False
        self._normal_size = None
        self._mtime = None
        self._state_lock = threading.RLock()
        self.preferences = _load_preferences()

    # ---- 渲染 ----
    def _render_payload(self, path):
        try:
            resolved = _document_path(path)
            md_text = _read_text(resolved)
            base_dir = str(resolved.parent)
            content, toc = _render.render_markdown(md_text, base_dir=base_dir)
            title = resolved.name
            return {"ok": True, "title": title, "content": content,
                    "toc": toc, "path": str(resolved)}
        except Exception as e:
            if os.environ.get("INKWELL_DEBUG") == "1":
                traceback.print_exc()
            error = html_module.escape(str(e))
            return {"ok": False, "error": str(e), "title": "错误",
                    "content": f"<h1>无法打开文件</h1><pre>{error}</pre>", "toc": ""}

    def render_path(self, path):
        """前端请求渲染某个文件，返回 payload。"""
        return self._render_payload(path)

    def activate_path(self, path):
        """前端确认 payload 已显示后，再切换 watcher 与相对链接的活动文档。"""
        try:
            resolved = _document_path(path)
            with self._state_lock:
                self.current_file = str(resolved)
                self._mtime = resolved.stat().st_mtime_ns
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_preference(self, key, value):
        """把主题/字号写入宿主配置，避免随机 HTTP 端口导致 localStorage 失忆。"""
        try:
            if key not in _PREFERENCE_KEYS:
                return {"ok": False, "error": "不支持的设置项"}
            if key == "theme":
                if value not in ("light", "dark"):
                    raise ValueError("无效主题")
            else:
                value = float(value)
                if not 10 <= value <= 26:
                    raise ValueError("无效字号")
            with self._state_lock:
                self.preferences[key] = value
                _save_preferences(self.preferences)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_dialog(self):
        """弹系统文件选择框，选中后渲染并返回 payload。"""
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Markdown / 文本 (*.md;*.markdown;*.mdown;*.mkd;*.txt)",),
            )
        except Exception:
            result = None
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self._render_payload(path)

    # ---- 文档间跳转（点击正文里的 .md 链接）----
    def _resolve_local(self, href, base_path=None):
        """把 href（相对/绝对/file://，可带 #anchor）解析为 (abs_path|None, anchor)。"""
        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
        href = unquote(href.strip())
        if not href:
            return None, anchor
        try:
            low = href.lower()
            if low.startswith("file:"):
                parts = urlsplit(href)
                p = Path(url2pathname(parts.path))
            else:
                p = Path(href)
                if not p.is_absolute():
                    source = base_path or self.current_file
                    base = Path(source).resolve().parent if source else Path.cwd()
                    p = base / href
            return str(p.resolve()), anchor
        except Exception:
            return None, anchor

    def open_md_link(self, href, base_path=None):
        """点击正文里指向本地 .md 的链接：解析并渲染目标文档，返回 payload（含 anchor）。"""
        try:
            if not href:
                return {"ok": False, "error": "空链接"}
            abspath, anchor = self._resolve_local(href, base_path)
            if abspath is None:                       # 纯锚点（#section）
                return {"ok": False, "samedoc": True, "anchor": anchor}
            # 同一文档内的锚点跳转，不重复渲染
            source_path = base_path or self.current_file
            if source_path and os.path.normcase(os.path.normpath(abspath)) == \
               os.path.normcase(os.path.normpath(source_path)):
                return {"ok": False, "samedoc": True, "anchor": anchor}
            payload = self._render_payload(abspath)
            payload["anchor"] = anchor
            return payload
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_external(self, href):
        """外部 URL → 系统浏览器；本地非 .md 文件 → 系统默认程序（白名单内）。"""
        try:
            low = (href or "").lower()
            if low.startswith(("http://", "https://", "mailto:", "tel:")):
                webbrowser.open(href)
                return {"ok": True}
            abspath, _ = self._resolve_local(href)
            if abspath and os.path.isfile(abspath):
                if Path(abspath).suffix.lower() in _SAFE_OPEN_EXTS:
                    os.startfile(abspath)            # noqa (Windows)
                    return {"ok": True}
                return {"ok": False, "error": "出于安全考虑未打开该类型文件：%s" % abspath}
            return {"ok": False, "error": "无法打开：%s" % href}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_boot(self):
        return {"name": APP_NAME, "version": __version__,
                "path": self.current_file,
                "title": Path(self.current_file).name if self.current_file else APP_NAME,
                "preferences": dict(self.preferences)}

    # ---- 窗口控制 ----
    def win_minimize(self):
        try:
            self._window.minimize()
        except Exception:
            pass

    def _apply_maximized_bounds(self):
        """把窗体 MaximizedBounds 设为「当前显示器」的工作区（排除任务栏）。
        无边框窗体（FormBorderStyle.None）在高 DPI 缩放显示器上，WinForms 默认算出的
        最大化尺寸会出错——典型表现就是「只铺到半截屏 / 盖住任务栏」。显式给定
        MaximizedBounds 后，由 WM_GETMINMAXINFO 强制最大化为精确的工作区矩形。
        必须在 UI 线程调用（WinForms 属性跨线程赋值会抛异常）。"""
        try:
            from System.Windows.Forms import Screen
            from System.Drawing import Rectangle
            form = self._window.native
            wa = Screen.FromControl(form).WorkingArea
            form.MaximizedBounds = Rectangle(wa.X, wa.Y, wa.Width, wa.Height)
        except Exception:
            pass

    def win_toggle_maximize(self):
        # 最大化 / 还原。最大化前按「当前显示器」工作区设定 MaximizedBounds，
        # 确保铺满整屏（高 DPI 下也不会只到半截），且不盖任务栏；保留原生 Snap / 拖回还原。
        def fn():
            try:
                from System.Windows.Forms import FormWindowState
                from System.Drawing import Size
                form = self._window.native
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
        self._ui_invoke(fn)

    def win_is_maximized(self):
        """供前端同步最大化状态；使用 Win32 查询，避免跨线程读取 WinForms 属性。"""
        try:
            return bool(_user32.IsZoomed(self._hwnd()))
        except Exception:
            return False

    def win_close(self):
        try:
            self._window.destroy()
        except Exception:
            pass

    # ---- 原生移动/缩放（ReleaseCapture + SendMessage，marshal 到 UI 线程）----
    def _hwnd(self):
        return self._window.native.Handle.ToInt32()

    def _ui_invoke(self, fn):
        """把调用异步派发到 WinForms UI 线程（SendMessage 模态循环会阻塞整个拖动）。"""
        try:
            from System import Action
            self._window.native.BeginInvoke(Action(fn))
        except Exception:
            try:
                fn()
            except Exception:
                pass

    def init_native_chrome(self):
        """窗口显示后：永久安装原生缩放样式 + WM_NCCALCSIZE 子类化（一次），
        并装好显示器/最大化状态同步。"""

        def init_state_sync():
            if not self._chrome_installed:
                _install_native_chrome(self._hwnd())
                self._chrome_installed = True
            self._apply_maximized_bounds()
            form = self._window.native

            def sync_frame(*_):
                maximized = self.win_is_maximized()
                if not maximized:
                    # 窗口移到另一块显示器后，下一次按钮或 Aero 最大化应使用新工作区。
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

        self._ui_invoke(init_state_sync)

    def win_native_drag(self):
        """从自绘标题栏发起原生窗口移动 → 支持拖到屏幕边缘 Snap 并列 / 拖回还原。
        样式已永久具备 WS_THICKFRAME，手势期间不再增删样式（避免闪白边/丢边框）。"""
        hwnd = self._hwnd()

        def fn():
            from System.Drawing import Size
            started_maximized = self.win_is_maximized()
            if not started_maximized:
                self._normal_size = (self._window.native.Width, self._window.native.Height)
            _user32.ReleaseCapture()
            _user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HTCAPTION, 0)
            if started_maximized and not self.win_is_maximized() and self._normal_size:
                self._window.native.Size = Size(*self._normal_size)
            elif not self.win_is_maximized():
                self._normal_size = (self._window.native.Width, self._window.native.Height)
            self._apply_maximized_bounds()

        self._ui_invoke(fn)

    def win_native_resize(self, edge):
        """从边/角发起原生缩放（8 向，原生光标 + Snap 预览）。"""
        code = _RESIZE_HT.get(edge)
        if code is None:
            return
        hwnd = self._hwnd()

        def fn():
            _user32.ReleaseCapture()
            _user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, code, 0)
            if not self.win_is_maximized():
                self._normal_size = (self._window.native.Width, self._window.native.Height)

        self._ui_invoke(fn)


def _watch_file(api: Api):
    """后台轮询当前文件 mtime，变化时推送新内容到前端。"""
    while True:
        time.sleep(1.0)
        with api._state_lock:
            path = api.current_file
            previous_mtime = api._mtime
        if not path:
            continue
        try:
            mt = os.stat(path).st_mtime_ns
        except OSError:
            continue
        if previous_mtime is not None and mt != previous_mtime:
            payload = api._render_payload(path)
            with api._state_lock:
                # 渲染期间用户可能已经切换文档；旧结果不得覆盖新页面或活动路径。
                if api.current_file != path:
                    continue
                api._mtime = mt
            try:
                js = "window.__applyPayload(%s)" % json.dumps(payload, ensure_ascii=False)
                api._window.evaluate_js(js)
            except Exception:
                pass


def _initial_file(argv):
    for a in argv[1:]:
        if a and not a.startswith("-"):
            try:
                return str(_document_path(a))
            except (OSError, ValueError):
                continue
    return None


def main():
    api = Api()

    init_path = _initial_file(sys.argv)
    if init_path:
        payload = api._render_payload(init_path)
        if payload.get("ok"):
            api.activate_path(payload["path"])
            title = payload.get("title", APP_NAME)
            content, toc = payload.get("content", ""), payload.get("toc", "")
        else:
            title = payload.get("title", APP_NAME)
            content, toc = payload.get("content", ""), ""
    else:
        content, toc = _render.render_markdown(WELCOME_MD, base_dir=str(Path.cwd()))
        title = APP_NAME

    _server.set_page(build_page(content, toc, title, api.current_file,
                                preferences=api.preferences))
    httpd, url = _server.start_server()

    # WebView2 数据目录（cookies/localStorage 主题持久化）需可写
    storage = os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())), "Inkwell", "webview")
    try:
        os.makedirs(storage, exist_ok=True)
    except Exception:
        storage = None

    window = webview.create_window(
        title=APP_NAME,
        url=url,
        js_api=api,
        width=1180,
        height=820,
        min_size=(520, 360),
        frameless=True,
        easy_drag=False,
        text_select=True,
        background_color="#FFFFFF",
        confirm_close=False,
        zoomable=False,
    )
    api._window = window

    watcher = threading.Thread(target=_watch_file, args=(api,), daemon=True)
    watcher.start()

    debug = os.environ.get("INKWELL_DEBUG") == "1"

    def _on_closed():
        _render.cleanup_assets()
        try:
            httpd.shutdown()
        except Exception:
            pass

    window.events.closed += _on_closed
    # 窗口显示后（UI 线程）开启原生缩放 + Snap
    window.events.shown += lambda *a: api.init_native_chrome()

    webview.start(gui="edgechromium", debug=debug,
                  private_mode=False, storage_path=storage)


if __name__ == "__main__":
    main()
