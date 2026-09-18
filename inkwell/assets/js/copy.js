// 复制净化：飞书/Word 等目标粘贴时不带底色、不带 Pygments 着色，公式还原为 LaTeX，
// 表格保留结构并生成 TSV 纯文本。
import { state } from "./state.js";
import { copyImage } from "./viewer.js";

const STRIP_PROPS = [
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

export function sanitizeForCopy(holder) {
  // step 0: 把渲染后的公式替换回 $tex$ / $$tex$$ 纯文本
  // holder 本身没有挂入 document，因此不能用 isConnected 判断克隆节点是否有效。
  holder.querySelectorAll(".math-block[data-latex], .math-inline[data-latex]").forEach(function (host) {
    var tex = host.getAttribute("data-latex");
    var marker = host.classList.contains("math-block") ? "$$" : "$";
    host.replaceWith(document.createTextNode(marker + tex + marker));
  });
  // 部分选区可能只克隆到 KaTeX 内部节点，用 annotation 作为后备。
  holder.querySelectorAll(".katex-display, .katex").forEach(function (k) {
    if (!holder.contains(k)) return;
    var host = k.classList.contains("katex-display") ? k : (k.closest(".katex-display") || k);
    var tex = texFromKatex(k);
    var marker = host.classList.contains("katex-display") ? "$$" : "$";
    host.replaceWith(document.createTextNode(tex != null ? (marker + tex + marker) : (k.textContent || "")));
  });
  // step 1: 删除纯 UI 元素
  holder.querySelectorAll(".code-copy-btn, .code-copy-float, .math-copy-btn, .image-copy-btn, .code-block-header")
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
    el.removeAttribute("data-inkwell-copyable");
    el.removeAttribute("tabindex");
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
export function buildCopyPayload(sel) {
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

export function onCopy(e) {
  try {
    var p = buildCopyPayload(window.getSelection());
    if (p && e.clipboardData) {
      e.clipboardData.setData("text/html", p.html);
      e.clipboardData.setData("text/plain", p.plain);
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
    // 图片被点击或通过 Tab 聚焦后，Ctrl+C 直接复制像素内容而非图片 URL。
    if (state.selectedImage && state.selectedImage.isConnected) {
      e.preventDefault();
      e.stopImmediatePropagation();
      var imageButton = state.selectedImage.closest(".image-block");
      copyImage(state.selectedImage, imageButton && imageButton.querySelector(".image-copy-btn"));
    }
  } catch (err) { /* 出错则放行默认复制 */ }
}
