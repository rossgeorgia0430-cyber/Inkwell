#!/usr/bin/env python3
"""
Inkwell - 页面组装
把渲染好的正文 HTML + 目录 + 文件名拼成完整的 HTML 文档。
样式/脚本/KaTeX 全部以 /assets/... 外链（由内置服务器提供），不内联，
以规避 WebView2 NavigateToString ~2MB 上限并让 KaTeX 字体相对路径可解析。
"""

import html as html_module
import json


# 无边框标题栏 + 侧栏 + 正文 + 搜索覆盖层 的整体骨架
_SHELL = """
<div class="window">
  <header class="titlebar">
    <div class="tb-cluster tb-left">
      <button class="icon-btn" id="sidebarToggle" title="目录 (Ctrl+B)" aria-label="目录">
        <svg viewBox="0 0 24 24" class="ico"><path d="M3 6h18M3 12h18M3 18h18"/></svg>
      </button>
      <span class="brand">Inkwell</span>
      <button class="icon-btn nav-btn" id="navBack" title="后退 (Alt+←)" aria-label="后退" disabled>
        <svg viewBox="0 0 24 24" class="ico"><path d="M15 18l-6-6 6-6"/></svg>
      </button>
      <button class="icon-btn nav-btn" id="navForward" title="前进 (Alt+→)" aria-label="前进" disabled>
        <svg viewBox="0 0 24 24" class="ico"><path d="M9 6l6 6-6 6"/></svg>
      </button>
    </div>
    <div class="tb-drag" id="dragRegion">
      <span class="tb-title" id="docTitle"></span>
    </div>
    <div class="tb-cluster tb-right">
      <button class="icon-btn" id="openBtn" title="打开文件 (Ctrl+O)" aria-label="打开">
        <svg viewBox="0 0 24 24" class="ico"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
      </button>
      <button class="icon-btn" id="searchBtn" title="搜索 (Ctrl+F)" aria-label="搜索">
        <svg viewBox="0 0 24 24" class="ico"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      </button>
      <button class="icon-btn" id="themeBtn" title="切换主题" aria-label="主题">
        <svg viewBox="0 0 24 24" class="ico ico-sun"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/></svg>
        <svg viewBox="0 0 24 24" class="ico ico-moon"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
      </button>
      <div class="win-controls">
        <button class="win-btn" id="winMin" title="最小化" aria-label="最小化"><svg viewBox="0 0 12 12"><path d="M2 6h8"/></svg></button>
        <button class="win-btn" id="winMax" title="最大化" aria-label="最大化"><svg viewBox="0 0 12 12"><rect x="2.5" y="2.5" width="7" height="7"/></svg></button>
        <button class="win-btn win-close" id="winClose" title="关闭" aria-label="关闭"><svg viewBox="0 0 12 12"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg></button>
      </div>
    </div>
  </header>

  <div class="app" id="app">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-head">目录</div>
      <nav class="toc" id="toc">__TOC__</nav>
    </aside>
    <main class="main" id="main">
      <div class="article-wrap" id="articleWrap">
        <article class="article" id="content">__CONTENT__</article>
      </div>
    </main>
  </div>

  <div class="scrim" id="scrim"></div>

  <div class="searchbar" id="searchbar">
    <svg viewBox="0 0 24 24" class="ico search-ico"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
    <input type="text" id="searchInput" placeholder="搜索内容…" autocomplete="off" spellcheck="false">
    <span class="search-count" id="searchCount"></span>
    <button class="icon-btn sm" id="searchPrev" title="上一个">
      <svg viewBox="0 0 24 24" class="ico"><path d="M18 15l-6-6-6 6"/></svg>
    </button>
    <button class="icon-btn sm" id="searchNext" title="下一个">
      <svg viewBox="0 0 24 24" class="ico"><path d="M6 9l6 6 6-6"/></svg>
    </button>
    <button class="icon-btn sm" id="searchClose" title="关闭 (Esc)">
      <svg viewBox="0 0 24 24" class="ico"><path d="M6 6l12 12M18 6L6 18"/></svg>
    </button>
  </div>
</div>
"""


def build_page(content_html: str, toc_html: str, title: str, path: str = None) -> str:
    """生成完整 HTML 文档字符串。"""
    safe_title = html_module.escape(title or "Inkwell")
    shell = (_SHELL
             .replace("__CONTENT__", content_html or "")
             .replace("__TOC__", toc_html or ""))
    # 初始 payload（标题 + 文档路径），供 JS 设置标题栏 & 播种跳转历史
    boot = json.dumps({"title": title or "", "path": path or ""}, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<script>window.__errors=[];window.addEventListener('error',function(e){{window.__errors.push((e.message||'')+' @'+(e.filename||'')+':'+(e.lineno||0));}});</script>
<script>
  // 在首帧前应用持久化主题，避免闪烁
  (function() {{
    try {{
      var t = localStorage.getItem('inkwell-theme') || 'light';
      document.documentElement.setAttribute('data-theme', t);
      document.write('<link rel="stylesheet" id="pygments-style" href="/assets/pygments-' + (t === 'dark' ? 'dark' : 'light') + '.css">');
    }} catch (e) {{
      document.write('<link rel="stylesheet" id="pygments-style" href="/assets/pygments-light.css">');
    }}
  }})();
</script>
<link rel="stylesheet" href="/assets/katex/katex.min.css">
<link rel="stylesheet" href="/assets/app.css">
</head>
<body>
{shell}
<script>window.__BOOT__ = {boot};</script>
<script src="/assets/katex/katex.min.js"></script>
<script src="/assets/app.js"></script>
</body>
</html>"""
