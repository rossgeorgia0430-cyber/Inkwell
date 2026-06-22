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

from . import render as _render


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


class _Handler(BaseHTTPRequestHandler):
    server_version = "Inkwell/1.1.4"

    def log_message(self, *args):
        pass  # 静默

    def _send_bytes(self, data: bytes, content_type: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _send_file(self, path: Path):
        try:
            data = path.read_bytes()
        except Exception:
            self._send_bytes(b"not found", "text/plain; charset=utf-8", 404)
            return
        self._send_bytes(data, _guess_type(path))

    def do_GET(self):
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

        if path.startswith(_render.IMG_URL_PREFIX):
            if _render.ASSETS_DIR is None:
                self._send_bytes(b"no img dir", "text/plain; charset=utf-8", 404)
                return
            target = _safe_join(_render.ASSETS_DIR, path[len(_render.IMG_URL_PREFIX):])
            if target and target.is_file():
                self._send_file(target)
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
