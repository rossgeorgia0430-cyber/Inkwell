"""图片资源：本地化、data URI 落盘、文件对话框过滤串。

本模块不 import markdown/pygments，供 server.py / api.py 直接（非延迟）导入。
"""

import base64
import hashlib
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import url2pathname

IMG_URL_PREFIX = "/__img__/"
# 本进程的图片临时目录：渲染时本地化的图片、data URI 落盘后的图片都放这里，
# 由内置服务器在 IMG_URL_PREFIX 下提供。进程退出时 cleanup_assets() 整体删除。
ASSETS_DIR = Path(tempfile.gettempdir()) / f"inkwell_img_{os.getpid()}_{uuid.uuid4().hex}"
_ASSET_CACHE = {}

try:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    ASSETS_DIR = None

# 唯一的“扩展名→MIME”表；IMAGE_EXTS、data URI 解码、文件对话框过滤串均由它推导。
EXT_TO_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".ico": "image/x-icon", ".avif": "image/avif",
}
# data URI 里常见的非规范 mime 写法，指向表里已有的同一种格式。
_MIME_ALIASES = {"image/jpg": ".jpg", "image/vnd.microsoft.icon": ".ico"}
_MIME_TO_EXT = {mime: ext for ext, mime in EXT_TO_MIME.items()}

IMAGE_EXTS = frozenset(EXT_TO_MIME)
IMAGE_DIALOG_FILTER = "图片 (%s)" % ";".join(f"*{ext}" for ext in EXT_TO_MIME)

_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_DATA_URI_RE = re.compile(r"^data:([^,]*),(.*)$", re.DOTALL)
# base64 形式的 data URI 内部不含逗号（base64 字母表没有逗号），可以安全地在
# 逗号分隔的 srcset 里原地整体替换；percent 编码形式则可能含逗号，与 srcset
# 自身的分隔符冲突，无法安全提取，只能整体放弃交给 sanitizer 拒绝。
_SRCSET_DATA_URI_B64_RE = re.compile(
    r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE
)


def mime_for_ext(ext):
    return EXT_TO_MIME.get(ext.lower(), "application/octet-stream")


def _ext_for_mime(mime):
    mime = (mime or "").lower()
    return _MIME_TO_EXT.get(mime) or _MIME_ALIASES.get(mime)


def cleanup_assets():
    """退出时清理本进程的图片临时目录。"""
    if ASSETS_DIR and ASSETS_DIR.exists():
        shutil.rmtree(ASSETS_DIR, ignore_errors=True)


def _looks_like_image_file(local_path: Path) -> bool:
    return local_path.suffix.lower() in IMAGE_EXTS


def _write_asset_if_absent(dest_name, produce):
    """dest_name 不存在时调用 produce(dest_path) 落盘；返回 /__img__/ URL。"""
    if ASSETS_DIR is None:
        return None
    dest_path = ASSETS_DIR / dest_name
    if not dest_path.exists():
        produce(dest_path)
    return IMG_URL_PREFIX + quote(dest_name)


def _copy_image_to_assets(local_path: Path):
    """将本地图片拷贝到临时目录，返回 /__img__/<name> URL（带缓存）。"""
    try:
        resolved = local_path.resolve()
        stat = resolved.stat()
    except OSError:
        return None

    cache_key = f"{resolved}|{stat.st_size}|{int(stat.st_mtime)}"
    cached = _ASSET_CACHE.get(cache_key)
    if cached:
        return cached

    digest = hashlib.sha1(cache_key.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    suffix = resolved.suffix.lower()
    dest_name = f"{digest}{suffix}" if suffix else digest
    try:
        url = _write_asset_if_absent(dest_name, lambda p: shutil.copy2(resolved, p))
    except OSError:
        return None
    if url:
        _ASSET_CACHE[cache_key] = url
    return url


def _localize_data_uri(src):
    """把 data: URI 解码落盘为 /__img__/ URL；非图片 mime 或解码失败返回 None。

    内嵌图片（截图粘贴、AI 工具产出等）常以 data URI 形式出现在 Markdown/HTML 中，
    体积可能很大且每次都会被内联进 DOM；落盘后复用现有的 /__img__/ 服务路径，
    既能被浏览器缓存，也让 CSP／sanitizer 不必为任意 data: 内容开口子。
    """
    if ASSETS_DIR is None:
        return None
    try:
        match = _DATA_URI_RE.match(str(src).strip())
        if not match:
            return None
        header, payload = match.group(1), match.group(2)
        if not payload:
            return None

        fields = [f.strip() for f in header.split(";")]
        mime = (fields[0] or "").lower()
        is_base64 = any(f.lower() == "base64" for f in fields[1:])
        ext = _ext_for_mime(mime)
        if ext is None:
            return None

        if is_base64:
            cleaned = re.sub(r"\s+", "", payload)
            if not cleaned:
                return None
            data = base64.b64decode(cleaned, validate=True)
        else:
            data = unquote(payload).encode("utf-8")
        if not data:
            return None

        digest = hashlib.sha1(data).hexdigest()[:20]
        dest_name = f"{digest}{ext}"
        return _write_asset_if_absent(dest_name, lambda p: p.write_bytes(data))
    except (ValueError, OSError):
        return None


def _resolve_sandbox_path(src, base_dir):
    """sandbox: 协议（外部工具产出的相对路径提示）：在文档目录及常见子目录中按文件名查找。"""
    sandbox_path = src[len("sandbox:"):]
    if sandbox_path.startswith("//"):
        sandbox_path = sandbox_path[1:]
    filename = os.path.basename(sandbox_path)
    if not filename or not base_dir:
        return src
    for sub in ("", "images", "assets"):
        loc = Path(base_dir) / sub / filename
        try:
            if loc.is_file():
                return str(loc)
        except OSError:
            continue
    return src


def localize_image_src(src, base_dir):
    """解析并本地化 Markdown/HTML 中的图片地址，返回 /__img__/ URL。

    远程 http(s)/blob 地址原样保留；data: URI 落盘复用同一套本地化逻辑；
    sandbox: 协议在本地找到文件后，继续走下面的本地路径解析。
    """
    if not src:
        return src
    src = str(src).strip()
    if not src or src.startswith(IMG_URL_PREFIX):
        return src

    lower = src.lower()
    if lower.startswith("sandbox:"):
        src = _resolve_sandbox_path(src, base_dir)
        lower = src.lower()

    if lower.startswith(("javascript:", "vbscript:")):
        return ""
    if lower.startswith("data:"):
        return _localize_data_uri(src) or src
    if lower.startswith(("http://", "https://", "mailto:", "tel:")):
        return src

    local_path = None
    try:
        if lower.startswith("file:"):
            parts = urlsplit(src)
            local_path = Path(url2pathname(unquote(parts.path)))
        elif _WINDOWS_DRIVE_RE.match(src) or src.startswith("\\"):
            local_path = Path(src)
        else:
            parts = urlsplit(src)
            if parts.scheme:
                return src
            candidate = unquote(parts.path)
            if not candidate:
                return src
            candidate = candidate.replace("/", os.sep).replace("\\", os.sep)
            if os.path.isabs(candidate):
                local_path = Path(candidate)
            elif base_dir is not None:
                local_path = Path(base_dir) / candidate
            else:
                return src
    except (OSError, ValueError):
        return src

    try:
        local_path = local_path.expanduser()
        if not local_path.is_file() or not _looks_like_image_file(local_path):
            return src
    except OSError:
        return src

    return _copy_image_to_assets(local_path) or src


def localize_srcset(srcset, base_dir):
    if not srcset:
        return srcset
    value = str(srcset)
    if "data:" in value.lower():
        value = _SRCSET_DATA_URI_B64_RE.sub(
            lambda m: _localize_data_uri(m.group(0)) or m.group(0), value
        )
        if "data:" in value.lower():
            return srcset
    parts = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        fields = item.split()
        url = localize_image_src(fields[0], base_dir)
        descriptor = " ".join(fields[1:])
        parts.append((url + (" " + descriptor if descriptor else "")).strip())
    return ", ".join(parts) if parts else srcset


def asset_path_for_url(source):
    """把前端图片 URL 严格限制到本进程的 /__img__/ 临时资源目录（用于复制到剪贴板等场景）。"""
    if not isinstance(source, str) or not source or ASSETS_DIR is None:
        return None
    try:
        parts = urlsplit(source)
        if parts.scheme:
            if parts.scheme not in {"http", "https"} or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return None
        path = unquote(parts.path or "")
        if not path.startswith(IMG_URL_PREFIX):
            return None
        rel = path[len(IMG_URL_PREFIX):].lstrip("/\\")
        if not rel:
            return None
        root = ASSETS_DIR.resolve()
        candidate = (root / rel).resolve()
        candidate.relative_to(root)
        return candidate if candidate.is_file() else None
    except (OSError, ValueError):
        return None
