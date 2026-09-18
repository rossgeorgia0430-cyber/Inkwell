#!/usr/bin/env python3
"""
Inkwell - Markdown 渲染管线
将 Markdown 文本转换为 HTML（含目录），并处理：
- 本地图片本地化（拷贝到临时目录，经内置服务器以 /__img__/ 提供）
- LaTeX 公式保护（保留原始 TeX 于 data-latex，供 KaTeX 渲染与复制）
- 带文件路径的代码块（``` lang:path）包装语言标签 / 文件名 / 复制按钮
- Mermaid 围栏块保留源码，并交由前端离线绘制为可切换图示
"""

import re
import uuid
import base64
import html as html_module

import markdown
from pygments import highlight as _pyg_highlight
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound

from . import images
from . import sanitize

_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)', re.MULTILINE)
_HTML_IMG_RE = re.compile(r'<img\s+([^>]*?)(?:/>|>)', re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RE = re.compile(r'''src\s*=\s*(?:["']([^"']+)["']|([^\s>]+))''', re.IGNORECASE)


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
        localized = images.localize_image_src(src, base_dir)
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
        localized = images.localize_image_src(original_src, base_dir)
        if localized == original_src:
            return match.group(0)
        escaped_src = html_module.escape(localized, quote=True).replace('\\', '\\\\')
        new_attrs = _SRC_ATTR_RE.sub(f'src="{escaped_src}"', attrs_str, count=1)
        tag_end = "/>" if match.group(0).rstrip().endswith("/>") else ">"
        return f'<img {new_attrs}{tag_end}'

    return _HTML_IMG_RE.sub(replace_html_img, result)


# ---------------------------------------------------------------------------
# LaTeX 公式保护：块级带复制按钮 + data-latex；行内仅 data-latex（避免行内按钮干扰阅读）
# ---------------------------------------------------------------------------
def _math_block_html(escaped, open_delim, close_delim):
    return (
        f'<div class="math-block" data-latex="{escaped}">'
        f'<button class="math-copy-btn" data-copy-action="latex" title="复制 LaTeX">复制公式</button>'
        f'{open_delim}{escaped}{close_delim}</div>'
    )


def _math_inline_html(escaped, open_delim, close_delim):
    return f'<span class="math-inline" data-latex="{escaped}">{open_delim}{escaped}{close_delim}</span>'


def _make_latex_replacer(placeholders, key_prefix, build_html, open_delim, close_delim):
    """生成正则替换闭包：把匹配的 LaTeX 源码存入占位表，正文中留下占位符。"""
    def replace(match):
        key = f"%%{key_prefix}{uuid.uuid4().hex}%%"
        latex_code = match.group(1).strip()
        escaped = html_module.escape(latex_code, quote=True)
        placeholders[key] = build_html(escaped, open_delim, close_delim)
        return key
    return replace


def protect_latex(md_text):
    placeholders = {}

    replace_block = _make_latex_replacer(placeholders, "LATEXBLOCK", _math_block_html, "$$", "$$")
    md_text = re.sub(r'\$\$(.+?)\$\$', replace_block, md_text, flags=re.DOTALL)

    # 行内 $...$：采用 markdown-it / pandoc 风格的边界规则，避免把货币（$5 和 $10）误判为公式：
    #  - 开 $ 后不能紧跟空白；闭 $ 前不能是空白；闭 $ 后不能紧跟数字；内容不含 $ 或换行
    replace_inline = _make_latex_replacer(placeholders, "LATEXINLINE", _math_inline_html, "$", "$")
    md_text = re.sub(r'(?<!\$)(?<!\\)\$(?!\s)(?!\$)([^\n$]+?)(?<!\s)\$(?!\$)(?!\d)',
                     replace_inline, md_text)

    replace_bracket_block = _make_latex_replacer(placeholders, "LATEXBRACKET", _math_block_html, "\\[", "\\]")
    md_text = re.sub(r'\\\[(.+?)\\\]', replace_bracket_block, md_text, flags=re.DOTALL)

    replace_bracket_inline = _make_latex_replacer(placeholders, "LATEXPAREN", _math_inline_html, "\\(", "\\)")
    md_text = re.sub(r'\\\((.+?)\\\)', replace_bracket_inline, md_text)

    return md_text, placeholders


def restore_placeholders(text, placeholders):
    """把占位符替换回原始/渲染好的内容；LaTeX 与行内代码保护共用这一步。"""
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


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
    except ClassNotFound:
        try:
            lexer = guess_lexer(code)
        except ClassNotFound:
            lexer = TextLexer()
    body = _pyg_highlight(code, lexer, _CODE_FORMATTER)
    label = html_module.escape(_label_for(lang))
    esc_lang = html_module.escape(lang)
    esc_path = html_module.escape(filepath) if filepath else ''
    is_mermaid = lang.strip().lower() == 'mermaid'

    badge = f'<span class="code-lang">{label}</span>' if label else '<span class="code-lang code-lang-plain">代码</span>'
    pathspan = f'<span class="code-filepath">{esc_path}</span>' if esc_path else '<span class="code-filepath"></span>'
    mermaid_toggle = ''
    mermaid_zoom = ''
    mermaid_attrs = ''
    if is_mermaid:
        # HTML 属性会规范化换行；以 UTF-8 base64 传到前端，既保留源码，
        # 也不会让文档里的引号或标签参与页面结构解析。
        source = base64.b64encode(code.encode('utf-8')).decode('ascii')
        mermaid_attrs = f' data-mermaid-source="{source}"'
        mermaid_toggle = (
            '<button class="mermaid-toggle-btn" type="button" '
            'data-mermaid-action="toggle" aria-pressed="false" title="查看图示">'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="6" r="2"/>'
            '<circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/>'
            '<path d="M7 7.2l3.8 8M17 7.2l-3.8 8M7 6h10"/></svg>'
            '<span class="mermaid-toggle-label">图示</span></button>'
        )
        mermaid_zoom = (
            '<button class="mermaid-toggle-btn mermaid-zoom-btn" type="button" '
            'data-mermaid-action="zoom" title="放大查看图示">'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/>'
            '<path d="M15.5 15.5L21 21M10.5 7.5v6M7.5 10.5h6"/></svg>'
            '<span class="mermaid-toggle-label">放大</span></button>'
        )
    header = (
        f'<div class="code-block-wrapper' + (' mermaid-block' if is_mermaid else '')
        + f'" data-lang="{esc_lang}"{mermaid_attrs}'
        + (f' data-filepath="{esc_path}"' if esc_path else '') + '>'
        f'<div class="code-block-header">'
        f'{badge}{pathspan}'
        f'{mermaid_toggle}{mermaid_zoom}'
        f'<button class="code-copy-btn" data-copy-action="code" title="复制代码">'
        f'<svg viewBox="0 0 24 24" class="copy-ico"><rect x="9" y="9" width="11" height="11" rx="2"/>'
        f'<path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg><span class="copy-label">复制</span></button>'
        f'</div>'
    )
    diagram = '<div class="mermaid-diagram" role="img" aria-label="Mermaid 图示"></div>' if is_mermaid else ''
    return header + body + diagram + '</div>'


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


def render_markdown(md_text, base_dir=None):
    """Markdown -> (html, toc_html)。"""
    md_text, code_blocks = protect_code_blocks(md_text)
    md_text, inline_code = protect_inline_code(md_text)
    md_text = _preprocess_markdown_images(md_text, base_dir)
    protected_text, placeholders = protect_latex(md_text)
    protected_text = restore_placeholders(protected_text, inline_code)
    # 公式内容里的反引号（如 $$a `b` c$$）也会被换成 INLINECODE 占位符，而
    # protect_latex 把公式整段捕获进了自己的 placeholders 值里，主文本的还原
    # 触及不到。这些值已经过 html.escape(quote=True)，若用未转义原文替换，
    # 原文中的 < > & " 会在属性/文本上下文里重新打开注入面，所以必须用
    # 同样转义过的原文替换。占位符本身只含 % 和字母数字，转义不改变其形态。
    if inline_code:
        escaped_inline = {k: html_module.escape(v, quote=True) for k, v in inline_code.items()}
        placeholders = {k: restore_placeholders(v, escaped_inline) for k, v in placeholders.items()}

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
    html = sanitize.rewrite_images_in_html(md.convert(protected_text), base_dir)
    html = sanitize.sanitize_html(html)
    toc_html = sanitize.sanitize_html(md.toc)
    html = restore_placeholders(html, placeholders)
    html = restore_code_blocks(html, code_blocks)
    return html, toc_html
