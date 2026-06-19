/* ============================================================
   Inkwell — 前端逻辑
   公式渲染 / 飞书无底色复制 / 代码双击高亮 / 搜索 / 目录 / 主题 / 无边框窗口
   ============================================================ */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var api = function () { return (window.pywebview && window.pywebview.api) || null; };

  var content, main, app, sidebar, toc, dragRegion, docTitle;
  var currentPath = null;
  var headings = [];

  // ---------- 工具 ----------
  function escapeReg(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function flash(btn, label) {
    if (!btn) return;
    var target = btn.querySelector(".copy-label") || btn;
    var old = target.getAttribute("data-label");
    if (old === null) { old = target.textContent; target.setAttribute("data-label", old); }
    target.textContent = label;
    btn.classList.add("copied");
    setTimeout(function () {
      target.textContent = target.getAttribute("data-label") || old;
      btn.classList.remove("copied");
    }, 1200);
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  function writeClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(function () { fallbackCopy(text); });
        return;
      }
    } catch (e) {}
    fallbackCopy(text);
  }

  // ============================================================
  // 1) 公式渲染：直接用 data-latex 调 katex.render（不扫描正文）
  // ============================================================
  function renderMath(root) {
    if (!window.katex) return;
    root.querySelectorAll(".math-block[data-latex]").forEach(function (el) {
      var tex = el.getAttribute("data-latex");
      var btn = el.querySelector(".math-copy-btn");
      try {
        window.katex.render(tex, el, {
          displayMode: true, throwOnError: false, strict: false, output: "htmlAndMathml"
        });
      } catch (e) { el.textContent = "$$" + tex + "$$"; }
      if (btn) el.appendChild(btn);            // katex.render 会清空子节点，重新挂回复制按钮
    });
    root.querySelectorAll(".math-inline[data-latex]").forEach(function (el) {
      var tex = el.getAttribute("data-latex");
      try {
        window.katex.render(tex, el, {
          displayMode: false, throwOnError: false, strict: false, output: "htmlAndMathml"
        });
      } catch (e) { el.textContent = "$" + tex + "$"; }
    });
  }

  // 复制单条公式的 LaTeX（行内 onclick 调用，需挂到 window）
  function copyLatex(btn) {
    var host = btn.closest("[data-latex]");
    if (!host) return;
    var tex = host.getAttribute("data-latex") || "";
    writeClipboard(tex);
    flash(btn, "已复制");
  }
  window.copyLatex = copyLatex;

  // ============================================================
  // 2) 飞书无底色复制：拦截 copy，净化选区后写 text/html + text/plain
  // ============================================================
  var STRIP_PROPS = [
    "color", "background", "background-color", "background-image",
    "box-shadow", "text-shadow", "filter", "-webkit-text-fill-color",
    "border", "border-color", "border-top-color", "border-bottom-color",
    "border-left-color", "border-right-color", "outline", "outline-color"
  ];

  function unwrap(el) {
    var p = el.parentNode;
    if (!p) return;
    while (el.firstChild) p.insertBefore(el.firstChild, el);
    p.removeChild(el);
  }

  function texFromKatex(k) {
    var ann = k.querySelector('annotation[encoding="application/x-tex"]');
    if (ann && ann.textContent) return ann.textContent;
    var wrap = k.closest("[data-latex]");
    if (wrap) return wrap.getAttribute("data-latex");
    return null;
  }

  // —— 表格：选区落在表格内时，cloneContents 往往只得到残缺的 tr/td 片段，
  //    粘贴到飞书/Word 会变“空的”。这里改为复制整张 <table>，并生成 TSV 纯文本。
  function nearestTable(node) {
    var el = node && (node.nodeType === 1 ? node : node.parentNode);
    return (el && el.closest) ? el.closest("table") : null;
  }

  function repairTableSelection(holder, range) {
    // 已抓到完整 <table> 就不动
    if (holder.querySelector("table")) return;
    // 仅当克隆片段里有“游离的”表格结构（多单元格/多行选择丢了 <table> 外壳）才修复；
    // 单纯选中单元格内的一段文字（无 tr/td 片段）不应被放大成整张表。
    if (!holder.querySelector("tr, td, th, tbody, thead, tfoot")) return;
    var t = nearestTable(range.commonAncestorContainer);
    if (t) { holder.innerHTML = ""; holder.appendChild(t.cloneNode(true)); }
  }

  function tableToTSV(t) {
    var rows = [];
    t.querySelectorAll("tr").forEach(function (tr) {
      var cells = [];
      tr.querySelectorAll("th,td").forEach(function (c) {
        cells.push((c.textContent || "").replace(/\s+/g, " ").trim());
      });
      rows.push(cells.join("\t"));
    });
    return rows.join("\n");
  }

  // 生成纯文本：表格转 TSV（制表符分列、换行分行），其余取 textContent
  function holderToPlain(holder) {
    holder.querySelectorAll("table").forEach(function (t) {
      t.replaceWith(document.createTextNode("\n" + tableToTSV(t) + "\n"));
    });
    return holder.textContent;
  }

  function sanitizeForCopy(holder) {
    // step 0: 把渲染后的公式替换回 $tex$ / $$tex$$ 纯文本
    holder.querySelectorAll(".math-block, .katex-display").forEach(function (k) {
      var host = k.classList.contains("math-block") ? k : (k.closest(".math-block") || k);
      var tex = texFromKatex(k);
      host.replaceWith(document.createTextNode(tex != null ? ("$$" + tex + "$$") : (k.textContent || "")));
    });
    holder.querySelectorAll(".math-inline, .katex").forEach(function (k) {
      if (!k.isConnected) return;
      var host = k.classList.contains("math-inline") ? k : (k.closest(".math-inline") || k);
      var tex = texFromKatex(k);
      host.replaceWith(document.createTextNode(tex != null ? ("$" + tex + "$") : (k.textContent || "")));
    });
    // step 1: 删除纯 UI 元素
    holder.querySelectorAll(".code-copy-btn, .code-copy-float, .math-copy-btn, .code-block-header")
      .forEach(function (b) { b.remove(); });
    // step 2: 拆掉所有 span（彻底去除 Pygments 着色与高亮）
    holder.querySelectorAll("span").forEach(unwrap);
    // step 3: 去掉 class/id/data-*/内联颜色样式；规范化 pre/code/table 外观
    holder.querySelectorAll("*").forEach(function (el) {
      el.removeAttribute("class");
      el.removeAttribute("id");
      el.removeAttribute("onclick");
      el.removeAttribute("data-latex");
      el.removeAttribute("data-filepath");
      el.removeAttribute("data-lang");
      if (el.hasAttribute("style")) {
        STRIP_PROPS.forEach(function (p) { el.style.removeProperty(p); });
        if (!el.getAttribute("style")) el.removeAttribute("style");
      }
      var t = el.tagName;
      if (t === "PRE" || t === "CODE") {
        el.style.color = "#000";
        el.style.background = "transparent";
        el.style.fontFamily = "Consolas, monospace";
        if (t === "PRE") el.style.whiteSpace = "pre-wrap";
      }
      // 表格：保留结构，去底色，仅留发丝边框，文字纯黑
      if (t === "TABLE") {
        el.style.borderCollapse = "collapse";
        el.removeAttribute("width");
      }
      if (t === "TD" || t === "TH") {
        el.style.border = "1px solid #c8c8c8";
        el.style.padding = "5px 9px";
        el.style.color = "#000";
        el.style.textAlign = "left";
      }
    });
    // step 4: 拆掉残留的无 class 包装 div（codehilite / code-block-wrapper），但保留表格元素
    holder.querySelectorAll("div").forEach(unwrap);
  }

  // 核心：把当前选区净化为 {html, plain}（供 onCopy 与自动化测试复用）
  function buildCopyPayload(sel) {
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    var range = sel.getRangeAt(0);
    var holder = document.createElement("div");
    for (var i = 0; i < sel.rangeCount; i++) {
      holder.appendChild(sel.getRangeAt(i).cloneContents());
    }
    repairTableSelection(holder, range);
    sanitizeForCopy(holder);
    var html = holder.innerHTML;
    var plain = holderToPlain(holder);   // 注意：会改动 holder，必须在取 html 之后
    return { html: html, plain: plain };
  }

  function onCopy(e) {
    try {
      var p = buildCopyPayload(window.getSelection());
      if (!p) return;
      if (e.clipboardData) {
        e.clipboardData.setData("text/html", p.html);
        e.clipboardData.setData("text/plain", p.plain);
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    } catch (err) { /* 出错则放行默认复制 */ }
  }

  // ============================================================
  // 3) 代码块双击高亮（VSCode 风格）
  // ============================================================
  function getWordAtPoint(x, y) {
    var pos = document.caretRangeFromPoint ? document.caretRangeFromPoint(x, y) : null;
    if (!pos) return "";
    var node = pos.startContainer;
    if (node.nodeType !== 3) {
      var tt = (node.textContent || "").trim();
      return /^[A-Za-z_]\w*$/.test(tt) ? tt : "";
    }
    var text = node.textContent;
    var s = pos.startOffset, en = s;
    while (s > 0 && /\w/.test(text[s - 1])) s--;
    while (en < text.length && /\w/.test(text[en])) en++;
    return text.slice(s, en);
  }

  function clearVarHighlights() {
    if (!content) return;
    content.querySelectorAll(".var-highlight").forEach(function (sp) {
      var p = sp.parentNode;
      if (!p) return;
      p.replaceChild(document.createTextNode(sp.textContent), sp);
      p.normalize();
    });
  }

  function highlightToken(block, word) {
    block.normalize();
    var re;
    try { re = new RegExp("(?<!\\w)" + escapeReg(word) + "(?!\\w)", "g"); }
    catch (e) { re = new RegExp(escapeReg(word), "g"); }
    var walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, null);
    var nodes = [], n;
    while ((n = walker.nextNode())) nodes.push(n);
    var first = true;
    nodes.forEach(function (tn) {
      var text = tn.nodeValue;
      if (text.indexOf(word) < 0) return;
      re.lastIndex = 0;
      var frag = document.createDocumentFragment();
      var last = 0, m, matched = false;
      while ((m = re.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var span = document.createElement("span");
        span.className = "var-highlight" + (first ? " current" : "");
        span.textContent = m[0];
        frag.appendChild(span);
        matched = true; first = false;
        last = m.index + m[0].length;
        if (m[0].length === 0) re.lastIndex++;
      }
      if (!matched) return;
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (tn.parentNode) tn.parentNode.replaceChild(frag, tn);
    });
    var cur = block.querySelector(".var-highlight.current");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  function onDblClick(e) {
    var block = e.target.closest && e.target.closest(".codehilite, pre");
    if (!block || !content.contains(block)) return;
    var word = getWordAtPoint(e.clientX, e.clientY);
    clearVarHighlights();
    if (!word || !/^[A-Za-z_]\w*$/.test(word)) return;
    highlightToken(block, word);
    e.stopPropagation();
  }

  // ============================================================
  // 代码复制按钮
  // ============================================================
  function copyCode(btn) {
    var box = btn.closest(".code-block-wrapper") || btn.closest(".codehilite") || btn.closest("pre");
    if (!box) return;
    var pre = box.querySelector("pre") || box;
    var clone = pre.cloneNode(true);
    clone.querySelectorAll(".code-copy-float, .code-copy-btn").forEach(function (b) { b.remove(); });
    var text = (clone.textContent || "").replace(/\n+$/, "");
    writeClipboard(text);
    flash(btn, "已复制");
  }
  window.copyCode = copyCode;

  function addCodeCopyButtons(root) {
    root.querySelectorAll(".codehilite").forEach(function (box) {
      if (box.closest(".code-block-wrapper")) return;        // 已有头部按钮
      if (box.querySelector(".code-copy-float")) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy-float";
      btn.textContent = "复制";
      btn.addEventListener("click", function () { copyCode(btn); });
      box.appendChild(btn);
    });
  }

  // ============================================================
  // 4) 目录：滚动高亮 + 点击跳转
  // ============================================================
  function collectHeadings() {
    headings = Array.prototype.slice.call(
      content.querySelectorAll("h1[id],h2[id],h3[id],h4[id]")
    );
  }

  // 仅当激活项滚出侧栏可视区时才把它滚进来——避免每次 spy 都 scrollIntoView 造成抖动
  function ensureTocVisible(link) {
    if (!sidebar) return;
    var lr = link.getBoundingClientRect(), sr = sidebar.getBoundingClientRect();
    if (lr.top < sr.top + 6 || lr.bottom > sr.bottom - 6) {
      link.scrollIntoView({ block: "nearest" });
    }
  }

  var activeLink = null;
  function setActiveToc(id) {
    if (!toc) return;
    var link = id ? toc.querySelector('a[href="#' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]') : null;
    if (link === activeLink) return;             // 无变化则不动，杜绝重复 class 抖动
    if (activeLink) activeLink.classList.remove("active");
    activeLink = link;
    if (link) { link.classList.add("active"); ensureTocVisible(link); }
  }

  var spyTick = false, spyLock = false, spyLockTimer = null;
  function lockSpy() {                            // 平滑滚动期间锁住 spy，避免高亮反复跳动闪烁
    spyLock = true;
    clearTimeout(spyLockTimer);
    spyLockTimer = setTimeout(function () { spyLock = false; }, 1500);
  }
  function unlockSpy() { spyLock = false; clearTimeout(spyLockTimer); }

  function onScrollSpy() {
    if (spyLock || spyTick) return;
    spyTick = true;
    requestAnimationFrame(function () {
      spyTick = false;
      if (!headings.length) return;
      var top = main.getBoundingClientRect().top + 90;
      var activeId = headings[0].id;
      for (var i = 0; i < headings.length; i++) {
        if (headings[i].getBoundingClientRect().top <= top) activeId = headings[i].id;
        else break;
      }
      setActiveToc(activeId);
    });
  }

  function scrollToHeading(id) {
    var h = document.getElementById(id);
    if (h) { h.scrollIntoView({ behavior: "smooth", block: "start" }); }
  }

  function onTocClick(e) {
    var a = e.target.closest("a");
    if (!a || !toc.contains(a)) return;
    var href = a.getAttribute("href") || "";
    if (href.charAt(0) !== "#") return;
    e.preventDefault();
    var id = decodeURIComponent(href.slice(1));
    lockSpy();                                   // 先锁 spy，再平滑滚动 + 一次性设激活
    setActiveToc(id);
    scrollToHeading(id);
    if (app.classList.contains("drawer")) closeDrawer();
  }

  // ============================================================
  // 5) 搜索
  // ============================================================
  var searchBar, searchInput, searchCount, searchHits = [], searchIdx = -1, searchTimer = null;

  function clearSearch() {
    if (!content) return;
    content.querySelectorAll("mark.search-hit").forEach(function (m) {
      var p = m.parentNode; if (!p) return;
      p.replaceChild(document.createTextNode(m.textContent), m);
      p.normalize();
    });
    searchHits = []; searchIdx = -1;
    if (searchCount) searchCount.textContent = "";
  }

  function runSearch(query) {
    clearSearch();
    if (!query) return;
    clearVarHighlights();
    var lower = query.toLowerCase();
    var walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        var p = node.parentNode;
        if (p && (p.tagName === "SCRIPT" || p.tagName === "STYLE")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var textNodes = [], nn;
    while ((nn = walker.nextNode())) textNodes.push(nn);

    textNodes.forEach(function (tn) {
      var text = tn.nodeValue;
      var hay = text.toLowerCase();
      var idx = hay.indexOf(lower);
      if (idx < 0) return;
      var frag = document.createDocumentFragment();
      var last = 0;
      while (idx >= 0) {
        if (idx > last) frag.appendChild(document.createTextNode(text.slice(last, idx)));
        var mk = document.createElement("mark");
        mk.className = "search-hit";
        mk.textContent = text.slice(idx, idx + query.length);
        frag.appendChild(mk);
        searchHits.push(mk);
        last = idx + query.length;
        idx = hay.indexOf(lower, last);
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (tn.parentNode) tn.parentNode.replaceChild(frag, tn);
    });

    if (searchHits.length) { searchIdx = 0; focusHit(); }
    updateSearchCount();
  }

  function updateSearchCount() {
    if (!searchCount) return;
    searchCount.textContent = searchHits.length
      ? (searchIdx + 1) + "/" + searchHits.length
      : "无结果";
  }

  function focusHit() {
    searchHits.forEach(function (m) { m.classList.remove("current"); });
    var m = searchHits[searchIdx];
    if (m) { m.classList.add("current"); m.scrollIntoView({ block: "center" }); }
    updateSearchCount();
  }

  function nextHit(dir) {
    if (!searchHits.length) return;
    searchIdx = (searchIdx + dir + searchHits.length) % searchHits.length;
    focusHit();
  }

  function openSearch() {
    searchBar.classList.add("open");
    $("searchBtn").classList.add("active");
    searchInput.focus(); searchInput.select();
  }
  function closeSearch() {
    searchBar.classList.remove("open");
    $("searchBtn").classList.remove("active");
    clearSearch();
  }

  // ============================================================
  // 侧栏 / 抽屉 / 主题 / 窗口
  // ============================================================
  function closeDrawer() { app.classList.remove("sidebar-open"); }

  function toggleSidebar() {
    if (app.classList.contains("drawer")) {
      app.classList.toggle("sidebar-open");
    } else {
      app.classList.toggle("sidebar-hidden");
    }
  }

  function onResize() {
    var drawer = window.innerWidth < 760;
    app.classList.toggle("drawer", drawer);
    if (!drawer) app.classList.remove("sidebar-open");
  }

  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("inkwell-theme", t); } catch (e) {}
    var link = $("pygments-style");
    if (link) link.href = "/assets/pygments-" + (t === "dark" ? "dark" : "light") + ".css";
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    setTheme(cur === "dark" ? "light" : "dark");
  }

  function setDocTitle(title) {
    if (docTitle) docTitle.textContent = title || "";
    document.title = title ? (title + " — Inkwell") : "Inkwell";
  }

  // ============================================================
  // 原生窗口行为：8 向缩放 + 标题栏拖动（Aero Snap 并列 / 拖回还原）
  // 通过 js_api 调 Win32 ReleaseCapture()+SendMessage(WM_NCLBUTTONDOWN,...)
  // ============================================================
  var RH = { n: "top", s: "bottom", w: "left", e: "right",
             nw: "topleft", ne: "topright", sw: "bottomleft", se: "bottomright" };

  function setupWindowResize() {
    Object.keys(RH).forEach(function (k) {
      var h = document.createElement("div");
      h.className = "resize-handle rh-" + k;
      h.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        var a = api(); if (a && a.win_native_resize) a.win_native_resize(RH[k]);
      });
      document.body.appendChild(h);
    });
  }

  function setupWindowDrag() {
    var bar = document.querySelector(".titlebar");
    if (!bar) return;
    // 拖动：超过阈值才发起原生移动（否则单击/双击不被吞掉）
    bar.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      if (e.target.closest("button, input, .win-controls, .resize-handle")) return;
      var sx = e.screenX, sy = e.screenY, started = false;
      function mm(ev) {
        if (started) return;
        if (Math.abs(ev.screenX - sx) > 4 || Math.abs(ev.screenY - sy) > 4) {
          started = true;
          var a = api(); if (a && a.win_native_drag) a.win_native_drag();
          done();
        }
      }
      function done() {
        window.removeEventListener("mousemove", mm);
        window.removeEventListener("mouseup", done);
      }
      window.addEventListener("mousemove", mm);
      window.addEventListener("mouseup", done);
    });
    // 双击标题栏：最大化/还原
    bar.addEventListener("dblclick", function (e) {
      if (e.target.closest("button, input, .win-controls")) return;
      var a = api(); if (a && a.win_toggle_maximize) a.win_toggle_maximize();
    });
  }

  // ============================================================
  // 字号缩放：Ctrl + 鼠标滚轮 调整正文字号（em 体系下整体自适应），持久化
  // ============================================================
  var FONT_MIN = 10, FONT_MAX = 26, FONT_DEFAULT = 13.5;
  function readerFont() {
    var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--reader-font"));
    return isNaN(v) ? FONT_DEFAULT : v;
  }
  function setReaderFont(px) {
    px = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px * 2) / 2));
    document.documentElement.style.setProperty("--reader-font", px + "px");
    try { localStorage.setItem("inkwell-font", px); } catch (e) {}
  }
  function setupFontZoom() {
    var saved = parseFloat(localStorage.getItem("inkwell-font"));
    if (!isNaN(saved)) setReaderFont(saved);
    // Ctrl+滚轮：放大/缩小正文（拦截 WebView2 的整页缩放）
    window.addEventListener("wheel", function (e) {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setReaderFont(readerFont() + (e.deltaY < 0 ? 0.5 : -0.5));
    }, { passive: false });
    // Ctrl + 加/减/0：键盘缩放与重置
    document.addEventListener("keydown", function (e) {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "=" || e.key === "+") { e.preventDefault(); setReaderFont(readerFont() + 0.5); }
      else if (e.key === "-") { e.preventDefault(); setReaderFont(readerFont() - 0.5); }
      else if (e.key === "0") { e.preventDefault(); setReaderFont(FONT_DEFAULT); }
    });
  }

  // 侧栏拖拽改变宽度
  function setupResizer() {
    var rz = document.createElement("div");
    rz.className = "sidebar-resizer";
    app.appendChild(rz);
    var dragging = false;
    rz.addEventListener("mousedown", function (e) { dragging = true; e.preventDefault(); document.body.style.cursor = "col-resize"; });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var w = Math.min(460, Math.max(180, e.clientX));
      document.documentElement.style.setProperty("--sidebar-w", w + "px");
    });
    window.addEventListener("mouseup", function () { dragging = false; document.body.style.cursor = ""; });
  }

  // ============================================================
  // 内容初始化（首帧 + 每次换文件）
  // ============================================================
  function initContent() {
    clearSearch();
    clearVarHighlights();
    renderMath(content);
    addCodeCopyButtons(content);
    collectHeadings();
    activeLink = null;          // 换文件后旧目录链接失效，重置激活引用
    unlockSpy();
    onScrollSpy();
  }

  // ============================================================
  // 文档间跳转：历史栈（后退/前进）+ 正文 .md 链接拦截（支持递归深入）
  // ============================================================
  var navHistory = [], navIndex = -1;

  function renderInto(p) {
    if (!p) return false;
    if (p.ok === false && !p.content) return false;
    content.innerHTML = p.content || "";
    if (toc) toc.innerHTML = p.toc || "";
    currentPath = p.path || currentPath;
    setDocTitle(p.title);
    initContent();
    return true;
  }

  function scrollAfterLoad(anchor, restoreScroll) {
    if (anchor) {
      var h = document.getElementById(anchor);
      if (h) { h.scrollIntoView({ block: "start" }); return; }
    }
    main.scrollTop = (typeof restoreScroll === "number") ? restoreScroll : 0;
  }

  function updateNavButtons() {
    var b = $("navBack"), f = $("navForward");
    if (b) b.disabled = navIndex <= 0;
    if (f) f.disabled = navIndex >= navHistory.length - 1;
  }

  function navSaveScroll() {
    if (navIndex >= 0 && navHistory[navIndex]) navHistory[navIndex].scrollTop = main.scrollTop;
  }

  // 前进式跳转（点击链接 / 打开新文件）：截断 forward 分支，压入新条目
  function navTo(p, anchor) {
    navSaveScroll();
    navHistory = navHistory.slice(0, navIndex + 1);
    navHistory.push({ path: p.path, anchor: anchor || "", scrollTop: 0 });
    navIndex = navHistory.length - 1;
    if (renderInto(p)) scrollAfterLoad(anchor, 0);
    updateNavButtons();
  }

  function navGo(delta) {
    var target = navIndex + delta;
    if (target < 0 || target >= navHistory.length) return;
    var a = api(); if (!a || !a.render_path) return;
    navSaveScroll();
    navIndex = target;
    var e = navHistory[navIndex];
    a.render_path(e.path).then(function (p) {
      if (p && p.ok === false) toast("文件可能已被移动或删除");
      if (renderInto(p)) scrollAfterLoad(null, e.scrollTop);
      updateNavButtons();
    });
  }
  function navBack() { navGo(-1); }
  function navForward() { navGo(1); }

  function navigateToMd(href) {
    var a = api(); if (!a || !a.open_md_link) return;
    a.open_md_link(href).then(function (p) {
      if (!p) return;
      if (p.samedoc) { if (p.anchor) { lockSpy(); scrollToHeading(p.anchor); } return; }
      if (p.ok === false) { toast(p.error || ("无法打开 " + href)); return; }
      navTo(p, p.anchor);
    });
  }

  // 含协议(http/mailto/...) 视为外链；但要排除 Windows 盘符 C:\ 这种"伪协议"
  function isExternalUrl(h) { return /^[a-z][a-z0-9+.\-]*:/i.test(h) && !/^[a-z]:[\\/]/i.test(h); }

  function onContentLinkClick(e) {
    var a = e.target.closest("a");
    if (!a || !content.contains(a)) return;
    var href = a.getAttribute("href");
    if (href == null || href === "") return;
    if (href.charAt(0) === "#") {                    // 同页锚点
      e.preventDefault(); lockSpy(); scrollToHeading(decodeURIComponent(href.slice(1))); return;
    }
    e.preventDefault();                              // 其余一律拦截，避免 webview 整页跳走
    if (isExternalUrl(href)) {                       // http/mailto/tel → 系统浏览器
      var ax = api(); if (ax && ax.open_external) ax.open_external(href); return;
    }
    var clean = href.split("#")[0];
    if (/\.(md|markdown|mdown|mkd)$/i.test(clean)) { navigateToMd(href); return; }   // 本地 .md → 应用内跳转
    var ay = api(); if (ay && ay.open_external) ay.open_external(href);              // 其它本地文件 → 系统默认程序
  }

  function openFileDialog() {
    var a = api(); if (!a) return;
    a.open_dialog().then(function (p) { if (p && p.ok) navTo(p, ""); });
  }

  // 轻量 toast 提示（打开失败等）
  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  // 文件变更自动刷新（同一文档）：保持滚动，不改动历史
  function applyPayload(p) {
    if (!p || p.cancelled) return;
    if (p.ok === false && !p.content) return;
    var keep = (p.path && p.path === currentPath) ? main.scrollTop : 0;
    renderInto(p);
    main.scrollTop = keep;
  }
  window.__applyPayload = applyPayload;

  // ============================================================
  // 绑定与启动
  // ============================================================
  function bind() {
    // 顶栏按钮
    $("sidebarToggle").addEventListener("click", toggleSidebar);
    $("themeBtn").addEventListener("click", toggleTheme);
    $("searchBtn").addEventListener("click", function () {
      if (searchBar.classList.contains("open")) closeSearch(); else openSearch();
    });
    $("openBtn").addEventListener("click", openFileDialog);

    // 文档跳转：后退/前进 + 正文链接拦截
    $("navBack").addEventListener("click", navBack);
    $("navForward").addEventListener("click", navForward);
    content.addEventListener("click", onContentLinkClick);

    // 窗口控制（拖动 / 双击最大化由 setupWindowDrag 处理）
    $("winMin").addEventListener("click", function () { var a = api(); if (a) a.win_minimize(); });
    $("winMax").addEventListener("click", function () { var a = api(); if (a && a.win_toggle_maximize) a.win_toggle_maximize(); });
    $("winClose").addEventListener("click", function () { var a = api(); if (a) a.win_close(); });

    // 搜索栏
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      var q = searchInput.value;
      searchTimer = setTimeout(function () { runSearch(q); }, 160);
    });
    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); nextHit(e.shiftKey ? -1 : 1); }
      else if (e.key === "Escape") { closeSearch(); }
    });
    $("searchNext").addEventListener("click", function () { nextHit(1); });
    $("searchPrev").addEventListener("click", function () { nextHit(-1); });
    $("searchClose").addEventListener("click", closeSearch);

    // 目录
    toc.addEventListener("click", onTocClick);
    main.addEventListener("scroll", onScrollSpy, { passive: true });
    main.addEventListener("scrollend", unlockSpy);   // 平滑滚动结束即解锁 spy

    // 抽屉遮罩
    $("scrim").addEventListener("click", closeDrawer);

    // 复制净化（捕获阶段）
    document.addEventListener("copy", onCopy, true);

    // 双击高亮 + 点击/Esc 清除
    content.addEventListener("dblclick", onDblClick);
    document.addEventListener("click", function (e) {
      if (!(e.target.closest && e.target.closest(".var-highlight"))) clearVarHighlights();
    });

    // 快捷键
    document.addEventListener("keydown", function (e) {
      var ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && (e.key === "f" || e.key === "F")) { e.preventDefault(); openSearch(); }
      else if (ctrl && (e.key === "b" || e.key === "B")) { e.preventDefault(); toggleSidebar(); }
      else if (ctrl && (e.key === "o" || e.key === "O")) { e.preventDefault(); openFileDialog(); }
      else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); navBack(); }
      else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); navForward(); }
      else if (e.key === "Escape") {
        if (searchBar.classList.contains("open")) closeSearch();
        else clearVarHighlights();
      }
      else if (e.key === "F3") { e.preventDefault(); nextHit(e.shiftKey ? -1 : 1); }
    });

    window.addEventListener("resize", onResize);
  }

  function boot() {
    content = $("content"); main = $("main"); app = $("app");
    sidebar = $("sidebar"); toc = $("toc"); dragRegion = $("dragRegion");
    docTitle = $("docTitle");
    searchBar = $("searchbar"); searchInput = $("searchInput"); searchCount = $("searchCount");

    var t = document.documentElement.getAttribute("data-theme") || "light";
    setTheme(t);

    var bootData = window.__BOOT__ || {};
    setDocTitle(bootData.title || "Inkwell");
    // 用初始文档播种跳转历史
    if (bootData.path) {
      currentPath = bootData.path;
      navHistory = [{ path: bootData.path, anchor: "", scrollTop: 0 }];
      navIndex = 0;
    }

    bind();
    setupResizer();
    setupWindowResize();
    setupWindowDrag();
    setupFontZoom();
    onResize();
    initContent();
    updateNavButtons();
  }

  // 测试钩子（无害；供自动化探针验证净化/渲染/高亮）
  window.__ink = {
    sanitize: sanitizeForCopy, render: renderMath,
    highlight: highlightToken, clearHL: clearVarHighlights, runSearch: runSearch,
    copyPayload: buildCopyPayload,
    nav: {
      to: navigateToMd, back: navBack, forward: navForward,
      state: function () {
        return { index: navIndex, len: navHistory.length, path: currentPath,
                 title: (docTitle && docTitle.textContent) || "" };
      }
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
