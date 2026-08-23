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

import json
import html as html_module
import importlib
import re
import shutil
import time
import threading
import traceback
import uuid
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
_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_MAX_EMBED_IMAGE_BYTES = 8 * 1024 * 1024
_PREFERENCE_KEYS = {"theme", "font"}
_IMAGE_PICK_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff", ".ico", ".avif"
}
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".ico": "image/x-icon", ".avif": "image/avif",
}

import webview

from . import server as _server
from .page import build_page
from . import APP_NAME, __version__
from .host import (
    IS_MACOS,
    app_data_dir,
    copy_image_to_clipboard,
    create_window_backend,
    open_local_file,
    webview_gui,
)

_RENDER_MODULE = None


def _get_render():
    """延迟加载 markdown/Pygments 渲染栈，让原生窗口更早出现。"""
    global _RENDER_MODULE
    if _RENDER_MODULE is None:
        _RENDER_MODULE = importlib.import_module(".render", __package__)
    return _RENDER_MODULE


def _image_asset_path_for_copy(source):
    """把前端图片 URL 严格限制到本进程的 /__img__/ 临时资源目录。"""
    if not isinstance(source, str) or not source:
        return None
    try:
        render = _get_render()
        parts = urlsplit(source)
        # currentSrc 会被浏览器标准化为完整 http://127.0.0.1:<port>/__img__/...；
        # 只接受本机回环地址，绝不让 JS bridge 读取任意本地路径或下载远程 URL。
        if parts.scheme:
            if parts.scheme not in {"http", "https"} or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return None
        path = unquote(parts.path or "")
        if not path.startswith(render.IMG_URL_PREFIX) or render.ASSETS_DIR is None:
            return None
        rel = path[len(render.IMG_URL_PREFIX):].lstrip("/\\")
        if not rel:
            return None
        root = render.ASSETS_DIR.resolve()
        candidate = (root / rel).resolve()
        candidate.relative_to(root)
        return candidate if candidate.is_file() else None
    except (OSError, ValueError):
        return None


def _welcome_md():
    mod = "⌘" if IS_MACOS else "Ctrl"
    copy_mod = "⌘" if IS_MACOS else "Ctrl"
    return f"""# 欢迎使用 Inkwell

这是一个本地运行的 **Markdown 阅读器**。

- 点击左上角 **目录** 按钮可折叠/展开侧栏
- 按 **{mod}+F** 搜索，**{mod}+B** 切换目录，**{mod}+O** 打开文件
- 右上角可切换 **浅色 / 深色** 主题
- 代码块里 **双击**任意标识符会高亮同名 token
- 选中文字复制到飞书等文档**不会带底色和彩色**；公式可直接复制为 LaTeX
- 点击图片会按当前窗口自适应放大；底部按钮或 **{mod}+滚轮** 可继续缩放
- 图片右上角可复制，选中后按 **{copy_mod}+C** 也可直接复制

> 用「打开文件」按钮选择一个 `.md` 文件开始，或直接双击任意 Markdown 文件。

```python
def hello(name: str) -> str:
    return f"Hello, {{name}}!"
```

行内公式 $E = mc^2$，块级公式：

$$\\int_{{-\\infty}}^{{\\infty}} e^{{-x^2}}\\,dx = \\sqrt{{\\pi}}$$
"""


WELCOME_MD = _welcome_md()


def _document_path(path) -> Path:
    if not path:
        raise ValueError("文件路径为空")
    p = Path(path).resolve(strict=True)
    if not p.is_file():
        raise ValueError("文件不存在")
    if p.suffix.lower() not in _READABLE_EXTS:
        raise ValueError("仅支持 Markdown 或纯文本文件")
    if p.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("文件过大（上限 64 MB）")
    return p


def _read_text_meta(path: str):
    """读取文档文本，返回 (text, encoding, mtime_ns)。

    encoding 用于保存时尽量保持原编码。
    注意：无 BOM 的 UTF-8 不能标成 utf-8-sig，否则保存时会凭空写入 BOM。
    用同一 FD 的 read + fstat，避免读字节与 mtime 之间的 TOCTOU。
    """
    p = _document_path(path)
    with open(p, "rb") as f:
        data = f.read()
        mtime_ns = os.fstat(f.fileno()).st_mtime_ns
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig", mtime_ns
        except (UnicodeDecodeError, UnicodeError):
            pass
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), enc, mtime_ns
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise UnicodeError("无法识别文件编码")


def _read_text(path: str) -> str:
    return _read_text_meta(path)[0]


def _coerce_mtime_ns(value):
    """把桥接层传来的 mtime 规范为 int；JSON Number 会丢 ns 精度，故优先收字符串。"""
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        return int(value)
    return int(value)


def _mtime_token(mtime_ns) -> str:
    """mtime 一律以十进制字符串过桥，避免 JS Number 丢精度。"""
    return str(int(mtime_ns))


def _encode_document_text(text: str, encoding: str):
    """按打开时检测到的编码写回；无法编码时回退 UTF-8。返回 (bytes, used_encoding)。"""
    if not isinstance(text, str):
        raise ValueError("文档内容必须是文本")
    enc = encoding if encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1") else "utf-8"
    try:
        return text.encode(enc), enc
    except (UnicodeEncodeError, LookupError):
        return text.encode("utf-8"), "utf-8"


def _write_text_atomic(path: Path, text: str, encoding: str = "utf-8",
                       expected_mtime_ns=None):
    """原子写盘，返回 (mtime_ns, used_encoding)。

    若提供 expected_mtime_ns，在 replace 前再校验一次，缩小冲突窗口。
    """
    data, used_enc = _encode_document_text(text, encoding)
    if len(data) > _MAX_DOCUMENT_BYTES:
        raise ValueError("文件过大（上限 64 MB）")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected_mtime_ns is not None and path.exists():
        if path.stat().st_mtime_ns != expected_mtime_ns:
            raise FileExistsError("CONFLICT:%d" % path.stat().st_mtime_ns)
    # 同目录临时文件 + replace，避免写一半被 watcher 读到
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        if expected_mtime_ns is not None and path.exists():
            if path.stat().st_mtime_ns != expected_mtime_ns:
                raise FileExistsError("CONFLICT:%d" % path.stat().st_mtime_ns)
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path.stat().st_mtime_ns, used_enc


def _md_image_markdown(alt: str, dest: str) -> str:
    """生成安全的 Markdown 图片语法；必要时用 <dest> 包裹。"""
    alt = re.sub(r"[\r\n\[\]]+", " ", alt or "").strip() or "image"
    dest = (dest or "").replace("\r", "").replace("\n", "")
    if re.search(r'[\s()<>]', dest):
        dest_esc = dest.replace("(", "%28").replace(")", "%29").replace(">", "%3E")
        return f"![{alt}](<{dest_esc}>)"
    return f"![{alt}]({dest})"


def _settings_path() -> Path:
    return app_data_dir() / "settings.json"


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


def _ensure_loopback_noproxy():
    """Keep 127.0.0.1 off HTTP/SOCKS proxies (Clash mixed-port 403s loopback)."""
    extra = ("127.0.0.1", "localhost", "::1")
    for key in ("NO_PROXY", "no_proxy"):
        cur = os.environ.get(key, "")
        parts = [p.strip() for p in cur.split(",") if p.strip()]
        for item in extra:
            if item not in parts:
                parts.append(item)
        os.environ[key] = ",".join(parts)


def _js_call(fn_name, payload):
    blob = json.dumps(payload, ensure_ascii=False)
    blob = (blob.replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029")
                .replace("</", "<\\/"))
    return "window.%s(%s)" % (fn_name, blob)


def _inject_js(window, js):
    """Push JS without pywebview's eval() wrapper (blocked by WKWebView CSP).

    On macOS, pywebview's run_js/evaluate_js wait on a semaphore after
    scheduling work on the Cocoa main thread. Calling that from the main
    thread deadlocks AppKit (the window beachballs / 'not responding').
    """
    if window is None:
        return
    if IS_MACOS:
        from .host_mac import evaluate_js_async, is_exiting, run_off_main
        if is_exiting():
            return
        if evaluate_js_async(window, js):
            return

        def _fallback():
            try:
                run_js = getattr(window, "run_js", None)
                if callable(run_js):
                    run_js(js)
                    return
            except Exception:
                if os.environ.get("INKWELL_DEBUG") == "1":
                    traceback.print_exc()
            try:
                window.evaluate_js(js)
            except Exception:
                if os.environ.get("INKWELL_DEBUG") == "1":
                    traceback.print_exc()

        run_off_main(_fallback, name="InkwellInjectJS")
        return
    try:
        run_js = getattr(window, "run_js", None)
        if callable(run_js):
            run_js(js)
            return
    except Exception:
        if os.environ.get("INKWELL_DEBUG") == "1":
            traceback.print_exc()
    try:
        window.evaluate_js(js)
    except Exception:
        if os.environ.get("INKWELL_DEBUG") == "1":
            traceback.print_exc()


class Api:
    """暴露给前端 window.pywebview.api 的方法。"""

    def __init__(self):
        self._window = None
        self.current_file = None
        self._backend = create_window_backend(self)
        self._mtime = None
        self._encoding = "utf-8"
        self._encoding_hint = None  # (path, encoding) from last successful read/render
        self._watch_paused = False
        self._save_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._load_lock = threading.Lock()
        self.preferences = _load_preferences()
        self._init_path = None
        self._pending_mac_files = []
        self._initial_payload = None
        self._open_seq = 0
        self._closing = False

    def _remember_encoding(self, path, encoding):
        with self._state_lock:
            self._encoding_hint = (str(path), encoding)
            if self.current_file and os.path.normcase(self.current_file) == os.path.normcase(str(path)):
                self._encoding = encoding

    def _same_active_path(self, path) -> bool:
        with self._state_lock:
            current = self.current_file
        if not current or not path:
            return False
        try:
            return os.path.normcase(os.path.normpath(str(path))) == \
                os.path.normcase(os.path.normpath(str(current)))
        except Exception:
            return False

    # ---- 渲染 ----
    def _render_payload(self, path):
        try:
            resolved = _document_path(path)
            md_text, encoding, _mtime = _read_text_meta(resolved)
            base_dir = str(resolved.parent)
            content, toc = _get_render().render_markdown(md_text, base_dir=base_dir)
            title = resolved.name
            self._remember_encoding(resolved, encoding)
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
            resolved_s = str(resolved)
            mtime_ns = resolved.stat().st_mtime_ns
            with self._state_lock:
                self.current_file = resolved_s
                self._mtime = mtime_ns
                hint = self._encoding_hint
                if hint and os.path.normcase(hint[0]) == os.path.normcase(resolved_s):
                    self._encoding = hint[1]
            try:
                self._backend.set_represented_file(resolved_s)
            except Exception:
                pass
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- 编辑模式：源码读写 / 预览 / 插图 ----
    def get_source(self, path=None):
        """读取 Markdown 源文本（编辑模式的数据源）。"""
        try:
            target = path or self.current_file
            if not target:
                return {"ok": False, "error": "当前没有可编辑的文档"}
            # 只允许读取当前活动文档，缩小桥接面
            if path and self.current_file and not self._same_active_path(path):
                # 允许在 activate 之前按 path 读（首次进入）；但若已有活动文档则必须一致
                if self.current_file:
                    return {"ok": False, "error": "只能编辑当前打开的文档"}
            resolved = _document_path(target)
            text, encoding, mtime_ns = _read_text_meta(resolved)
            self._remember_encoding(resolved, encoding)
            with self._state_lock:
                if self.current_file and os.path.normcase(self.current_file) == os.path.normcase(str(resolved)):
                    self._mtime = mtime_ns
                # 进入编辑时先暂停监视，避免 get_source 往返期间被热重载打断
                self._watch_paused = True
            return {
                "ok": True,
                "path": str(resolved),
                "title": resolved.name,
                "text": text,
                "encoding": encoding,
                "mtime_ns": _mtime_token(mtime_ns),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_watch_paused(self, paused):
        """编辑模式中暂停文件监视自动重载，避免覆盖未保存内容。"""
        with self._state_lock:
            self._watch_paused = bool(paused)
        return {"ok": True, "paused": self._watch_paused}

    def preview_markdown(self, text, base_path=None):
        """把编辑缓冲渲染为 HTML（不写盘、不切换活动文档）。"""
        try:
            if text is None:
                text = ""
            if not isinstance(text, str):
                raise ValueError("预览内容必须是文本")
            # 预览也受体积上限约束，防止超大缓冲拖垮渲染线程
            if len(text.encode("utf-8", errors="replace")) > _MAX_DOCUMENT_BYTES:
                raise ValueError("内容过大（上限 64 MB）")
            source = base_path or self.current_file
            if source and self.current_file and not self._same_active_path(source):
                source = self.current_file
            base_dir = str(Path(source).resolve().parent) if source else str(Path.cwd())
            content, toc = _get_render().render_markdown(text, base_dir=base_dir)
            title = Path(source).name if source else APP_NAME
            return {
                "ok": True,
                "title": title,
                "content": content,
                "toc": toc,
                "path": str(Path(source).resolve()) if source else "",
            }
        except Exception as e:
            if os.environ.get("INKWELL_DEBUG") == "1":
                traceback.print_exc()
            error = html_module.escape(str(e))
            return {"ok": False, "error": str(e), "title": "错误",
                    "content": f"<h1>预览失败</h1><pre>{error}</pre>", "toc": ""}

    def save_document(self, text, path=None, expected_mtime_ns=None):
        """保存编辑缓冲并返回重新渲染后的 payload。

        仅允许写入当前活动文档（path 必须与 current_file 一致或省略）。
        mtime 以字符串过桥，避免 JS Number 丢 ns 精度导致伪冲突。
        """
        try:
            if text is None:
                text = ""
            if not isinstance(text, str):
                raise ValueError("文档内容必须是文本")
            with self._state_lock:
                target = self.current_file
            if path:
                if target and not self._same_active_path(path):
                    return {"ok": False, "error": "只能保存当前打开的文档"}
                if not target:
                    target = path
            if not target:
                return {"ok": False, "error": "当前没有可保存的文档路径"}
            resolved = _document_path(target)
            expected = None
            try:
                expected = _coerce_mtime_ns(expected_mtime_ns)
            except (TypeError, ValueError):
                expected = None
            with self._state_lock:
                encoding = self._encoding or "utf-8"
            with self._save_lock:
                disk_mtime = resolved.stat().st_mtime_ns
                if expected is not None and expected != disk_mtime:
                    return {
                        "ok": False,
                        "conflict": True,
                        "error": "文件已被其他程序修改，请先重新加载或强制保存",
                        "mtime_ns": _mtime_token(disk_mtime),
                    }
                try:
                    new_mtime, used_enc = _write_text_atomic(
                        resolved, text, encoding,
                        expected_mtime_ns=expected,
                    )
                except FileExistsError as exc:
                    msg = str(exc)
                    if msg.startswith("CONFLICT:"):
                        cm = int(msg.split(":", 1)[1])
                        return {
                            "ok": False,
                            "conflict": True,
                            "error": "文件已被其他程序修改，请先重新加载或强制保存",
                            "mtime_ns": _mtime_token(cm),
                        }
                    raise
            self._remember_encoding(resolved, used_enc)
            with self._state_lock:
                # 自己写入后同步 mtime，避免 watcher 立刻把页面刷掉
                if self.current_file and os.path.normcase(self.current_file) == os.path.normcase(str(resolved)):
                    self._mtime = new_mtime
                elif not self.current_file:
                    self.current_file = str(resolved)
                    self._mtime = new_mtime
            payload = self._render_payload(resolved)
            payload["mtime_ns"] = _mtime_token(new_mtime)
            payload["saved"] = True
            if used_enc != encoding:
                payload["encoding_changed"] = used_enc
            return payload
        except Exception as e:
            if os.environ.get("INKWELL_DEBUG") == "1":
                traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def save_document_as(self, text):
        """另存为：弹出保存对话框后写入并切换活动文档。"""
        if getattr(self, "_closing", False):
            return {"ok": False, "cancelled": True}
        try:
            if text is None:
                text = ""
            if not isinstance(text, str):
                raise ValueError("文档内容必须是文本")
            default_dir = None
            if self.current_file:
                try:
                    default_dir = str(Path(self.current_file).resolve().parent)
                except Exception:
                    default_dir = None
            result = self._file_dialog(
                "SAVE",
                directory=default_dir,
                save_filename=Path(self.current_file).name if self.current_file else "untitled.md",
                file_types=("Markdown 与文本 (*.md;*.markdown;*.mdown;*.mkd;*.txt)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            path = result if isinstance(result, str) else (result[0] if result else None)
            if not path:
                return {"ok": False, "cancelled": True}
            # SAVE_DIALOG 可能指向尚不存在的文件；此时 _document_path 会失败。
            p = Path(path)
            if p.suffix.lower() not in _READABLE_EXTS:
                p = p.with_suffix(".md")
            if p.exists() and not p.is_file():
                return {"ok": False, "error": "目标路径不是文件"}
            if not p.parent.exists():
                return {"ok": False, "error": "目标目录不存在"}
            # 新文件默认 UTF-8，避免把 GBK 策略带到全新路径
            encoding = "utf-8"
            with self._save_lock:
                new_mtime, used_enc = _write_text_atomic(p, text, encoding)
            resolved = p.resolve()
            self._remember_encoding(resolved, used_enc)
            with self._state_lock:
                self.current_file = str(resolved)
                self._mtime = new_mtime
            payload = self._render_payload(resolved)
            payload["mtime_ns"] = _mtime_token(new_mtime)
            payload["saved"] = True
            return payload
        except Exception as e:
            if os.environ.get("INKWELL_DEBUG") == "1":
                traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def pick_image(self, mode="file"):
        """选择图片并返回可插入 Markdown 的片段。

        mode:
          - file（默认）：复制到文档旁 images/ 目录，返回相对路径语法
          - embed：以 data URI 内嵌（注意体积；适合单文件分发）
        """
        if getattr(self, "_closing", False):
            return {"ok": False, "cancelled": True}
        try:
            if not self.current_file:
                return {"ok": False, "error": "请先打开一个文档再插入图片"}
            doc = _document_path(self.current_file)
            result = self._file_dialog(
                "OPEN",
                allow_multiple=False,
                file_types=("图片 (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp;*.svg;*.tif;*.tiff;*.ico;*.avif)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            src_path = result[0] if isinstance(result, (list, tuple)) else result
            src = Path(src_path).resolve(strict=True)
            if not src.is_file():
                return {"ok": False, "error": "图片文件不存在"}
            ext = src.suffix.lower()
            if ext not in _IMAGE_PICK_EXTS:
                return {"ok": False, "error": "不支持的图片类型"}
            size = src.stat().st_size
            if size <= 0:
                return {"ok": False, "error": "图片为空"}
            # 文件模式也限制体积，避免误选超大资源
            if size > _MAX_EMBED_IMAGE_BYTES * 4:
                return {"ok": False, "error": "图片过大（上限 32 MB）"}
            if size > _MAX_EMBED_IMAGE_BYTES and str(mode).lower() == "embed":
                return {"ok": False, "error": "内嵌图片不能超过 8 MB，请改用「插图」"}

            alt = src.stem
            # 清理 alt 中的 ] 等，避免破坏 ![alt](...) 边界
            alt = re.sub(r"[\r\n\[\]]+", " ", alt).strip() or "image"

            if str(mode).lower() == "embed":
                import base64
                data = src.read_bytes()
                if len(data) > _MAX_EMBED_IMAGE_BYTES:
                    return {"ok": False, "error": "内嵌图片不能超过 8 MB"}
                mime = _IMAGE_MIME.get(ext, "application/octet-stream")
                b64 = base64.b64encode(data).decode("ascii")
                markdown = _md_image_markdown(alt, f"data:{mime};base64,{b64}")
                return {
                    "ok": True,
                    "mode": "embed",
                    "markdown": markdown,
                    "alt": alt,
                    "bytes": len(data),
                }

            # 相对路径模式：拷到文档目录下 images/
            images_dir = doc.parent / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            # 文件名清洗：去掉空格与括号，避免破坏 MD 图片语法
            safe_stem = re.sub(r"[^\w.\-]+", "-", src.stem, flags=re.UNICODE).strip("-._") or "image"
            dest_name = f"{safe_stem}{ext}"
            dest = images_dir / dest_name
            if dest.exists():
                dest_name = f"{safe_stem}-{uuid.uuid4().hex[:8]}{ext}"
                dest = images_dir / dest_name
            shutil.copy2(src, dest)
            rel = Path("images") / dest_name
            # Markdown 中统一用正斜杠，跨工具兼容更好
            rel_posix = rel.as_posix()
            markdown = _md_image_markdown(alt, rel_posix)
            return {
                "ok": True,
                "mode": "file",
                "markdown": markdown,
                "path": str(dest),
                "relative": rel_posix,
                "alt": alt,
            }
        except Exception as e:
            if os.environ.get("INKWELL_DEBUG") == "1":
                traceback.print_exc()
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

    def _file_dialog(self, kind, **kwargs):
        """兼容 pywebview 新旧 FileDialog API。"""
        dialog = None
        file_dialog = getattr(webview, "FileDialog", None)
        if file_dialog is not None:
            dialog = getattr(file_dialog, kind, None)
        if dialog is None:
            # 旧版常量：OPEN_DIALOG / SAVE_DIALOG
            dialog = getattr(webview, kind + "_DIALOG", None)
        if dialog is None:
            raise RuntimeError("当前 pywebview 不支持文件对话框")
        return self._window.create_file_dialog(dialog, **kwargs)

    def open_dialog(self):
        """弹系统文件选择框，选中后渲染并返回 payload。"""
        if getattr(self, "_closing", False):
            return {"ok": False, "cancelled": True}
        try:
            result = self._file_dialog(
                "OPEN",
                allow_multiple=False,
                # 描述只能含「单词字符+空格」：pywebview 6.1 的 parse_file_type 用
                # ^([\w ]+)\( 校验，描述里出现 '/' 等标点会抛错被吞掉→对话框返回 None
                # （表现为「打开文件」按钮点了没反应）。故用「与」连接，避免斜杠。
                file_types=("Markdown 与文本 (*.md;*.markdown;*.mdown;*.mkd;*.txt)",),
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
                    open_local_file(abspath)
                    return {"ok": True}
                return {"ok": False, "error": "出于安全考虑未打开该类型文件：%s" % abspath}
            return {"ok": False, "error": "无法打开：%s" % href}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def copy_image(self, source):
        """前端 Clipboard API 不可用时，复制本进程已本地化的图片资源。"""
        if getattr(self, "_closing", False):
            return {"ok": False, "error": "正在退出"}
        path = _image_asset_path_for_copy(source)
        if path is None:
            return {"ok": False, "error": "仅能复制当前文档中的本地或内嵌图片"}
        try:
            copy_image_to_clipboard(path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_boot(self):
        return {"name": APP_NAME, "version": __version__,
                "path": self.current_file,
                "title": Path(self.current_file).name if self.current_file else APP_NAME,
                "preferences": dict(self.preferences),
                "platform": sys.platform}

    def _build_startup_payload(self):
        with self._state_lock:
            cached = self._initial_payload
        if cached is not None:
            return cached
        path = self._init_path
        pending = self._pending_mac_files
        if not path and pending:
            path = pending[-1]
            pending.clear()
        if path:
            payload = self._render_payload(path)
        else:
            try:
                rendered, rendered_toc = _get_render().render_markdown(
                    WELCOME_MD, base_dir=str(Path.cwd()))
                payload = {"ok": True, "title": APP_NAME,
                           "content": rendered, "toc": rendered_toc, "path": ""}
            except Exception as exc:
                error = html_module.escape(str(exc))
                payload = {"ok": False, "title": "错误",
                           "content": f"<h1>无法打开欢迎页</h1><pre>{error}</pre>", "toc": ""}
        with self._state_lock:
            self._initial_payload = payload
        return payload

    def pull_initial(self):
        """前端在 pywebviewready 后拉取首屏；WKWebView 上 evaluate_js/eval 可能被 CSP 拦住。"""
        if getattr(self, "_closing", False):
            return {"ok": False, "cancelled": True}
        return self._build_startup_payload()

    # ---- 窗口控制（Windows: Win32 无边框缩放/Snap；macOS: Cocoa NSWindow）----
    def win_minimize(self):
        try:
            self._window.minimize()
        except Exception:
            pass

    def win_toggle_maximize(self):
        self._backend.toggle_maximize()

    def win_is_maximized(self):
        try:
            return bool(self._backend.is_maximized())
        except Exception:
            return False

    def win_close(self):
        self._closing = True
        if IS_MACOS:
            from .host_mac import schedule_window_close
            schedule_window_close(self._window)
            return
        try:
            self._window.destroy()
        except Exception:
            pass

    def init_native_chrome(self):
        self._backend.init_chrome()

    def win_native_drag(self):
        self._backend.native_drag()

    def win_native_resize(self, edge):
        self._backend.native_resize(edge)


def _watch_file(api: Api):
    """后台轮询当前文件 mtime，变化时推送新内容到前端。"""
    while True:
        time.sleep(1.0)
        if getattr(api, "_closing", False):
            return
        with api._state_lock:
            path = api.current_file
            previous_mtime = api._mtime
            paused = api._watch_paused
        if not path or paused:
            # 编辑模式暂停监视，避免外部 mtime 变化冲掉未保存缓冲。
            continue
        try:
            mt = os.stat(path).st_mtime_ns
        except OSError:
            continue
        if previous_mtime is not None and mt != previous_mtime:
            payload = api._render_payload(path)
            with api._state_lock:
                # 渲染期间用户可能已经切换文档；旧结果不得覆盖新页面或活动路径。
                if api.current_file != path or api._watch_paused:
                    continue
                api._mtime = mt
            _inject_js(api._window, _js_call("__applyPayload", payload))


def _initial_file(argv):
    for a in argv[1:]:
        if a and not a.startswith("-"):
            try:
                return str(_document_path(a))
            except (OSError, ValueError):
                continue
    return None


def main():
    _ensure_loopback_noproxy()
    if IS_MACOS:
        from .host_mac import configure_app_identity
        configure_app_identity()
    api = Api()

    init_path = _initial_file(sys.argv)
    api._init_path = init_path
    if init_path:
        title = Path(init_path).name
    else:
        title = APP_NAME
    # 先给 WebView 一个极轻的首帧；Markdown/Pygments 导入、base64 解码和正文
    # 渲染全部移到页面加载后的后台线程，尤其能改善大图片文档的感知启动速度。
    loading_name = html_module.escape(title)
    content = (f'<div class="initial-loading" role="status" aria-live="polite">'
               f'<span class="initial-loading-spinner"></span>'
               f'<span>正在打开 {loading_name}…</span></div>')
    toc = ""

    _server.set_page(build_page(content, toc, title, init_path,
                                preferences=api.preferences))
    httpd, url = _server.start_server()

    storage = str(app_data_dir() / "webview")
    try:
        os.makedirs(storage, exist_ok=True)
    except Exception:
        storage = None

    pending_mac_files = api._pending_mac_files
    page_loaded = threading.Event()

    def _open_from_finder(paths):
        def work():
            if getattr(api, "_closing", False):
                return
            chosen = None
            for p in paths:
                try:
                    chosen = str(_document_path(p))
                    break
                except (OSError, ValueError):
                    continue
            if not chosen:
                return
            with api._state_lock:
                api._init_path = chosen
                api._initial_payload = None
                api._open_seq += 1
                seq = api._open_seq
                window_ready = api._window is not None and page_loaded.is_set()
                if not window_ready:
                    if chosen not in pending_mac_files:
                        pending_mac_files.append(chosen)
                    return
            with api._load_lock:
                payload = api._render_payload(chosen)
                with api._state_lock:
                    if seq != api._open_seq:
                        return
                _inject_js(api._window, _js_call("__openFromFinder", payload))

        if IS_MACOS:
            from .host_mac import run_off_main
            run_off_main(work, name="InkwellOpenFromFinder")
        else:
            work()

    if IS_MACOS:
        from .host_mac import install_open_file_handler
        install_open_file_handler(_open_from_finder)

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
        background_color="#1E1D1A" if api.preferences.get("theme") == "dark" else "#F7F5EF",
        confirm_close=False,
        zoomable=False,
    )
    api._window = window

    initial_render_started = threading.Event()

    def _begin_initial_render(*_args):
        if initial_render_started.is_set():
            return
        initial_render_started.set()
        page_loaded.set()

        def work():
            if getattr(api, "_closing", False):
                return
            with api._load_lock:
                payload = api._build_startup_payload()
                _inject_js(window, _js_call("__applyInitialPayload", payload))
                late = None
                with api._state_lock:
                    if api._pending_mac_files:
                        late = api._pending_mac_files[-1]
                        api._pending_mac_files.clear()
                if late and payload.get("path") != late:
                    _inject_js(window, _js_call("__openFromFinder", api._render_payload(late)))

        threading.Thread(target=work, name="InkwellInitialRender", daemon=True).start()

    def _loaded_watchdog():
        if initial_render_started.wait(2.5):
            return
        _begin_initial_render()

    watcher = threading.Thread(target=_watch_file, args=(api,), daemon=True)
    watcher.start()
    threading.Thread(target=_loaded_watchdog, name="InkwellLoadedWatchdog",
                     daemon=True).start()

    debug = os.environ.get("INKWELL_DEBUG") == "1"

    def _on_closed():
        api._closing = True
        if IS_MACOS:
            from .host_mac import mark_exiting
            mark_exiting()
        if _RENDER_MODULE is not None:
            try:
                _RENDER_MODULE.cleanup_assets()
            except Exception:
                pass

        def stop_http():
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass

        stopper = threading.Thread(target=stop_http, name="InkwellHttpShutdown",
                                   daemon=True)
        stopper.start()
        stopper.join(1.5)

    window.events.closed += _on_closed
    window.events.loaded += _begin_initial_render
    window.events.shown += lambda *a: api.init_native_chrome()

    close_after = os.environ.get("INKWELL_SELFTEST_CLOSE")
    if close_after:
        try:
            delay = float(close_after)
        except ValueError:
            delay = 2.0

        def _self_close():
            time.sleep(max(0.2, delay))
            try:
                api.win_close()
            except Exception:
                pass

        threading.Thread(target=_self_close, name="InkwellSelfTestClose",
                         daemon=True).start()

    start_kwargs = dict(debug=debug, private_mode=False, storage_path=storage)
    gui = webview_gui()
    if gui:
        start_kwargs["gui"] = gui
    if IS_MACOS:
        icns = Path(__file__).resolve().parent / "assets" / "icon.icns"
        if icns.is_file():
            start_kwargs["icon"] = str(icns)
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
