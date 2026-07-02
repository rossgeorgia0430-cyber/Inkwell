#!/usr/bin/env python3
"""
Inkwell - Markdown 渲染管线
将 Markdown 文本转换为 HTML（含目录），并处理：
- 本地图片本地化（拷贝到临时目录，经内置服务器以 /__img__/ 提供）
- LaTeX 公式保护（保留原始 TeX 于 data-latex，供 KaTeX 渲染与复制）
- 带文件路径的代码块（``` lang:path）包装语言标签 / 文件名 / 复制按钮

本模块从旧 MDReader.py 的渲染部分移植而来，去掉了 http.server / 浏览器相关代码。
"""

import os
import re
import uuid
import base64
import html as html_module
import tempfile
import shutil
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, quote, unquote
from urllib.request import url2pathname

import markdown
from pygments import highlight as _pyg_highlight
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.formatters import HtmlFormatter


# ---------------------------------------------------------------------------
# 图片资源：本地图片拷贝到一个进程级临时目录，由内置服务器在 IMG_URL_PREFIX 下提供
# ---------------------------------------------------------------------------
IMG_URL_PREFIX = "/__img__/"
ASSETS_DIR = Path(tempfile.gettempdir()) / f"inkwell_img_{os.getpid()}_{uuid.uuid4().hex}"
_ASSET_CACHE = {}

try:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    ASSETS_DIR = None

_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff", ".ico", ".avif"
}
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")

_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)', re.MULTILINE)
_HTML_IMG_RE = re.compile(r'<img\s+([^>]*?)(?:/>|>)', re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RE = re.compile(r'''src\s*=\s*(?:["']([^"']+)["']|([^\s>]+))''', re.IGNORECASE)


def cleanup_assets():
    """退出时清理临时图片目录。"""
    try:
        if ASSETS_DIR and ASSETS_DIR.exists():
            shutil.rmtree(ASSETS_DIR, ignore_errors=True)
    except Exception:
        pass


def _normalize_image_path(src, base_dir):
    """规范化图像路径，处理 sandbox:/file://、相对/绝对路径、URL 编码等。"""
    if not src:
        return src
    src = str(src).strip()
    if not src:
        return src

    lower = src.lower()
    if lower.startswith(("data:", "http://", "https://", "blob:")):
        return src
    if src.startswith(IMG_URL_PREFIX):
        return src

    # sandbox: 协议（ChatGPT 等工具产出）
    if lower.startswith("sandbox:"):
        sandbox_path = src[8:]
        if sandbox_path.startswith("//"):
            sandbox_path = sandbox_path[1:]
        filename = os.path.basename(sandbox_path)
        search_locations = []
        if base_dir:
            search_locations.append(Path(base_dir) / filename)
            search_locations.append(Path(base_dir) / "images" / filename)
            search_locations.append(Path(base_dir) / "assets" / filename)
        for loc in search_locations:
            try:
                if loc.is_file():
                    return str(loc)
            except Exception:
                continue
        return src

    return src


def _looks_like_image_file(local_path: Path) -> bool:
    try:
        return local_path.suffix.lower() in _IMAGE_EXTS
    except Exception:
        return False


def _copy_image_to_assets(local_path: Path):
    """将本地图片拷贝到临时目录，返回 /__img__/<name> URL（带缓存）。"""
    if ASSETS_DIR is None:
        return None
    try:
        resolved = local_path.resolve()
        stat = resolved.stat()
    except Exception:
        return None

    cache_key = f"{resolved}|{stat.st_size}|{int(stat.st_mtime)}"
    cached = _ASSET_CACHE.get(cache_key)
    if cached:
        return cached

    digest = hashlib.sha1(cache_key.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    suffix = resolved.suffix.lower()
    dest_name = f"{digest}{suffix}" if suffix else digest
    dest_path = ASSETS_DIR / dest_name
    try:
        if not dest_path.exists():
            shutil.copy2(resolved, dest_path)
    except Exception:
        return None

    url_path = IMG_URL_PREFIX + quote(dest_name)
    _ASSET_CACHE[cache_key] = url_path
    return url_path


# data: URI 的 mime -> 落盘扩展名。仅收录图片类型；不在表内的一律拒绝落盘，
# 交由调用方 fallback 到原始 src，再由 sanitizer 的 allowlist 兜底拦截。
_DATA_URI_MIME_EXTS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
    "image/avif": ".avif", "image/svg+xml": ".svg", "image/tiff": ".tif",
    "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
}
_DATA_URI_RE = re.compile(r"^data:([^,]*),(.*)$", re.DOTALL)


def _localize_data_uri(src):
    """把 data: URI 解码落盘为 /__img__/ URL；非图片 mime 或解码失败返回 None。

    内嵌图片（截图粘贴、AI 工具产出等）常以 data URI 形式出现在 Markdown/HTML 中，
    体积可能很大且每次都会被内联进 DOM；落盘后复用现有的 /__img__/ 服务路径，
    既能被浏览器缓存，也让 CSP／sanitizer 不必为任意 data: 内容开口子。
    """
    try:
        if ASSETS_DIR is None:
            return None
        match = _DATA_URI_RE.match(str(src).strip())
        if not match:
            return None
        header, payload = match.group(1), match.group(2)
        if not payload:
            return None

        fields = [f.strip() for f in header.split(";")]
        mime = (fields[0] or "").lower()
        is_base64 = any(f.lower() == "base64" for f in fields[1:])
        ext = _DATA_URI_MIME_EXTS.get(mime)
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
        dest_path = ASSETS_DIR / dest_name
        if not dest_path.exists():
            dest_path.write_bytes(data)
        return IMG_URL_PREFIX + dest_name
    except Exception:
        return None


def _localize_image_url(src, base_dir):
    """把指向本地图片的 src 转成 /__img__/ URL；远程原样返回，data: 尝试落盘。"""
    if not src:
        return src
    src = str(src).strip()
    if not src or src.startswith(IMG_URL_PREFIX):
        return src

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
    except Exception:
        return src

    try:
        local_path = local_path.expanduser()
        if not local_path.is_file() or not _looks_like_image_file(local_path):
            return src
    except Exception:
        return src

    return _copy_image_to_assets(local_path) or src


# base64 形式的 data URI 内部不含逗号（base64 字母表没有逗号），可以安全地在
# 逗号分隔的 srcset 里原地整体替换；percent 编码形式则可能含逗号，与 srcset
# 自身的分隔符冲突，无法安全提取，只能整体放弃交给 sanitizer 拒绝。
_SRCSET_DATA_URI_B64_RE = re.compile(
    r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE
)


def _localize_srcset(srcset, base_dir):
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
        url = _localize_image_url(fields[0], base_dir)
        descriptor = " ".join(fields[1:])
        parts.append((url + (" " + descriptor if descriptor else "")).strip())
    return ", ".join(parts) if parts else srcset


def _preprocess_markdown_images(md_text, base_dir):
    """渲染前预处理 Markdown 与内联 HTML 中的图片路径。"""
    if not md_text:
        return md_text

    def replace_md_image(match):
        alt_text = match.group(1)
        original_src = match.group(2).strip()
        title = ""
        src = original_src
        title_match = re.match(r'^(.+?)\s+["\'](.+?)["\']$', original_src)
        if title_match:
            src = title_match.group(1).strip()
            title = title_match.group(2)
        normalized = _normalize_image_path(src, base_dir)
        localized = _localize_image_url(normalized, base_dir)
        if title:
            return f'![{alt_text}]({localized} "{title}")'
        return f'![{alt_text}]({localized})'

    result = _MD_IMAGE_RE.sub(replace_md_image, md_text)

    def replace_html_img(match):
        attrs_str = match.group(1)
        src_match = _SRC_ATTR_RE.search(attrs_str)
        if not src_match:
            return match.group(0)
        original_src = src_match.group(1) or src_match.group(2)
        if not original_src:
            return match.group(0)
        normalized = _normalize_image_path(original_src, base_dir)
        localized = _localize_image_url(normalized, base_dir)
        if localized == original_src:
            return match.group(0)
        escaped_src = html_module.escape(localized, quote=True).replace('\\', '\\\\')
        new_attrs = _SRC_ATTR_RE.sub(f'src="{escaped_src}"', attrs_str, count=1)
        tag_end = "/>" if match.group(0).rstrip().endswith("/>") else ">"
        return f'<img {new_attrs}{tag_end}'

    return _HTML_IMG_RE.sub(replace_html_img, result)


from html.parser import HTMLParser


# Markdown 允许混入原始 HTML。这里保留常用排版标签，但主动移除脚本、事件属性、
# 内联样式和危险 URL；否则本地文档中的脚本可以直接接触 pywebview 的 JS bridge。
_SAFE_HTML_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col",
    "colgroup", "dd", "del", "details", "div", "dl", "dt", "em", "figcaption",
    "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins",
    "kbd", "li", "mark", "ol", "p", "picture", "pre", "q", "s", "samp",
    "small", "source", "span", "strong", "sub", "summary", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "var",
}
_DROP_WITH_CONTENT_TAGS = {
    "applet", "audio", "canvas", "form", "frameset", "iframe", "math", "object",
    "plaintext", "script", "select", "style", "svg", "template", "textarea", "video",
    "xmp",
}
_STRIP_ONLY_TAGS = {"base", "embed", "frame", "input", "link", "meta"}
_VOID_HTML_TAGS = {"br", "col", "hr", "img", "source"}
_GLOBAL_SAFE_ATTRS = {"class", "dir", "id", "lang", "role", "title"}
_TAG_SAFE_ATTRS = {
    "a": {"href", "rel", "target"},
    "blockquote": {"cite"}, "col": {"span"}, "colgroup": {"span"},
    "del": {"cite", "datetime"}, "details": {"open"},
    "div": {"data-codeblk"},
    "img": {"alt", "height", "loading", "src", "srcset", "width"},
    "ins": {"cite", "datetime"}, "li": {"value"},
    "ol": {"reversed", "start", "type"}, "q": {"cite"},
    "source": {"height", "media", "sizes", "src", "srcset", "type", "width"},
    "td": {"align", "colspan", "headers", "rowspan"},
    "th": {"align", "colspan", "headers", "rowspan", "scope"},
}
_SAFE_LINK_SCHEMES = {"http", "https", "mailto", "tel"}
_SAFE_IMAGE_SCHEMES = {"http", "https", "blob"}
# 含 svg+xml：<img> 上下文加载的 SVG 不会执行其内嵌 <script>（浏览器按图片
# 而非文档处理），所以放行不会打开 XSS 面；<svg> 标签本身在别处仍被丢弃。
_SAFE_DATA_IMAGE_RE = re.compile(
    r"^data:image/(?:avif|bmp|gif|jpeg|jpg|png|svg\+xml|webp);(?:base64,|charset=[^,]+,)",
    re.IGNORECASE,
)


def _safe_url(value, *, image=False):
    """返回可放入 href/src 的 URL；危险或混淆 scheme 返回 None。"""
    value = str(value or "").strip()
    if not value:
        return value
    compact = re.sub(r"[\x00-\x20\x7f]+", "", value)
    if image and compact.lower().startswith("data:"):
        return value if _SAFE_DATA_IMAGE_RE.match(compact) else None
    try:
        scheme = urlsplit(compact).scheme.lower()
    except Exception:
        return None
    if not scheme:
        return value
    allowed = _SAFE_IMAGE_SCHEMES if image else _SAFE_LINK_SCHEMES
    return value if scheme in allowed else None


def _safe_srcset(value):
    """校验 srcset 中的每个 URL；data URL 含逗号，保守地不接受。"""
    value = str(value or "").strip()
    if not value or "data:" in value.lower():
        return None
    for candidate in value.split(","):
        url = candidate.strip().split(None, 1)[0] if candidate.strip() else ""
        if not url or _safe_url(url, image=True) is None:
            return None
    return value


class _HTMLSanitizer(HTMLParser):
    """面向 Markdown 输出的轻量 allowlist sanitizer。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self._drop_depth = 0

    def _attrs(self, tag, attrs):
        allowed = _GLOBAL_SAFE_ATTRS | _TAG_SAFE_ATTRS.get(tag, set())
        rendered = []
        for key, value in attrs:
            key = key.lower()
            if key.startswith("on") or key == "style":
                continue
            if key not in allowed and not key.startswith("aria-"):
                continue
            if key == "data-codeblk" and not re.fullmatch(r"[0-9a-f]{32}", value or ""):
                continue
            if value is not None and key in {"href", "cite"}:
                value = _safe_url(value)
                if value is None:
                    continue
            elif value is not None and key == "src":
                value = _safe_url(value, image=True)
                if value is None:
                    continue
            elif value is not None and key == "srcset":
                value = _safe_srcset(value)
                if value is None:
                    continue
            elif key == "target" and value not in {"_blank", "_self"}:
                continue
            elif key == "rel" and value:
                tokens = {v.lower() for v in value.split()}
                value = " ".join(sorted(tokens & {"noopener", "noreferrer", "nofollow"}))
                if not value:
                    continue
            rendered.append((key, value))
        if tag == "a" and any(k == "target" and v == "_blank" for k, v in rendered):
            rel_index = next((i for i, item in enumerate(rendered) if item[0] == "rel"), None)
            if rel_index is None:
                rendered.append(("rel", "noopener noreferrer"))
            else:
                rel = set(rendered[rel_index][1].split()) | {"noopener", "noreferrer"}
                rendered[rel_index] = ("rel", " ".join(sorted(rel)))
        return rendered

    def _start(self, tag, attrs, self_closing=False):
        tag = tag.lower()
        if self._drop_depth:
            if tag in _DROP_WITH_CONTENT_TAGS and not self_closing:
                self._drop_depth += 1
            return
        if tag in _DROP_WITH_CONTENT_TAGS:
            if not self_closing:
                self._drop_depth = 1
            return
        if tag in _STRIP_ONLY_TAGS:
            return
        if tag not in _SAFE_HTML_TAGS:
            return
        buf = [f"<{tag}"]
        for key, value in self._attrs(tag, attrs):
            if value is None:
                buf.append(f" {key}")
            else:
                buf.append(f' {key}="{html_module.escape(str(value), quote=True)}"')
        buf.append(" />" if self_closing else ">")
        self.out.append("".join(buf))

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._drop_depth:
            if tag in _DROP_WITH_CONTENT_TAGS:
                self._drop_depth -= 1
            return
        if tag in _SAFE_HTML_TAGS and tag not in _VOID_HTML_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._drop_depth:
            # HTMLParser 对 plaintext/xmp 及畸形 HTML 可能把形似标签的内容作为
            # data 交回；始终转义，避免清理后重新组成可执行标签。
            self.out.append(html_module.escape(data, quote=False))

    def handle_entityref(self, name):
        if not self._drop_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self._drop_depth:
            self.out.append(f"&#{name};")


def sanitize_html(html_text):
    """清理 Markdown 产生或携带的 HTML。解析失败时安全降级为纯文本。"""
    try:
        parser = _HTMLSanitizer()
        parser.feed(html_text or "")
        parser.close()
        return "".join(parser.out)
    except Exception:
        return html_module.escape(html_text or "")


class _ImgSrcHTMLRewriter(HTMLParser):
    """重写已渲染 HTML 里 <img>/<source> 的 src/srcset 为本地化 URL。"""

    def __init__(self, base_dir):
        super().__init__(convert_charrefs=False)
        self.base_dir = base_dir
        self.out = []

    def handle_starttag(self, tag, attrs):
        self.out.append(self._render_tag(tag, attrs, False))

    def handle_startendtag(self, tag, attrs):
        self.out.append(self._render_tag(tag, attrs, True))

    def handle_endtag(self, tag):
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(data)

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def unknown_decl(self, data):
        self.out.append(f"<![{data}]>")

    def _render_tag(self, tag, attrs, self_closing):
        should_rewrite = tag.lower() in {"img", "source"}
        rendered = []
        for key, value in attrs:
            if value is not None and should_rewrite:
                kl = key.lower()
                if kl == "src":
                    value = _localize_image_url(value, self.base_dir)
                elif kl == "srcset":
                    value = _localize_srcset(value, self.base_dir)
            rendered.append((key, value))
        buf = [f"<{tag}"]
        for key, value in rendered:
            if value is None:
                buf.append(f" {key}")
            else:
                buf.append(f' {key}="{html_module.escape(str(value), quote=True)}"')
        buf.append(" />" if self_closing else ">")
        return "".join(buf)


def rewrite_images_in_html(html_text, base_dir=None):
    try:
        parser = _ImgSrcHTMLRewriter(base_dir)
        parser.feed(html_text)
        parser.close()
        return "".join(parser.out)
    except Exception:
        return html_text


# ---------------------------------------------------------------------------
# LaTeX 公式保护：块级带复制按钮 + data-latex；行内仅 data-latex（避免行内按钮干扰阅读）
# ---------------------------------------------------------------------------
def protect_latex(md_text):
    placeholders = {}

    def replace_block(match):
        key = f"%%LATEXBLOCK{uuid.uuid4().hex}%%"
        latex_code = match.group(1).strip()
        escaped = html_module.escape(latex_code, quote=True)
        placeholders[key] = (
            f'<div class="math-block" data-latex="{escaped}">'
            f'<button class="math-copy-btn" data-copy-action="latex" title="复制 LaTeX">复制公式</button>'
            f'$${escaped}$$</div>'
        )
        return key

    md_text = re.sub(r'\$\$(.+?)\$\$', replace_block, md_text, flags=re.DOTALL)

    def replace_inline(match):
        key = f"%%LATEXINLINE{uuid.uuid4().hex}%%"
        latex_code = match.group(1).strip()
        escaped = html_module.escape(latex_code, quote=True)
        placeholders[key] = (
            f'<span class="math-inline" data-latex="{escaped}">${escaped}$</span>'
        )
        return key

    # 行内 $...$：采用 markdown-it / pandoc 风格的边界规则，避免把货币（$5 和 $10）误判为公式：
    #  - 开 $ 后不能紧跟空白；闭 $ 前不能是空白；闭 $ 后不能紧跟数字；内容不含 $ 或换行
    md_text = re.sub(r'(?<!\$)(?<!\\)\$(?!\s)(?!\$)([^\n$]+?)(?<!\s)\$(?!\$)(?!\d)',
                     replace_inline, md_text)

    def replace_bracket_block(match):
        key = f"%%LATEXBRACKET{uuid.uuid4().hex}%%"
        latex_code = match.group(1).strip()
        escaped = html_module.escape(latex_code, quote=True)
        placeholders[key] = (
            f'<div class="math-block" data-latex="{escaped}">'
            f'<button class="math-copy-btn" data-copy-action="latex" title="复制 LaTeX">复制公式</button>'
            f'\\[{escaped}\\]</div>'
        )
        return key

    def replace_bracket_inline(match):
        key = f"%%LATEXPAREN{uuid.uuid4().hex}%%"
        latex_code = match.group(1).strip()
        escaped = html_module.escape(latex_code, quote=True)
        placeholders[key] = (
            f'<span class="math-inline" data-latex="{escaped}">\\({escaped}\\)</span>'
        )
        return key

    md_text = re.sub(r'\\\[(.+?)\\\]', replace_bracket_block, md_text, flags=re.DOTALL)
    md_text = re.sub(r'\\\((.+?)\\\)', replace_bracket_inline, md_text)
    return md_text, placeholders


def restore_latex(html_text, placeholders):
    for key, value in placeholders.items():
        html_text = html_text.replace(key, value)
    return html_text


# ---------------------------------------------------------------------------
# 代码块（Claude 风格）：每个围栏代码块都直接用 Pygments 渲染，并加统一头部
# （语言徽标 + 可选文件名 + 复制按钮）。以占位 <div data-codeblk> 替换，渲染后还原。
# 支持 ```lang、```lang:path、```lang path；缩进代码块仍走 markdown（无头部）。
# ---------------------------------------------------------------------------
_CODE_FORMATTER = HtmlFormatter(cssclass='codehilite', nowrap=False)

# 语言名 -> 头部显示名（更友好/更像 Claude）
_LANG_LABELS = {
    'py': 'Python', 'python': 'Python', 'js': 'JavaScript', 'javascript': 'JavaScript',
    'ts': 'TypeScript', 'typescript': 'TypeScript', 'jsx': 'JSX', 'tsx': 'TSX',
    'sh': 'Bash', 'bash': 'Bash', 'shell': 'Shell', 'zsh': 'Zsh', 'ps1': 'PowerShell',
    'powershell': 'PowerShell', 'bat': 'Batch', 'c': 'C', 'cpp': 'C++', 'cs': 'C#',
    'java': 'Java', 'go': 'Go', 'rs': 'Rust', 'rust': 'Rust', 'rb': 'Ruby', 'php': 'PHP',
    'swift': 'Swift', 'kt': 'Kotlin', 'sql': 'SQL', 'json': 'JSON', 'yaml': 'YAML',
    'yml': 'YAML', 'toml': 'TOML', 'xml': 'XML', 'html': 'HTML', 'css': 'CSS',
    'scss': 'SCSS', 'md': 'Markdown', 'markdown': 'Markdown', 'diff': 'Diff',
    'dockerfile': 'Dockerfile', 'make': 'Makefile', 'ini': 'INI', 'text': 'Text', '': '',
}


def _label_for(lang):
    return _LANG_LABELS.get(lang.lower(), lang.upper() if len(lang) <= 4 else lang.capitalize())


def _parse_info(info):
    """解析围栏信息串：返回 (lang, filepath|None)。支持 lang / lang:path / lang path。"""
    info = info.strip()
    if not info:
        return '', None
    first = info.split()[0]
    if ':' in first:
        lang, path = first.split(':', 1)
        return lang, (path.strip() or None)
    parts = info.split(None, 1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else None)


def _build_code_html(lang, filepath, code):
    code = code.rstrip('\n')
    try:
        lexer = get_lexer_by_name(lang, stripnl=False) if lang else TextLexer()
    except Exception:
        try:
            lexer = guess_lexer(code)
        except Exception:
            lexer = TextLexer()
    body = _pyg_highlight(code, lexer, _CODE_FORMATTER)
    label = html_module.escape(_label_for(lang))
    esc_lang = html_module.escape(lang)
    esc_path = html_module.escape(filepath) if filepath else ''

    badge = f'<span class="code-lang">{label}</span>' if label else '<span class="code-lang code-lang-plain">代码</span>'
    pathspan = f'<span class="code-filepath">{esc_path}</span>' if esc_path else '<span class="code-filepath"></span>'
    header = (
        f'<div class="code-block-wrapper" data-lang="{esc_lang}"'
        + (f' data-filepath="{esc_path}"' if esc_path else '') + '>'
        f'<div class="code-block-header">'
        f'{badge}{pathspan}'
        f'<button class="code-copy-btn" data-copy-action="code" title="复制代码">'
        f'<svg viewBox="0 0 24 24" class="copy-ico"><rect x="9" y="9" width="11" height="11" rx="2"/>'
        f'<path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg><span class="copy-label">复制</span></button>'
        f'</div>'
    )
    return header + body + '</div>'


def protect_code_blocks(md_text):
    """逐行扫描，把所有围栏代码块替换为占位 div，渲染好的 HTML 存入 placeholders。"""
    lines = md_text.split('\n')
    out, placeholders = [], {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        opener = re.match(r'^( {0,3})(`{3,}|~{3,})([^\n]*)$', line)
        if opener and not (opener.group(2)[0] == '`' and '`' in opener.group(3)):
            indent, fence, info = opener.groups()
            fence_char = fence[0]
            fence_len = len(fence)
            closer_re = re.compile(rf'^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$')
            j, body = i + 1, []
            while j < n and not closer_re.match(lines[j]):
                body_line = lines[j]
                # CommonMark：内容行最多移除 opening fence 的缩进量。
                remove = min(len(indent), len(body_line) - len(body_line.lstrip(' ')))
                body.append(body_line[remove:])
                j += 1
            if j < n:  # 找到了闭合围栏
                lang, filepath = _parse_info(info)
                key = uuid.uuid4().hex
                placeholders[key] = _build_code_html(lang, filepath, '\n'.join(body))
                out.append(f'{indent}<div data-codeblk="{key}"></div>')
                i = j + 1
                continue
            # 未闭合：当普通文本处理
        out.append(line)
        i += 1
    return '\n'.join(out), placeholders


def restore_code_blocks(html_text, placeholders):
    for key, value in placeholders.items():
        html_text = html_text.replace(f'<div data-codeblk="{key}"></div>', value)
    return html_text


# ---------------------------------------------------------------------------
# 行内代码保护：`...` 里的内容语义上是字面文本，不该被后续步骤当成 Markdown
# 语法二次解析——否则 `$x$`会被 protect_latex 误判成公式，`![a](p.png)`会被
# 图片本地化步骤改写路径。这里只是"占位挡一挡"，真正生成 <code> 仍交回给
# python-markdown（所以还原要发生在 md.convert 之前，而不是像代码块那样在之后）。
# ---------------------------------------------------------------------------
_INLINE_CODE_RE = re.compile(r'(?<!`)(`+)(?!`)([^`]+?)\1(?!`)')


def protect_inline_code(md_text):
    placeholders = {}

    def replace(match):
        content = match.group(2)
        # 跨越空行的反引号大概率不是同一段行内代码（更像误配对的围栏残留），
        # 保守起见维持原文，交给后续流程按原来的（可能不完美的）方式处理。
        if "\n\n" in content:
            return match.group(0)
        key = f"%%INLINECODE{uuid.uuid4().hex}%%"
        placeholders[key] = match.group(0)
        return key

    md_text = _INLINE_CODE_RE.sub(replace, md_text)
    return md_text, placeholders


def restore_inline_code(text, placeholders):
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


def render_markdown(md_text, base_dir=None):
    """Markdown -> (html, toc_html)。"""
    md_text, code_blocks = protect_code_blocks(md_text)
    md_text, inline_code = protect_inline_code(md_text)
    md_text = _preprocess_markdown_images(md_text, base_dir)
    protected_text, placeholders = protect_latex(md_text)
    protected_text = restore_inline_code(protected_text, inline_code)
    # 公式内容里的反引号（如 $$a `b` c$$）也会被换成 INLINECODE 占位符，而
    # protect_latex 把公式整段捕获进了自己的 placeholders 值里，主文本的还原
    # 触及不到。这些值已经过 html.escape(quote=True)，若用未转义原文替换，
    # 原文中的 < > & " 会在属性/文本上下文里重新打开注入面，所以必须用
    # 同样转义过的原文替换。占位符本身只含 % 和字母数字，转义不改变其形态。
    if inline_code:
        escaped_inline = {k: html_module.escape(v, quote=True) for k, v in inline_code.items()}
        placeholders = {k: restore_inline_code(v, escaped_inline) for k, v in placeholders.items()}

    md = markdown.Markdown(
        extensions=['fenced_code', 'codehilite', 'tables', 'toc',
                    'sane_lists', 'md_in_html', 'attr_list'],
        extension_configs={
            'codehilite': {'linenums': False, 'css_class': 'codehilite', 'guess_lang': False},
            'toc': {'permalink': False, 'toc_depth': 4},
        },
    )
    # 先本地化原始 HTML 中的 img/source，再做安全清理；这样 file:/Windows 路径
    # 不需要作为可执行 URL scheme 放进 sanitizer 的 allowlist。
    html = rewrite_images_in_html(md.convert(protected_text), base_dir)
    html = sanitize_html(html)
    toc_html = sanitize_html(md.toc)
    html = restore_latex(html, placeholders)
    html = restore_code_blocks(html, code_blocks)
    return html, toc_html
