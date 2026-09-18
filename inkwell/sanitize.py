"""HTML 安全清理：allowlist sanitizer，以及渲染后 <img>/<source> 地址本地化重写器。

Markdown 允许混入原始 HTML。sanitizer 保留常用排版标签，主动移除脚本、事件属性、
内联样式和危险 URL；否则本地文档中的脚本可以直接接触 pywebview 的 JS bridge。
"""

import html as html_module
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from . import images
from . import log_exception

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
    except ValueError:
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


def _render_start_tag(tag, attr_pairs, self_closing):
    """把 (key, value) 属性对拼成 <tag ...> 开始标签；value=None 表示无值属性。"""
    buf = [f"<{tag}"]
    for key, value in attr_pairs:
        if value is None:
            buf.append(f" {key}")
        else:
            buf.append(f' {key}="{html_module.escape(str(value), quote=True)}"')
    buf.append(" />" if self_closing else ">")
    return "".join(buf)


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
        self.out.append(_render_start_tag(tag, self._attrs(tag, attrs), self_closing))

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

    def handle_comment(self, data):
        """仅放行 Live Preview 块分隔注释；其它 HTML 注释一律丢弃。"""
        if self._drop_depth:
            return
        # 与前端 bufferWithMarkers / splitPreviewHtml 约定一致
        if re.fullmatch(r"inkwell-lp-block:\d+", (data or "").strip()):
            self.out.append(f"<!--{data.strip()}-->")


def sanitize_html(html_text):
    """清理 Markdown 产生或携带的 HTML。解析失败时安全降级为纯文本（安全兜底，fail closed，不收窄）。"""
    try:
        parser = _HTMLSanitizer()
        parser.feed(html_text or "")
        parser.close()
        return "".join(parser.out)
    except Exception:
        log_exception()
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
                    value = images.localize_image_src(value, self.base_dir)
                elif kl == "srcset":
                    value = images.localize_srcset(value, self.base_dir)
            rendered.append((key, value))
        return _render_start_tag(tag, rendered, self_closing)


def rewrite_images_in_html(html_text, base_dir=None):
    parser = _ImgSrcHTMLRewriter(base_dir)
    parser.feed(html_text)
    parser.close()
    return "".join(parser.out)
