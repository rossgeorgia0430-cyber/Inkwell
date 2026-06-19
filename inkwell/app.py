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

import ctypes
from ctypes import wintypes

import webview

from . import render as _render
from . import server as _server
from .page import build_page
from . import APP_NAME, __version__


# ---------------------------------------------------------------------------
# 原生窗口行为（Win32）：给无边框窗口加上 WS_THICKFRAME 以启用「原生 8 向缩放 +
# Aero Snap 并列 + 拖回还原」，并用 ReleaseCapture()+SendMessage(WM_NCLBUTTONDOWN)
# 进入系统自带的移动/缩放模态循环（完全原生手感）。WS_THICKFRAME 对无边框窗口
# 不会画出可见边框（见 SDL 实现）。所有调用必须 marshal 到 WinForms UI 线程。
# ---------------------------------------------------------------------------
_user32 = ctypes.windll.user32
_WM_NCLBUTTONDOWN = 0x00A1
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

_user32.ReleaseCapture.restype = wintypes.BOOL
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
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


def _enable_native_chrome(hwnd):
    """一次性给无边框窗口加 WS_THICKFRAME|WS_MAXIMIZEBOX：启用原生缩放与 Snap，无可见边框。"""
    try:
        style = _GetStyle(hwnd, _GWL_STYLE)
        _SetStyle(hwnd, _GWL_STYLE, style | _WS_THICKFRAME | _WS_MAXIMIZEBOX)
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
    except Exception:
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


def _read_text(path: str) -> str:
    p = Path(path)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            break
    try:
        return p.read_bytes().decode("utf-8", errors="replace")
    except Exception:
        return ""


class Api:
    """暴露给前端 window.pywebview.api 的方法。"""

    def __init__(self):
        self._window = None
        self.current_file = None
        self._maximized = False
        self._mtime = None

    # ---- 渲染 ----
    def _render_payload(self, path):
        try:
            md_text = _read_text(path)
            base_dir = str(Path(path).resolve().parent)
            content, toc = _render.render_markdown(md_text, base_dir=base_dir)
            title = Path(path).name
            self.current_file = os.path.abspath(path)
            try:
                self._mtime = os.path.getmtime(path)
            except OSError:
                self._mtime = None
            return {"ok": True, "title": title, "content": content,
                    "toc": toc, "path": self.current_file}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e), "title": "错误",
                    "content": f"<h1>无法打开文件</h1><pre>{e}</pre>", "toc": ""}

    def render_path(self, path):
        """前端请求渲染某个文件，返回 payload。"""
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": "文件不存在", "title": "错误",
                    "content": "<h1>文件不存在</h1>", "toc": ""}
        return self._render_payload(path)

    def open_dialog(self):
        """弹系统文件选择框，选中后渲染并返回 payload。"""
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Markdown (*.md;*.markdown;*.mdown;*.mkd)", "所有文件 (*.*)"),
            )
        except Exception:
            result = None
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self._render_payload(path)

    # ---- 文档间跳转（点击正文里的 .md 链接）----
    def _resolve_local(self, href):
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
                    base = (Path(self.current_file).resolve().parent
                            if self.current_file else Path.cwd())
                    p = base / href
            return str(p.resolve()), anchor
        except Exception:
            return None, anchor

    def open_md_link(self, href):
        """点击正文里指向本地 .md 的链接：解析并渲染目标文档，返回 payload（含 anchor）。"""
        try:
            if not href:
                return {"ok": False, "error": "空链接"}
            abspath, anchor = self._resolve_local(href)
            if abspath is None:                       # 纯锚点（#section）
                return {"ok": False, "samedoc": True, "anchor": anchor}
            # 同一文档内的锚点跳转，不重复渲染
            if self.current_file and os.path.normcase(os.path.normpath(abspath)) == \
               os.path.normcase(os.path.normpath(self.current_file)):
                return {"ok": False, "samedoc": True, "anchor": anchor}
            if not os.path.isfile(abspath):
                return {"ok": False, "error": "文件不存在：%s" % abspath, "anchor": anchor}
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
                "title": Path(self.current_file).name if self.current_file else APP_NAME}

    # ---- 窗口控制 ----
    def win_minimize(self):
        try:
            self._window.minimize()
        except Exception:
            pass

    def win_toggle_maximize(self):
        # 直接读原生窗口实际状态（避免拖动还原后本地标志位失同步）
        try:
            form = self._window.native
            if int(form.WindowState) == 2:   # FormWindowState.Maximized
                self._window.restore()
            else:
                self._window.maximize()
        except Exception:
            pass

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
        """窗口显示后（UI 线程）调用一次：开启原生缩放 + Snap 资格。"""
        try:
            _enable_native_chrome(self._hwnd())
        except Exception:
            pass

    def win_native_drag(self):
        """从自绘标题栏发起原生窗口移动 → 支持拖到屏幕边缘 Snap 并列 / 拖回还原。"""
        hwnd = self._hwnd()
        self._ui_invoke(lambda: (_user32.ReleaseCapture(),
                                 _user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HTCAPTION, 0)))

    def win_native_resize(self, edge):
        """从边/角发起原生缩放（8 向，原生光标 + Snap 预览）。"""
        code = _RESIZE_HT.get(edge)
        if code is None:
            return
        hwnd = self._hwnd()
        self._ui_invoke(lambda: (_user32.ReleaseCapture(),
                                 _user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, code, 0)))


def _watch_file(api: Api):
    """后台轮询当前文件 mtime，变化时推送新内容到前端。"""
    while True:
        time.sleep(1.0)
        path = api.current_file
        if not path:
            continue
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if api._mtime is not None and mt != api._mtime:
            api._mtime = mt
            payload = api._render_payload(path)
            try:
                js = "window.__applyPayload(%s)" % json.dumps(payload, ensure_ascii=False)
                api._window.evaluate_js(js)
            except Exception:
                pass


def _initial_file(argv):
    for a in argv[1:]:
        if a and not a.startswith("-") and os.path.isfile(a):
            low = a.lower()
            if low.endswith((".md", ".markdown", ".mdown", ".mkd", ".txt")) or True:
                return os.path.abspath(a)
    return None


def main():
    api = Api()

    init_path = _initial_file(sys.argv)
    if init_path:
        payload = api._render_payload(init_path)
        title = payload.get("title", APP_NAME)
        content, toc = payload.get("content", ""), payload.get("toc", "")
    else:
        content, toc = _render.render_markdown(WELCOME_MD, base_dir=str(Path.cwd()))
        title = APP_NAME

    _server.set_page(build_page(content, toc, title, api.current_file))
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
