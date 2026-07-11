#!/usr/bin/env python3
"""
Inkwell - 内置静态/页面服务器
在本地回环地址提供：
  /                -> 当前页面 HTML（内存中，由 app 设置）
  /assets/<path>  -> 打包资源（app.css/app.js/katex/pygments-*.css）
  /__img__/<name> -> 渲染时本地化的图片（render.ASSETS_DIR）

用自建服务器（而非 pywebview 内置 http_server）以便完全掌控路由，并避免每次启动
拷贝 KaTeX 字体；窗口以 http://127.0.0.1:<port>/ 方式加载。
"""

import os
import sys
import threading
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

IMG_URL_PREFIX = "/__img__/"


def _render_assets_dir():
    # 延迟导入 markdown/Pygments 渲染栈；首个页面外壳不需要它们。
    from . import render
    return render.ASSETS_DIR


def asset_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "inkwell" / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "assets"


ASSET_ROOT = asset_root()

# 共享状态：app 通过 set_page() 更新当前页面 HTML
_STATE = {"page_html": "<!doctype html><meta charset=utf-8><title>Inkwell</title>"}

_EXTRA_MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


def set_page(html_text: str):
    _STATE["page_html"] = html_text


def _guess_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    typ, _ = mimetypes.guess_type(str(path))
    return typ or "application/octet-stream"


def _safe_join(base: Path, rel: str):
    """把 rel 安全拼到 base 下，阻止 .. 越界。返回解析后的路径或 None。"""
    rel = rel.lstrip("/\\")
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target


# /__img__/ 目录会提供用户文档中引用/内嵌的图片，其中包括 SVG。SVG 本质上是可以带
# <script> 的 XML 文档：如果这类文件被当作顶级文档直接打开（而不是通过 <img> 加载），
# 脚本会在与应用页面相同的源（127.0.0.1:<port>）下执行，从而有机会接触到
# window.pywebview 提供的 JS 桥。附加一个 sandbox 的 CSP 后，即使 SVG 被作为顶级文档
# 打开也无法执行脚本/表单/弹窗等；作为 <img> 元素加载时浏览器本就不执行 SVG 脚本，
# 因此正常渲染不受影响。
_IMG_EXTRA_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}

# Host 头允许的取值（DNS rebinding 加固）：只信任回环地址本身，忽略端口号。
# WebView2 请求会带 Host: 127.0.0.1:<port> 这类带端口的形式，需要在比较前把端口拆掉。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _host_allowed(host_header: str) -> bool:
    """校验请求的 Host 头是否为本机回环地址，抵御 DNS rebinding 攻击。"""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host in _ALLOWED_HOSTS:
        return True
    # 带端口号的形式：127.0.0.1:51234 / localhost:51234 / [::1]:51234
    if host.startswith("[::1]:"):
        return True
    if ":" in host:
        hostname = host.rsplit(":", 1)[0]
        if hostname in ("127.0.0.1", "localhost"):
            return True
    return False


class _Handler(BaseHTTPRequestHandler):
    server_version = "Inkwell/1.2.0"

    def log_message(self, *args):
        pass  # 静默

    def _send_bytes(self, data: bytes, content_type: str, code: int = 200,
                     extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _send_file(self, path: Path, extra_headers=None):
        try:
            data = path.read_bytes()
        except Exception:
            self._send_bytes(b"not found", "text/plain; charset=utf-8", 404)
            return
        self._send_bytes(data, _guess_type(path), extra_headers=extra_headers)

    def do_GET(self):
        # DNS rebinding 加固：拒绝 Host 头不是本机回环地址的请求，避免恶意网页通过
        # 把域名解析指向 127.0.0.1 来跨源访问本地服务器。
        if not _host_allowed(self.headers.get("Host", "")):
            self._send_bytes(b"forbidden", "text/plain; charset=utf-8", 403)
            return

        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        if path in ("/", "/index.html"):
            self._send_bytes(_STATE["page_html"].encode("utf-8"),
                             "text/html; charset=utf-8")
            return

        if path.startswith("/assets/"):
            target = _safe_join(ASSET_ROOT, path[len("/assets/"):])
            if target and target.is_file():
                self._send_file(target)
                return
            self._send_bytes(b"asset not found", "text/plain; charset=utf-8", 404)
            return

        if path.startswith(IMG_URL_PREFIX):
            assets_dir = _render_assets_dir()
            if assets_dir is None:
                self._send_bytes(b"no img dir", "text/plain; charset=utf-8", 404)
                return
            target = _safe_join(assets_dir, path[len(IMG_URL_PREFIX):])
            if target and target.is_file():
                self._send_file(target, extra_headers=_IMG_EXTRA_HEADERS)
                return
            self._send_bytes(b"img not found", "text/plain; charset=utf-8", 404)
            return

        self._send_bytes(b"not found", "text/plain; charset=utf-8", 404)


def start_server(host: str = "127.0.0.1", port: int = 0):
    """启动后台线程服务器，返回 (server, url)。port=0 自动选空闲端口。"""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    actual_port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://{host}:{actual_port}/"
