"""文档路径校验、读写、偏好设置：Api 与 app 共用的文档层逻辑。"""

import json
import os
import re
import uuid
from pathlib import Path

from . import log_exception
from .host import app_data_dir

MD_EXTS = {".md", ".markdown", ".mdown", ".mkd"}
READABLE_EXTS = MD_EXTS | {".txt"}
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
DOCUMENT_DIALOG_FILTER = "Markdown 与文本 (*.md;*.markdown;*.mdown;*.mkd;*.txt)"

PREFERENCE_KEYS = ("theme", "font")


class SaveConflict(Exception):
    """写盘时发现磁盘 mtime 与调用方预期不一致（文件已被外部修改）。"""

    def __init__(self, mtime_ns):
        super().__init__("文件已被其他程序修改")
        self.mtime_ns = mtime_ns


def document_path(path) -> Path:
    """校验并解析文档路径：必须存在、是文件、扩展名可读、体积不超限。"""
    if not path:
        raise ValueError("文件路径为空")
    p = Path(path).resolve(strict=True)
    if not p.is_file():
        raise ValueError("文件不存在")
    if p.suffix.lower() not in READABLE_EXTS:
        raise ValueError("仅支持 Markdown 或纯文本文件")
    if p.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("文件过大（上限 64 MB）")
    return p


def read_text_meta(path):
    """读取文档文本，返回 (text, encoding, mtime_ns)。

    encoding 用于保存时尽量保持原编码；无 BOM 的 UTF-8 不能标成 utf-8-sig，
    否则保存时会凭空写入 BOM。用同一 FD 的 read + fstat，避免读字节与 mtime 之间的 TOCTOU。
    """
    p = document_path(path)
    with open(p, "rb") as f:
        data = f.read()
        mtime_ns = os.fstat(f.fileno()).st_mtime_ns
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig", mtime_ns
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc), enc, mtime_ns
        except UnicodeDecodeError:
            continue
    # latin-1 把每个字节都映射到一个码位，不会解码失败，作为最终兜底。
    return data.decode("latin-1"), "latin-1", mtime_ns


def encode_document_text(text, encoding):
    """按打开时检测到的编码写回；无法编码时回退 UTF-8。返回 (bytes, used_encoding)。"""
    if not isinstance(text, str):
        raise ValueError("文档内容必须是文本")
    enc = encoding if encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1") else "utf-8"
    try:
        return text.encode(enc), enc
    except (UnicodeEncodeError, LookupError):
        return text.encode("utf-8"), "utf-8"


def coerce_mtime_ns(value):
    """把桥接层传来的 mtime 规范为 int；JSON Number 会丢 ns 精度，故优先收字符串。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        return int(value)
    return int(value)


def mtime_token(mtime_ns) -> str:
    """mtime 一律以十进制字符串过桥，避免 JS Number 丢精度。"""
    return str(int(mtime_ns))


def write_text_atomic(path, text, encoding="utf-8", expected_mtime_ns=None):
    """原子写盘，返回 (mtime_ns, used_encoding)。

    expected_mtime_ns 非 None 时，写临时文件前后各校验一次磁盘 mtime，缩小与
    外部修改的冲突窗口；不一致则抛 SaveConflict(磁盘当前 mtime)。
    """
    data, used_enc = encode_document_text(text, encoding)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("文件过大（上限 64 MB）")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def check_conflict():
        if expected_mtime_ns is None or not path.exists():
            return
        disk_mtime = path.stat().st_mtime_ns
        if disk_mtime != expected_mtime_ns:
            raise SaveConflict(disk_mtime)

    check_conflict()
    # 同目录临时文件 + replace，避免写一半被 watcher 读到
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        check_conflict()
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            log_exception()
    return path.stat().st_mtime_ns, used_enc


def md_image_markdown(alt, dest) -> str:
    """生成安全的 Markdown 图片语法；必要时用 <dest> 包裹。"""
    alt = re.sub(r"[\r\n\[\]]+", " ", alt or "").strip() or "image"
    dest = (dest or "").replace("\r", "").replace("\n", "")
    if re.search(r'[\s()<>]', dest):
        dest_esc = dest.replace("(", "%28").replace(")", "%29").replace(">", "%3E")
        return f"![{alt}](<{dest_esc}>)"
    return f"![{alt}]({dest})"


def _settings_path() -> Path:
    return app_data_dir() / "settings.json"


def validate_preference(key, value):
    """校验并规范化单个偏好设置的值；非法时抛 ValueError。"""
    if key == "theme":
        if value not in ("light", "dark"):
            raise ValueError("无效主题")
        return value
    if key == "font":
        value = float(value)
        if not 10 <= value <= 26:
            raise ValueError("无效字号")
        return value
    raise ValueError("不支持的设置项")


def load_preferences():
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    prefs = {}
    for key in PREFERENCE_KEYS:
        if key not in data:
            continue
        try:
            prefs[key] = validate_preference(key, data[key])
        except (TypeError, ValueError):
            continue
    return prefs


def save_preferences(preferences):
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
