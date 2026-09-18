// 阅读态：公式渲染、代码复制按钮、双击高亮、目录 + scroll spy、搜索、initContent。
import { state } from "./state.js";
import { $, escapeReg, writeClipboard, flash, idSelector, inRenderableRoot } from "./util.js";
import { toggleMermaid, zoomMermaid, initMermaidDiagrams } from "./mermaid.js";
import { copyImage, setupImageCopySupport } from "./viewer.js";
import { closeDrawer } from "./chrome.js";

// ============================================================
// 公式渲染：直接用 data-latex 调 katex.render（不扫描正文）
// ============================================================
export function renderMath(root) {
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

// 复制单条公式的 LaTeX（由正文容器统一事件委托）
function copyLatex(btn) {
  var host = btn.closest("[data-latex]");
  if (!host) return;
  var tex = host.getAttribute("data-latex") || "";
  writeClipboard(tex);
  flash(btn, "已复制");
}

// ============================================================
// 代码块双击高亮（VSCode 风格）
// ============================================================
function getWordAtPoint(x, y) {
  var pos = document.caretRangeFromPoint(x, y);
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

export function clearVarHighlights() {
  if (!state.content) return;
  state.content.querySelectorAll(".var-highlight").forEach(function (sp) {
    var p = sp.parentNode;
    if (!p) return;
    p.replaceChild(document.createTextNode(sp.textContent), sp);
    p.normalize();
  });
}

export function highlightToken(block, word) {
  block.normalize();
  // word 已在 onDblClick 里校验为 ^[A-Za-z_]\w*$，\b 与后行断言写法等价。
  var re = new RegExp("\\b" + escapeReg(word) + "\\b", "g");
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
  if (!block || !state.content.contains(block)) return;
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

function addCodeCopyButtons(root) {
  root.querySelectorAll(".codehilite").forEach(function (box) {
    if (box.closest(".code-block-wrapper")) return;        // 已有头部按钮
    if (box.querySelector(".code-copy-float")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy-float";
    btn.setAttribute("data-copy-action", "code");
    btn.textContent = "复制";
    box.appendChild(btn);
  });
}

export function onContentActionClick(e) {
  var mermaidButton = e.target.closest && e.target.closest("[data-mermaid-action]");
  if (mermaidButton && inRenderableRoot(mermaidButton)) {
    e.preventDefault();
    e.stopPropagation();
    var mAction = mermaidButton.getAttribute("data-mermaid-action");
    var mBlock = mermaidButton.closest(".mermaid-block");
    if (mAction === "toggle") toggleMermaid(mBlock);
    else if (mAction === "zoom") zoomMermaid(mBlock);
    return;
  }
  var btn = e.target.closest && e.target.closest("[data-copy-action]");
  if (!btn || !inRenderableRoot(btn)) return;
  var action = btn.getAttribute("data-copy-action");
  if (action !== "latex" && action !== "code" && action !== "image") return;
  e.preventDefault();
  e.stopPropagation();
  if (action === "latex") copyLatex(btn);
  else if (action === "code") copyCode(btn);
  else {
    var block = btn.closest(".image-block");
    copyImage(block && block.querySelector("img"), btn);
  }
}

// ============================================================
// 目录：滚动高亮 + 点击跳转
// ============================================================
function collectHeadings() {
  state.headings = Array.prototype.slice.call(
    state.content.querySelectorAll("h1[id],h2[id],h3[id],h4[id]")
  );
}

// 仅当激活项滚出侧栏可视区时才把它滚进来——避免每次 spy 都 scrollIntoView 造成抖动
function ensureTocVisible(link) {
  if (!state.sidebar) return;
  var lr = link.getBoundingClientRect(), sr = state.sidebar.getBoundingClientRect();
  if (lr.top < sr.top + 6 || lr.bottom > sr.bottom - 6) {
    link.scrollIntoView({ block: "nearest" });
  }
}

function setActiveToc(id) {
  if (!state.toc) return;
  var link = id ? state.toc.querySelector('a[href="' + idSelector(id) + '"]') : null;
  if (link === state.activeLink) return;             // 无变化则不动，杜绝重复 class 抖动
  if (state.activeLink) state.activeLink.classList.remove("active");
  state.activeLink = link;
  if (link) { link.classList.add("active"); ensureTocVisible(link); }
}

export function lockSpy() {                            // 平滑滚动期间锁住 spy，避免高亮反复跳动闪烁
  state.spyLock = true;
  clearTimeout(state.spyLockTimer);
  state.spyLockTimer = setTimeout(function () { state.spyLock = false; }, 1500);
}
export function unlockSpy() { state.spyLock = false; clearTimeout(state.spyLockTimer); }

export function onScrollSpy() {
  if (state.spyLock || state.spyTick) return;
  state.spyTick = true;
  requestAnimationFrame(function () {
    state.spyTick = false;
    if (!state.headings.length) return;
    var top = state.main.getBoundingClientRect().top + 90;
    var activeId = state.headings[0].id;
    for (var i = 0; i < state.headings.length; i++) {
      if (state.headings[i].getBoundingClientRect().top <= top) activeId = state.headings[i].id;
      else break;
    }
    setActiveToc(activeId);
  });
}

export function scrollToHeading(id) {
  if (!id) return;
  var h = null;
  // 编辑态：标题在 #editorLive 内（与隐藏的 #content 可能同 id）
  if (state.editMode && state.editorLive) {
    h = state.editorLive.querySelector(idSelector(id));
  }
  if (!h) h = document.getElementById(id);
  if (h) h.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function onTocClick(e) {
  var a = e.target.closest("a");
  if (!a || !state.toc.contains(a)) return;
  var href = a.getAttribute("href") || "";
  if (href.charAt(0) !== "#") return;
  e.preventDefault();
  var id = decodeURIComponent(href.slice(1));
  lockSpy();                                   // 先锁 spy，再平滑滚动 + 一次性设激活
  setActiveToc(id);
  scrollToHeading(id);
  if (state.app.classList.contains("drawer")) closeDrawer();
}

// ============================================================
// 搜索
// ============================================================
export function clearSearch() {
  if (!state.content) return;
  state.content.querySelectorAll("mark.search-hit").forEach(function (m) {
    var p = m.parentNode; if (!p) return;
    p.replaceChild(document.createTextNode(m.textContent), m);
    p.normalize();
  });
  state.searchHits = []; state.searchIdx = -1;
  if (state.searchCount) state.searchCount.textContent = "";
}

export function runSearch(query) {
  clearSearch();
  if (!query) return;
  clearVarHighlights();
  var lower = query.toLowerCase();
  var walker = document.createTreeWalker(state.content, NodeFilter.SHOW_TEXT, {
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
      state.searchHits.push(mk);
      last = idx + query.length;
      idx = hay.indexOf(lower, last);
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    if (tn.parentNode) tn.parentNode.replaceChild(frag, tn);
  });

  if (state.searchHits.length) { state.searchIdx = 0; focusHit(); }
  updateSearchCount();
}

function updateSearchCount() {
  if (!state.searchCount) return;
  state.searchCount.textContent = state.searchHits.length
    ? (state.searchIdx + 1) + "/" + state.searchHits.length
    : "无结果";
}

function focusHit() {
  state.searchHits.forEach(function (m) { m.classList.remove("current"); });
  var m = state.searchHits[state.searchIdx];
  if (m) { m.classList.add("current"); m.scrollIntoView({ block: "center" }); }
  updateSearchCount();
}

export function nextHit(dir) {
  if (!state.searchHits.length) return;
  state.searchIdx = (state.searchIdx + dir + state.searchHits.length) % state.searchHits.length;
  focusHit();
}

export function openSearch() {
  state.searchBar.classList.add("open");
  $("searchBtn").classList.add("active");
  state.searchInput.focus(); state.searchInput.select();
}
export function closeSearch() {
  state.searchBar.classList.remove("open");
  $("searchBtn").classList.remove("active");
  clearSearch();
}

// ============================================================
// 内容初始化：渲染出的 HTML 片段接上公式 / 代码复制 / Mermaid / 图片交互。
// initContent 用于整篇正文（另加目录与搜索状态重置）；Live Preview 的单个块
// 视图只需要这部分公共装配，见 initRenderedFragment。
// ============================================================
export function initRenderedFragment(root) {
  if (!root) return;
  renderMath(root);
  addCodeCopyButtons(root);
  initMermaidDiagrams(root);
  setupImageCopySupport(root);
}

export function initContent() {
  clearSearch();
  clearVarHighlights();
  initRenderedFragment(state.content);
  collectHeadings();
  state.activeLink = null;          // 换文件后旧目录链接失效，重置激活引用
  unlockSpy();
  onScrollSpy();
}

export function bindReaderEvents() {
  $("searchBtn").addEventListener("click", function () {
    if (state.editMode) return; // 编辑态搜索正文无意义
    if (state.searchBar.classList.contains("open")) closeSearch(); else openSearch();
  });
  state.toc.addEventListener("click", onTocClick);
  state.main.addEventListener("scroll", onScrollSpy, { passive: true });
  state.main.addEventListener("scrollend", unlockSpy);   // 平滑滚动结束即解锁 spy
  state.content.addEventListener("dblclick", onDblClick);
  document.addEventListener("click", function (e) {
    if (!(e.target.closest && e.target.closest(".var-highlight"))) clearVarHighlights();
  });
  state.searchInput.addEventListener("input", function () {
    clearTimeout(state.searchTimer);
    var q = state.searchInput.value;
    state.searchTimer = setTimeout(function () { runSearch(q); }, 160);
  });
  state.searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); nextHit(e.shiftKey ? -1 : 1); }
    else if (e.key === "Escape") { closeSearch(); }
  });
  $("searchNext").addEventListener("click", function () { nextHit(1); });
  $("searchPrev").addEventListener("click", function () { nextHit(-1); });
  $("searchClose").addEventListener("click", closeSearch);
}
