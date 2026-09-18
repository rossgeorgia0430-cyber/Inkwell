"""JS 桥：window.pywebview.api 暴露给前端的方法（class Api）。"""

import base64
import functools
import os
import re
import shutil
import threading
import uuid
import webbrowser
import html as html_module
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import webview

from . import APP_NAME, log_exception
from . import documents
from . import images
from .host import (
    IS_MACOS,
    copy_image_to_clipboard,
    create_window_backend,
    open_local_file,
)

# 允许通过系统默认程序打开的本地文件类型（白名单）——避免不可信文档里的链接
# 直接拉起 .exe/.bat/.ps1 等可执行/脚本文件。
_SAFE_OPEN_EXTS = {
    ".pdf", ".txt", ".csv", ".json", ".xml", ".log", ".rtf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff", ".ico",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".htm", ".html", ".mp3", ".wav", ".mp4", ".mov", ".webm",
}
_MAX_EMBED_IMAGE_BYTES = 8 * 1024 * 1024


def _welcome_md():
    mod = "⌘" if IS_MACOS else "Ctrl"
    return f"""# 欢迎使用 Inkwell

这是一个本地运行的 **Markdown 阅读器**。

- 点击左上角 **目录** 按钮可折叠/展开侧栏
- 按 **{mod}+F** 搜索，**{mod}+B** 切换目录，**{mod}+O** 打开文件
- 右上角可切换 **浅色 / 深色** 主题
- 代码块里 **双击**任意标识符会高亮同名 token
- 选中文字复制到飞书等文档**不会带底色和彩色**；公式可直接复制为 LaTeX
- 点击图片会按当前窗口自适应放大；底部按钮或 **{mod}+滚轮** 可继续缩放
- 图片右上角可复制，选中后按 **{mod}+C** 也可直接复制

> 用「打开文件」按钮选择一个 `.md` 文件开始，或直接双击任意 Markdown 文件。

```python
def hello(name: str) -> str:
    return f"Hello, {{name}}!"
```

行内公式 $E = mc^2$，块级公式：

$$\\int_{{-\\infty}}^{{\\infty}} e^{{-x^2}}\\,dx = \\sqrt{{\\pi}}$$
"""


WELCOME_MD = _welcome_md()


def _get_render():
    """延迟加载 markdown/Pygments 渲染栈，让原生窗口更早出现。

    重复调用只是 sys.modules 命中，不必自行缓存模块对象。
    """
    from . import render
    return render


def _error_payload(exc, heading):
    """渲染/预览失败时的统一错误 payload：标题固定为『错误』，正文是转义后的异常信息。"""
    error = html_module.escape(str(exc))
    return {"ok": False, "error": str(exc), "title": "错误",
            "content": f"<h1>{heading}</h1><pre>{error}</pre>", "toc": ""}


def _conflict_payload(mtime_ns):
    return {
        "ok": False,
        "conflict": True,
        "error": "文件已被其他程序修改，请先重新加载或强制保存",
        "mtime_ns": documents.mtime_token(mtime_ns),
    }


def _coerce_text(value, what="文档内容"):
    """校验桥接层传入的文本：None 视为空串，非字符串报错。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{what}必须是文本")
    return value


def _same_path(a, b) -> bool:
    """比较两个路径是否指向同一位置（大小写不敏感 + 规范化分隔符/相对片段）。"""
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _bridge_call(fn):
    """JS 桥方法的统一异常边界：捕获后返回 {"ok": False, "error": str(e)}。

    这是合法的错误处理边界（JS 桥入口，前端依赖该返回契约），但边界处仍需可诊断。
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:
            log_exception()
            return {"ok": False, "error": str(e)}
    return wrapper


class Api:
    """暴露给前端 window.pywebview.api 的方法。"""

    def __init__(self):
        self._window = None
        self.current_file = None
        self._backend = create_window_backend(self)
        self._mtime = None
        self._encoding = "utf-8"
        self._encoding_hint = None  # (path, encoding)：最近一次成功读取/渲染时探测到的编码
        self._watch_paused = False
        self._save_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._load_lock = threading.Lock()
        self.preferences = documents.load_preferences()
        self._init_path = None
        self._pending_open_path = None
        self._initial_delivered = False
        self._open_seq = 0
        self._closing = False

    def _remember_encoding(self, path, encoding):
        with self._state_lock:
            self._encoding_hint = (str(path), encoding)
            if self.current_file and _same_path(self.current_file, path):
                self._encoding = encoding

    def _same_active_path(self, path) -> bool:
        with self._state_lock:
            current = self.current_file
        if not current or not path:
            return False
        return _same_path(path, current)

    # ---- 渲染 ----
    def _render_payload(self, path):
        try:
            resolved = documents.document_path(path)
            md_text, encoding, _mtime = documents.read_text_meta(resolved)
            base_dir = str(resolved.parent)
            content, toc = _get_render().render_markdown(md_text, base_dir=base_dir)
            title = resolved.name
            self._remember_encoding(resolved, encoding)
            return {"ok": True, "title": title, "content": content,
                    "toc": toc, "path": str(resolved)}
        except Exception as e:
            log_exception()
            return _error_payload(e, "无法打开文件")

    def render_path(self, path):
        """前端请求渲染某个文件，返回 payload。"""
        return self._render_payload(path)

    @_bridge_call
    def activate_path(self, path):
        """前端确认 payload 已显示后，再切换 watcher 与相对链接的活动文档。"""
        resolved = documents.document_path(path)
        resolved_s = str(resolved)
        mtime_ns = resolved.stat().st_mtime_ns
        with self._state_lock:
            self.current_file = resolved_s
            self._mtime = mtime_ns
            hint = self._encoding_hint
            if hint and _same_path(hint[0], resolved_s):
                self._encoding = hint[1]
        self._backend.set_represented_file(resolved_s)
        return {"ok": True}

    # ---- 编辑模式：源码读写 / 预览 / 插图 ----
    @_bridge_call
    def get_source(self, path=None):
        """读取 Markdown 源文本（编辑模式的数据源）。"""
        target = path or self.current_file
        if not target:
            return {"ok": False, "error": "当前没有可编辑的文档"}
        # 只允许读取当前活动文档，缩小桥接面；允许在 activate 之前按 path 读（首次进入）
        if path and self.current_file and not self._same_active_path(path):
            return {"ok": False, "error": "只能编辑当前打开的文档"}
        resolved = documents.document_path(target)
        text, encoding, mtime_ns = documents.read_text_meta(resolved)
        self._remember_encoding(resolved, encoding)
        with self._state_lock:
            if self.current_file and _same_path(self.current_file, resolved):
                self._mtime = mtime_ns
            # 进入编辑时先暂停监视，避免 get_source 往返期间被热重载打断
            self._watch_paused = True
        return {
            "ok": True,
            "path": str(resolved),
            "title": resolved.name,
            "text": text,
            "encoding": encoding,
            "mtime_ns": documents.mtime_token(mtime_ns),
        }

    def set_watch_paused(self, paused):
        """编辑模式中暂停文件监视自动重载，避免覆盖未保存内容。"""
        with self._state_lock:
            self._watch_paused = bool(paused)
        return {"ok": True, "paused": self._watch_paused}

    def preview_markdown(self, text, base_path=None):
        """把编辑缓冲渲染为 HTML（不写盘、不切换活动文档）。"""
        try:
            text = _coerce_text(text, "预览内容")
            # 预览也受体积上限约束，防止超大缓冲拖垮渲染线程
            if len(text.encode("utf-8", errors="replace")) > documents.MAX_DOCUMENT_BYTES:
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
            log_exception()
            return _error_payload(e, "预览失败")

    @_bridge_call
    def save_document(self, text, path=None, expected_mtime_ns=None):
        """保存编辑缓冲并返回重新渲染后的 payload。

        仅允许写入当前活动文档（path 必须与 current_file 一致或省略）。
        mtime 以字符串过桥，避免 JS Number 丢 ns 精度导致伪冲突。
        """
        text = _coerce_text(text)
        with self._state_lock:
            target = self.current_file
        if path:
            if target and not self._same_active_path(path):
                return {"ok": False, "error": "只能保存当前打开的文档"}
            if not target:
                target = path
        if not target:
            return {"ok": False, "error": "当前没有可保存的文档路径"}
        resolved = documents.document_path(target)
        try:
            expected = documents.coerce_mtime_ns(expected_mtime_ns)
        except (TypeError, ValueError):
            # 非法 mtime 绝不能当成"不校验冲突"直接覆盖磁盘文件
            return {"ok": False, "error": "无效的 mtime"}
        with self._state_lock:
            encoding = self._encoding or "utf-8"
        with self._save_lock:
            try:
                new_mtime, used_enc = documents.write_text_atomic(
                    resolved, text, encoding, expected_mtime_ns=expected)
            except documents.SaveConflict as exc:
                return _conflict_payload(exc.mtime_ns)
        self._remember_encoding(resolved, used_enc)
        with self._state_lock:
            # 自己写入后同步 mtime，避免 watcher 立刻把页面刷掉
            if self.current_file and _same_path(self.current_file, resolved):
                self._mtime = new_mtime
            elif not self.current_file:
                self.current_file = str(resolved)
                self._mtime = new_mtime
        payload = self._render_payload(resolved)
        payload["mtime_ns"] = documents.mtime_token(new_mtime)
        payload["saved"] = True
        if used_enc != encoding:
            payload["encoding_changed"] = used_enc
        return payload

    def _embed_image_as_data_uri(self, src, ext, alt):
        data = src.read_bytes()
        if len(data) > _MAX_EMBED_IMAGE_BYTES:
            return {"ok": False, "error": "内嵌图片不能超过 8 MB"}
        mime = images.mime_for_ext(ext)
        b64 = base64.b64encode(data).decode("ascii")
        markdown = documents.md_image_markdown(alt, f"data:{mime};base64,{b64}")
        return {"ok": True, "mode": "embed", "markdown": markdown, "alt": alt, "bytes": len(data)}

    def _copy_image_to_document_dir(self, src, doc, ext, alt):
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
        rel_posix = (Path("images") / dest_name).as_posix()  # 跨工具兼容更好
        markdown = documents.md_image_markdown(alt, rel_posix)
        return {"ok": True, "mode": "file", "markdown": markdown, "path": str(dest),
                "relative": rel_posix, "alt": alt}

    @_bridge_call
    def pick_image(self, mode="file"):
        """选择图片并返回可插入 Markdown 的片段。

        mode:
          - file（默认）：复制到文档旁 images/ 目录，返回相对路径语法
          - embed：以 data URI 内嵌（注意体积；适合单文件分发）
        """
        if self._closing:
            return {"ok": False, "cancelled": True}
        if not self.current_file:
            return {"ok": False, "error": "请先打开一个文档再插入图片"}
        doc = documents.document_path(self.current_file)
        result = self._file_dialog(allow_multiple=False, file_types=(images.IMAGE_DIALOG_FILTER,))
        if not result:
            return {"ok": False, "cancelled": True}
        src_path = result[0] if isinstance(result, (list, tuple)) else result
        src = Path(src_path).resolve(strict=True)
        if not src.is_file():
            return {"ok": False, "error": "图片文件不存在"}
        ext = src.suffix.lower()
        if ext not in images.IMAGE_EXTS:
            return {"ok": False, "error": "不支持的图片类型"}
        size = src.stat().st_size
        if size <= 0:
            return {"ok": False, "error": "图片为空"}
        # 文件模式也限制体积，避免误选超大资源
        if size > _MAX_EMBED_IMAGE_BYTES * 4:
            return {"ok": False, "error": "图片过大（上限 32 MB）"}
        embed = str(mode).lower() == "embed"
        if size > _MAX_EMBED_IMAGE_BYTES and embed:
            return {"ok": False, "error": "内嵌图片不能超过 8 MB，请改用「插图」"}

        alt = src.stem
        if embed:
            return self._embed_image_as_data_uri(src, ext, alt)
        return self._copy_image_to_document_dir(src, doc, ext, alt)

    @_bridge_call
    def set_preference(self, key, value):
        """把主题/字号写入宿主配置，避免随机 HTTP 端口导致 localStorage 失忆。"""
        value = documents.validate_preference(key, value)
        with self._state_lock:
            self.preferences[key] = value
            documents.save_preferences(self.preferences)
        return {"ok": True}

    def _file_dialog(self, **kwargs):
        """弹出系统文件选择框（仅用于打开文件）。"""
        return self._window.create_file_dialog(webview.FileDialog.OPEN, **kwargs)

    @_bridge_call
    def open_dialog(self):
        """弹系统文件选择框，选中后渲染并返回 payload。"""
        if self._closing:
            return {"ok": False, "cancelled": True}
        result = self._file_dialog(
            allow_multiple=False,
            # 描述只能含「单词字符+空格」：pywebview 6.1 的 parse_file_type 用
            # ^([\w ]+)\( 校验，描述里出现 '/' 等标点会抛错被吞掉→对话框返回 None
            # （表现为「打开文件」按钮点了没反应）。故用「与」连接，避免斜杠。
            file_types=(documents.DOCUMENT_DIALOG_FILTER,),
        )
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
        except (OSError, ValueError, RuntimeError):
            return None, anchor

    @_bridge_call
    def open_md_link(self, href, base_path=None):
        """点击正文里指向本地 .md 的链接：解析并渲染目标文档，返回 payload（含 anchor）。"""
        if not href:
            return {"ok": False, "error": "空链接"}
        abspath, anchor = self._resolve_local(href, base_path)
        if abspath is None:  # 纯锚点（#section）
            return {"ok": False, "samedoc": True, "anchor": anchor}
        # 同一文档内的锚点跳转，不重复渲染
        source_path = base_path or self.current_file
        if source_path and _same_path(abspath, source_path):
            return {"ok": False, "samedoc": True, "anchor": anchor}
        payload = self._render_payload(abspath)
        payload["anchor"] = anchor
        return payload

    @_bridge_call
    def open_external(self, href):
        """外部 URL → 系统浏览器；本地非 .md 文件 → 系统默认程序（白名单内）。"""
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

    @_bridge_call
    def copy_image(self, source):
        """前端 Clipboard API 不可用时，复制本进程已本地化的图片资源。"""
        if self._closing:
            return {"ok": False, "error": "正在退出"}
        path = images.asset_path_for_url(source)
        if path is None:
            return {"ok": False, "error": "仅能复制当前文档中的本地或内嵌图片"}
        copy_image_to_clipboard(path)
        return {"ok": True}

    def _render_startup_payload(self, path):
        if path:
            return self._render_payload(path)
        try:
            rendered, rendered_toc = _get_render().render_markdown(
                WELCOME_MD, base_dir=str(Path.cwd()))
            return {"ok": True, "title": APP_NAME, "content": rendered,
                    "toc": rendered_toc, "path": ""}
        except Exception as exc:
            log_exception()
            return _error_payload(exc, "无法打开欢迎页")

    def pull_initial(self):
        """前端在 pywebviewready 后拉取首屏；WKWebView 上 evaluate_js/eval 可能被 CSP 拦住。

        首屏渲染期间若 Finder 送来新文件会先记到 _pending_open_path；这里在同一把
        _load_lock 下循环重建，直到没有新文件到达再标记"首屏已交付"并返回。
        """
        if self._closing:
            return {"ok": False, "cancelled": True}
        with self._load_lock:
            path = self._init_path
            while True:
                payload = self._render_startup_payload(path)
                with self._state_lock:
                    pending = self._pending_open_path
                    self._pending_open_path = None
                    if pending is None:
                        self._initial_delivered = True
                        return payload
                    path = pending
                    self._init_path = path

    def _accept_finder_path(self, path):
        """macOS Finder/Open With 投递的文件路径。

        首屏尚未交付（pull_initial 还没完成）时只记下待打开路径，返回 None 交给
        pull_initial 一并处理；已交付则渲染并返回要注入前端的 payload，经
        _open_seq 去重——渲染期间若又有更新的文件到达，旧的渲染结果作废。
        """
        with self._state_lock:
            if not self._initial_delivered:
                self._pending_open_path = path
                return None
            self._open_seq += 1
            seq = self._open_seq
        with self._load_lock:
            payload = self._render_payload(path)
            with self._state_lock:
                if seq != self._open_seq:
                    return None
            return payload

    def _poll_file_change(self):
        """轮询用：当前活动文档的磁盘 mtime 若有变化则返回新 payload，否则返回 None。"""
        with self._state_lock:
            path = self.current_file
            previous_mtime = self._mtime
            paused = self._watch_paused
        if not path or paused:
            return None
        try:
            mt = os.stat(path).st_mtime_ns
        except OSError:
            return None
        if previous_mtime is None or mt == previous_mtime:
            return None
        payload = self._render_payload(path)
        with self._state_lock:
            # 渲染期间用户可能已经切换文档；旧结果不得覆盖新页面或活动路径。
            if self.current_file != path or self._watch_paused:
                return None
            self._mtime = mt
        return payload

    # ---- 窗口控制（Windows: Win32 无边框缩放/Snap；macOS: Cocoa NSWindow）----
    def win_minimize(self):
        # pywebview 的 js_bridge_call 本身会捕获并记录桥方法异常，前端也不看这
        # 两个窗口控制方法的返回值，这里不需要再包一层。
        self._window.minimize()

    def win_toggle_maximize(self):
        self._backend.toggle_maximize()

    def win_is_maximized(self):
        # 契约是返回 bool（前端 !!result），不能套用返回 dict 的 _bridge_call
        try:
            return bool(self._backend.is_maximized())
        except Exception:
            log_exception()
            return False

    def win_close(self):
        self._closing = True
        if IS_MACOS:
            from .host_mac import schedule_window_close
            schedule_window_close(self._window)
            return
        self._window.destroy()

    def _init_native_chrome(self):
        self._backend.init_chrome()

    def win_native_drag(self):
        self._backend.native_drag()

    def win_native_resize(self, edge):
        self._backend.native_resize(edge)
