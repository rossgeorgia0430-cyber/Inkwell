// 正文直接编辑与段落源码编辑共用同一份 Markdown 缓冲。
// Markdown 全文（state.editorText）为真相，state.lpBlocks 是它的分块缓存。
import { state } from "../state.js";
import { $, api, toast, formatModShortcut, holdHeightWhileImagesLoad } from "../util.js";
import { initRenderedFragment } from "../reader.js";
import {
  classifyBlockKind, splitMarkdownBlocks, joinMarkdownBlocks,
  bufferWithMarkers, splitPreviewHtml, withReferenceDefinitions, isHeadingBlock,
} from "./blocks.js";
// editor.js 反过来也从本模块导入（进入/退出/保存都要驱动块级 DOM）——这是一个
// 有意为之的模块间循环引用：两边都只在事件回调里调用对方，不在模块顶层求值时
// 调用，ES 模块的循环导入在这种用法下是安全的。
import { prepareVisualBlock, appendBlockTools } from "./visual.js";
import { saveEdit, exitEditMode } from "./editor.js";

export function setEditStatus(msg, kind) {
  if (!state.editorStatus) return;
  state.editorStatus.textContent = msg || "";
  state.editorStatus.classList.remove("warn", "ok");
  if (kind) state.editorStatus.classList.add(kind);
}

function applyBlockKindClass(blockEl, kind) {
  if (!blockEl) return;
  blockEl.classList.remove("is-fence", "is-math", "is-table", "is-heading", "is-list",
    "h-1", "h-2", "h-3", "h-4", "h-5", "h-6");
  if (!kind) return;
  if (kind.indexOf("fence") === 0) blockEl.classList.add("is-fence");
  else if (kind === "math") blockEl.classList.add("is-math");
  else if (kind === "table") blockEl.classList.add("is-table");
  else if (kind === "list") blockEl.classList.add("is-list");
  else if (kind.indexOf("heading:") === 0) {
    blockEl.classList.add("is-heading");
    var lv = kind.split(":")[1];
    if (lv) blockEl.classList.add("h-" + lv);
  }
}

let bufferTimer;

function bufferText() {
  if (state.editorText === null) state.editorText = joinMarkdownBlocks(state.lpBlocks);
  return state.editorText;
}

export function syncBufferFromBlocks() {
  state.editorText = null;
  setDirty(true);
  clearTimeout(bufferTimer);
  bufferTimer = setTimeout(() => { if (state.editMode) markDirtyFromBuffer(); }, 350);
}

export function markDirtyFromBuffer() {
  if (!state.editMode) return;
  setDirty(bufferText() !== state.editBaseline);
}

export function updateDirtyUI() {
  if (state.docTitle) {
    if (state.editDirty) state.docTitle.classList.add("dirty");
    else state.docTitle.classList.remove("dirty");
  }
  var btn = $("editBtn");
  if (btn) {
    btn.classList.toggle("active", state.editMode);
    btn.setAttribute("aria-pressed", state.editMode ? "true" : "false");
    btn.title = state.editMode
      ? (state.editDirty ? "退出编辑（有未保存更改）" : "退出编辑模式 (" + formatModShortcut("mod+E") + ")")
      : "编辑模式 (" + formatModShortcut("mod+E") + ")";
  }
  var saveBtn = $("editSaveBtn");
  if (saveBtn) saveBtn.classList.toggle("primary", state.editDirty);
}

export function setDirty(next) {
  if (state.editDirty === !!next) return;
  state.editDirty = !!next;
  updateDirtyUI();
}

export function getActiveTextarea() {
  if (!state.editorLive || state.activeBlockIdx < 0) return null;
  // 必须用 data-idx 对齐，避免 DOM .is-active 与 activeBlockIdx 竞态错配
  return state.editorLive.querySelector(
    '.lp-block[data-idx="' + state.activeBlockIdx + '"] .lp-source'
  );
}

export function flushActiveBlock() {
  var ta = getActiveTextarea();
  if (!ta) return;
  var idx = state.activeBlockIdx;
  if (idx < 0 || !state.lpBlocks[idx] || state.lpBlocks[idx].text === ta.value) return;
  state.lpBlocks[idx].text = ta.value;
  syncBufferFromBlocks();
}

export function invalidateLiveRender() {
  // 让进行中的 preview 回调失效，避免把用户新激活的块盖掉
  state.editPreviewSeq += 1;
}

export function getEditText() {
  flushActiveBlock();
  return bufferText() || "";
}

export function setEditText(t, opts) {
  opts = opts || {};
  var text = t == null ? "" : String(t);
  state.editorText = text;
  state.lpBlocks = splitMarkdownBlocks(text);
  state.activeBlockIdx = opts.activate == null ? state.activeBlockIdx : opts.activate;
  if (state.activeBlockIdx >= state.lpBlocks.length) state.activeBlockIdx = state.lpBlocks.length - 1;
  markDirtyFromBuffer();
  if (opts.render !== false) runLiveRender();
}

function isStructuredKind(kind) {
  if (!kind) return false;
  return kind === "table" || kind === "math" || kind.indexOf("fence") === 0;
}

export function autosizeTextarea(ta) {
  if (!ta) return;
  // 表格/围栏：高度锁定，内部滚动，避免整页被撑开
  var block = ta.closest && ta.closest(".lp-block");
  if (block && (block.classList.contains("is-table") || block.classList.contains("is-fence") || block.classList.contains("is-math"))) {
    var lock = parseFloat(block.style.height || block.style.minHeight) || 0;
    if (lock > 0) {
      ta.style.height = "100%";
      ta.style.maxHeight = "100%";
      ta.style.overflow = "auto";
      return;
    }
  }
  var minH = parseFloat(ta.style.minHeight) || 0;
  // 避免 height=0 触发滚动锚定跳动
  ta.style.height = "auto";
  var next = Math.max(minH || 0, ta.scrollHeight);
  if (!minH) next = Math.max(next, Math.ceil((parseFloat(getComputedStyle(ta).lineHeight) || 22)));
  // 非结构化块也设上限，防止巨段拖垮布局
  var cap = Math.floor(window.innerHeight * 0.7);
  if (next > cap) {
    ta.style.height = cap + "px";
    ta.style.overflow = "auto";
  } else {
    ta.style.height = next + "px";
    ta.style.overflow = "hidden";
  }
}

function resolveBlockIdxFromEl(el) {
  var b = el && el.closest && el.closest(".lp-block");
  if (!b) return -1;
  var n = parseInt(b.dataset.idx, 10);
  return isNaN(n) ? -1 : n;
}

// 等预览返回的只读源码框：内容已写回缓冲，由预览回调负责换成渲染结果
function isPendingSource(el) {
  return el.readOnly || !!el.querySelector("textarea.lp-source[readonly]");
}

export function teardownActiveTextarea(opts) {
  // 拆掉孤立 textarea。opts.skipWrite：结构已 re-split 后禁止把旧全文写回新块
  opts = opts || {};
  if (!state.editorLive) return;
  if (!opts.skipWrite) {
    var tas = Array.from(state.editorLive.querySelectorAll(".lp-block.is-active .lp-source, textarea.lp-source"))
      .filter(function (el) { return !isPendingSource(el); });
    for (var i = 0; i < tas.length; i++) {
      var ta = tas[i];
      var idx = resolveBlockIdxFromEl(ta);
      if (idx >= 0 && state.lpBlocks[idx]) {
        state.lpBlocks[idx].text = ta.value;
      }
    }
    if (tas.length) syncBufferFromBlocks();
  }
  var actives = state.editorLive.querySelectorAll(".lp-block.is-active");
  for (var j = 0; j < actives.length; j++) {
    if (isPendingSource(actives[j])) continue;
    var idx2 = parseInt(actives[j].dataset.idx, 10);
    if (!isNaN(idx2) && state.lpBlocks[idx2]) {
      fillBlockView(actives[j], idx2);
    } else {
      // 索引失效：直接拆掉 DOM，不写缓冲
      actives[j].classList.remove("is-active");
      actives[j].innerHTML = "";
    }
  }
  // 仍残留的 textarea 强制移除
  var leftovers = state.editorLive.querySelectorAll("textarea.lp-source");
  for (var k = 0; k < leftovers.length; k++) {
    if (isPendingSource(leftovers[k])) continue;
    var host = leftovers[k].closest(".lp-block");
    if (host) {
      host.classList.remove("is-active");
      var hi = parseInt(host.dataset.idx, 10);
      if (!isNaN(hi) && state.lpBlocks[hi]) fillBlockView(host, hi);
      else host.innerHTML = "";
    }
  }
}

// —— 滚动锚点：全页重挂 / 激活块时避免跳到顶部 ——
function captureLiveScrollAnchor() {
  if (!state.editorLiveWrap || !state.editorLive) return null;
  var wrap = state.editorLiveWrap;
  var wrapRect = wrap.getBoundingClientRect();
  var blocks = state.editorLive.querySelectorAll(".lp-block");
  for (var i = 0; i < blocks.length; i++) {
    var r = blocks[i].getBoundingClientRect();
    if (r.bottom > wrapRect.top + 6) {
      return {
        idx: parseInt(blocks[i].dataset.idx, 10),
        offset: r.top - wrapRect.top,
        scrollTop: wrap.scrollTop
      };
    }
  }
  return { idx: -1, offset: 0, scrollTop: wrap.scrollTop };
}

function restoreLiveScrollAnchor(anchor) {
  if (!anchor || !state.editorLiveWrap) return;
  if (anchor.idx >= 0 && !isNaN(anchor.idx)) {
    var el = getBlockEl(anchor.idx);
    if (el) {
      var wrapRect = state.editorLiveWrap.getBoundingClientRect();
      var r = el.getBoundingClientRect();
      state.editorLiveWrap.scrollTop += (r.top - wrapRect.top) - anchor.offset;
      return;
    }
  }
  state.editorLiveWrap.scrollTop = anchor.scrollTop;
}

function getBlockEl(idx) {
  if (!state.editorLive || idx < 0) return null;
  return state.editorLive.querySelector('.lp-block[data-idx="' + idx + '"]');
}

export function focusNoScroll(el) {
  if (!el) return;
  var top = state.editorLiveWrap ? state.editorLiveWrap.scrollTop : 0;
  el.focus({ preventScroll: true });
  if (state.editorLiveWrap) state.editorLiveWrap.scrollTop = top;
}

// 缓存的 HTML 是不是这份源码渲染出来的（直接编辑单元格、段落只改源码不改缓存）。
function hasFreshHtml(idx) {
  var block = state.lpBlocks[idx];
  return !!block && state.lastHtmlTexts[idx] === block.text && state.lastHtmlParts[idx] != null;
}

// 未激活块的视图：缓存与源码对应就用渲染结果；否则退回纯文本，只留「源码」按钮，不开放直接编辑。
function buildBlockViewEl(idx) {
  var view = document.createElement("div");
  view.className = "lp-view";
  var raw = (state.lpBlocks[idx] && state.lpBlocks[idx].text) || "";
  var fresh = hasFreshHtml(idx);
  var html = fresh ? state.lastHtmlParts[idx] : "";
  if (html.trim()) {
    view.innerHTML = html;
  } else if (!raw.trim()) {
    view.innerHTML = "<p class='lp-empty-hint'>点击此处开始写作…</p>";
  } else {
    view.textContent = raw;
    if (!fresh) {
      appendBlockTools(view, null, true);
      return view;
    }
  }
  prepareVisualBlock(view, state.lpBlocks[idx].text, text => {
    if (!view.isConnected) return;
    state.lpBlocks[idx].text = text;
    invalidateLiveRender();
    syncBufferFromBlocks();
  });
  return view;
}

function fillBlockView(blockEl, idx) {
  if (!blockEl) return;
  var heldHeight = blockEl.getBoundingClientRect().height;
  var kind = classifyBlockKind(state.lpBlocks[idx] ? state.lpBlocks[idx].text : "");
  applyBlockKindClass(blockEl, kind);
  blockEl.classList.remove("is-active");
  delete blockEl.dataset.lpKindSticky;
  blockEl.style.height = "";
  blockEl.style.maxHeight = "";
  blockEl.style.minHeight = "";
  blockEl.style.marginTop = "";
  blockEl.style.marginBottom = "";
  blockEl.innerHTML = "";
  var view = buildBlockViewEl(idx);
  blockEl.appendChild(view);
  if (hasFreshHtml(idx)) initRenderedFragment(view);
  holdHeightWhileImagesLoad(blockEl, heldHeight);
}

// 源码框退出时恢复渲染视图：缓存对得上就同步填回；否则源码框原地只读保留，
// 等预览返回后一步换成渲染结果，避免中间闪一帧未渲染的原文。
function restoreBlockView(idx) {
  var el = getBlockEl(idx);
  if (!el) return;
  var ta = el.querySelector("textarea.lp-source");
  if (hasFreshHtml(idx) || !ta) {
    fillBlockView(el, idx);
    return;
  }
  ta.readOnly = true;
  refreshSingleBlockView(idx);
}

// 激活块与 mountLiveDom 重挂时的激活块都需要同一种 textarea：class/属性/初值/绑定一致。
function createBlockTextarea(idx, value) {
  var ta = document.createElement("textarea");
  ta.className = "lp-source";
  ta.spellcheck = false;
  ta.setAttribute("aria-label", "编辑块 " + (idx + 1));
  ta.value = value || "";
  bindActiveTextarea(ta);
  return ta;
}

// 块与前后相邻块之间的实际间距（含穿过块边界折叠出来的子元素外边距）。
function measureBlockGaps(blockEl) {
  var rect = blockEl.getBoundingClientRect();
  var prev = blockEl.previousElementSibling;
  var next = blockEl.nextElementSibling;
  var live = blockEl.parentElement.getBoundingClientRect();
  var style = getComputedStyle(blockEl.parentElement);
  // 首尾块的外侧是编辑区的内边距
  return {
    top: rect.top - (prev ? prev.getBoundingClientRect().bottom : live.top + parseFloat(style.paddingTop)),
    bottom: (next ? next.getBoundingClientRect().top : live.bottom - parseFloat(style.paddingBottom)) - rect.bottom,
  };
}

// 渲染内容清空后子元素的外边距消失，间距变小；补成块自己的外边距，前后内容不跟着挪。
function keepBlockGaps(blockEl, before) {
  var after = measureBlockGaps(blockEl);
  if (after.top < before.top - 0.5) blockEl.style.marginTop = before.top + "px";
  if (after.bottom < before.bottom - 0.5) blockEl.style.marginBottom = before.bottom + "px";
}

function activateBlockInPlace(idx, opts) {
  opts = opts || {};
  var blockEl = getBlockEl(idx);
  if (!blockEl || !state.lpBlocks[idx]) return false;
  var view = blockEl.querySelector(".lp-view");
  var minH = 0;
  if (view) {
    minH = view.getBoundingClientRect().height;
  } else if (blockEl.getBoundingClientRect) {
    minH = blockEl.getBoundingClientRect().height;
  }
  var anchor = captureLiveScrollAnchor();
  var gaps = measureBlockGaps(blockEl);
  var kind = classifyBlockKind(state.lpBlocks[idx].text);
  // 先锁高度再清空，避免布局塌缩导致滚动跳动
  if (minH > 0) {
    blockEl.style.minHeight = minH + "px";
    if (isStructuredKind(kind)) {
      blockEl.style.height = minH + "px";
      blockEl.style.maxHeight = "min(70vh, 28rem)";
    }
  }
  blockEl.innerHTML = "";
  blockEl.classList.add("is-active");
  applyBlockKindClass(blockEl, kind);
  blockEl.dataset.lpKindSticky = kind;

  var ta = createBlockTextarea(idx, state.lpBlocks[idx].text);
  if (minH > 0) ta.style.minHeight = minH + "px";
  if (isStructuredKind(kind) && minH > 0) {
    ta.style.height = "100%";
    ta.style.overflow = "auto";
  }
  blockEl.appendChild(ta);
  keepBlockGaps(blockEl, gaps);
  state.activeBlockIdx = idx;
  autosizeTextarea(ta);
  if (!isStructuredKind(kind) && minH > 0 && ta.offsetHeight < minH) {
    ta.style.height = minH + "px";
  }
  if (opts.cursorEnd) {
    var len = ta.value.length;
    ta.selectionStart = ta.selectionEnd = len;
  }
  focusNoScroll(ta);
  restoreLiveScrollAnchor(anchor);
  requestAnimationFrame(function () {
    restoreLiveScrollAnchor(anchor);
    focusNoScroll(ta);
    requestAnimationFrame(function () {
      restoreLiveScrollAnchor(anchor);
    });
  });
  return true;
}

function bindActiveTextarea(ta) {
  if (!ta || ta._lpBound) return;
  ta._lpBound = true;
  ta.addEventListener("compositionstart", function () { ta._lpComposing = true; });
  ta.addEventListener("compositionend", function () {
    ta._lpComposing = false;
    var idx = resolveBlockIdxFromEl(ta);
    if (idx >= 0 && state.lpBlocks[idx]) {
      state.lpBlocks[idx].text = ta.value;
      syncBufferFromBlocks();
    }
    autosizeTextarea(ta);
    if (ta._lpPendingActivate != null) {
      var next = ta._lpPendingActivate;
      ta._lpPendingActivate = null;
      activateBlock(next);
    }
  });
  ta.addEventListener("input", function () {
    if (ta._lpComposing || ta.isComposing) return;
    var idx = resolveBlockIdxFromEl(ta);
    if (idx < 0 || !state.lpBlocks[idx]) return;
    state.lpBlocks[idx].text = ta.value;
    syncBufferFromBlocks();
    // sticky kind：编辑过程中不降级（避免表格 thrash）
    var bel = getBlockEl(idx);
    var sticky = bel && bel.dataset.lpKindSticky;
    if (sticky) applyBlockKindClass(bel, sticky);
    autosizeTextarea(ta);
  });
  ta.addEventListener("keydown", onActiveTextareaKeydown);
  ta.addEventListener("blur", function () {
    setTimeout(function () {
      if (!ta.isConnected || !state.editMode || state.lpActivating) return;
      if (ta._lpComposing || ta.isComposing) return;
      var ae = document.activeElement;
      if (ae && ae.classList && ae.classList.contains("lp-source")) return;
      if (ae && state.editorPane && state.editorPane.contains(ae) && ae !== state.editorLive && !(state.editorLive && state.editorLive.contains(ae))) {
        flushActiveBlock();
        return;
      }
      if (ae && state.editorLive && state.editorLive.contains(ae) && ae !== state.editorLive) return;
      deactivateBlock();
    }, 0);
  });
}

function onActiveTextareaKeydown(e) {
  var ta = e.target;
  var ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    saveEdit();
    return;
  }
  if (ctrl && e.shiftKey && (e.key === "p" || e.key === "P")) {
    e.preventDefault();
    flushActiveBlock();
    deactivateBlock();
    return;
  }
  if (e.key === "Tab") {
    e.preventDefault();
    var s = ta.selectionStart, en = ta.selectionEnd;
    if (s !== en) {
      var block = ta.value.slice(s, en);
      var indented = block.split("\n").map(function (line) { return "  " + line; }).join("\n");
      ta.value = ta.value.slice(0, s) + indented + ta.value.slice(en);
      ta.selectionStart = s;
      ta.selectionEnd = s + indented.length;
    } else {
      ta.value = ta.value.slice(0, s) + "  " + ta.value.slice(en);
      ta.selectionStart = ta.selectionEnd = s + 2;
    }
    var tidx = resolveBlockIdxFromEl(ta);
    if (tidx >= 0) state.lpBlocks[tidx].text = ta.value;
    syncBufferFromBlocks();
    autosizeTextarea(ta);
    return;
  }
  if (e.key === "Escape") {
    if (ta._lpComposing || ta.isComposing) return;
    e.preventDefault();
    e.stopPropagation();
    deactivateBlock();
  }
}

// mountLiveDom 全页重挂前，先把当前激活块（若要保留）的最新值/选区取出来，
// 重挂后按同一索引把它放回去。
function captureMountRestoreState(opts) {
  var keepActive = !!opts.keepActive && state.activeBlockIdx >= 0 && state.activeBlockIdx < state.lpBlocks.length;
  var restoreIdx = keepActive ? state.activeBlockIdx : -1;
  var restoreVal = null, selStart = null, selEnd = null;
  if (restoreIdx >= 0) {
    var oldTa = getActiveTextarea();
    if (oldTa) {
      restoreVal = oldTa.value;
      selStart = oldTa.selectionStart;
      selEnd = oldTa.selectionEnd;
    } else if (state.lpBlocks[restoreIdx]) {
      restoreVal = state.lpBlocks[restoreIdx].text;
    }
  }
  return { restoreIdx: restoreIdx, restoreVal: restoreVal, selStart: selStart, selEnd: selEnd };
}

function buildLiveBlocks(restore) {
  const fragment = document.createDocumentFragment();
  for (var i = 0; i < state.lpBlocks.length; i++) {
    var blockEl = document.createElement("div");
    blockEl.className = "lp-block";
    blockEl.dataset.idx = String(i);
    var kind = classifyBlockKind(state.lpBlocks[i].text);
    applyBlockKindClass(blockEl, kind);

    if (i === restore.restoreIdx) {
      blockEl.classList.add("is-active");
      var ta = createBlockTextarea(i, restore.restoreVal != null ? restore.restoreVal : state.lpBlocks[i].text);
      blockEl.appendChild(ta);
    } else {
      blockEl.appendChild(buildBlockViewEl(i));
    }
    fragment.appendChild(blockEl);
  }
  state.editorLive.appendChild(fragment);
  initRenderedFragment(state.editorLive);
}

function restoreMountFocus(restore) {
  state.activeBlockIdx = restore.restoreIdx;
  if (restore.restoreIdx < 0) return;
  var focusTa = getActiveTextarea();
  if (!focusTa) return;
  autosizeTextarea(focusTa);
  if (restore.selStart != null) {
    // textarea 对越界的 selectionStart/End 会自动夹取到合法范围，不会抛异常。
    focusTa.selectionStart = restore.selStart;
    focusTa.selectionEnd = restore.selEnd;
  }
  focusNoScroll(focusTa);
}

export function editSurfaceShown() {
  return document.body.classList.contains("edit-mode");
}

// 首次挂载完成后，在同一帧里把阅读区换成编辑区；返回阅读区当时的滚动位置。
// 之前一直保持阅读区可见，进入编辑不会出现空白帧。
function showEditSurface() {
  if (editSurfaceShown()) return null;
  var top = state.main ? state.main.scrollTop : 0;
  document.body.classList.add("edit-mode");
  if (state.editorPane) state.editorPane.hidden = false;
  return top;
}

// 首次渲染失败时编辑区还没显示，直接退回阅读态并说明原因。
function abortEnter(message) {
  if (editSurfaceShown()) return false;
  toast(message);
  exitEditMode({ force: true });
  return true;
}

export function mountLiveDom(htmlParts, opts) {
  opts = opts || {};
  if (!state.editorLive) return;
  var anchor = captureLiveScrollAnchor();
  var restore = captureMountRestoreState(opts);

  state.lastHtmlParts = htmlParts || state.lastHtmlParts;
  if (opts.texts) state.lastHtmlTexts = opts.texts;
  state.editorLive.innerHTML = "";

  buildLiveBlocks(restore);
  var enterScroll = showEditSurface();
  if (enterScroll != null) anchor = { idx: -1, offset: 0, scrollTop: enterScroll };
  restoreMountFocus(restore);
  restoreLiveScrollAnchor(anchor);
  requestAnimationFrame(function () {
    if (anchor) restoreLiveScrollAnchor(anchor);
  });
}

export function activateBlock(idx, opts) {
  opts = opts || {};
  if (!state.editMode || idx < 0 || idx >= state.lpBlocks.length) return;
  if (idx === state.activeBlockIdx && getActiveTextarea()) {
    focusNoScroll(getActiveTextarea());
    return;
  }
  // IME 组合中：延后切换，避免吞字
  var curTa = getActiveTextarea();
  if (curTa && (curTa._lpComposing || curTa.isComposing)) {
    curTa._lpPendingActivate = idx;
    return;
  }
  invalidateLiveRender();
  state.lpActivating = true;
  var prev = state.activeBlockIdx;
  flushActiveBlock();

  // 切换块：尽量就地改 DOM，不全页重挂（减少跳动）
  if (prev >= 0 && prev !== idx) {
    var full = bufferText();
    var oldCount = state.lpBlocks.length;
    state.lpBlocks = splitMarkdownBlocks(full);
    // 结构未变：只刷新上一块 HTML，再就地激活目标块
    if (state.lpBlocks.length === oldCount) {
      var prevStill = Math.min(prev, state.lpBlocks.length - 1);
      if (idx >= state.lpBlocks.length) idx = state.lpBlocks.length - 1;
      // 先同步拆掉上一块 textarea（防止孤立）
      var prevEl = getBlockEl(prevStill);
      state.activeBlockIdx = -1;
      if (prevEl) restoreBlockView(prevStill);
      activateBlockInPlace(idx, opts);
      state.lpActivating = false;
      setEditStatus("编辑块 " + (idx + 1) + " / " + state.lpBlocks.length, "");
      return;
    }
    // 结构变化：缓冲已 flush+resplit，拆 DOM 时禁止把旧全文写回新块
    teardownActiveTextarea({ skipWrite: true });
    if (idx >= state.lpBlocks.length) idx = state.lpBlocks.length - 1;
    state.activeBlockIdx = -1;
    state.lpActivating = false;
    runLiveRender({ afterActivate: idx });
    return;
  }

  // 首次激活：就地替换当前块
  if (!activateBlockInPlace(idx, opts)) {
    state.activeBlockIdx = -1;
    runLiveRender({ afterActivate: idx });
  }
  state.lpActivating = false;
  setEditStatus("编辑块 " + (idx + 1) + " / " + state.lpBlocks.length, "");
}

// 单块预览失败：退回纯文本（可从「源码」继续编辑）并提示。
function showPreviewFailure(idx) {
  var el = getBlockEl(idx);
  if (el && state.activeBlockIdx !== idx) fillBlockView(el, idx);
  setEditStatus("预览失败，已显示原文", "warn");
}

function refreshSingleBlockView(idx) {
  if (idx < 0 || idx >= state.lpBlocks.length) return;
  var a = api();
  if (!a || !a.preview_markdown) return;
  var text = (state.lpBlocks[idx] && state.lpBlocks[idx].text) || "";
  const element = getBlockEl(idx);
  var expected = text;
  a.preview_markdown(withReferenceDefinitions(text, state.lpBlocks, idx) || "\n", state.editPath || state.currentPath).then(function (p) {
    if (!state.editMode || element !== getBlockEl(idx)) return;
    if (!p || p.ok === false) {
      showPreviewFailure(idx);
      return;
    }
    // 源码在请求期间又改了则丢弃
    if ((state.lpBlocks[idx] && state.lpBlocks[idx].text) !== expected) return;
    state.lastHtmlParts[idx] = p.content || "";
    state.lastHtmlTexts[idx] = expected;
    if (state.activeBlockIdx === idx) return;
    var el = getBlockEl(idx);
    if (el) fillBlockView(el, idx);
  }).catch(function () {
    if (!state.editMode || element !== getBlockEl(idx)) return;
    showPreviewFailure(idx);
  });
}

export function deactivateBlock() {
  if (state.activeBlockIdx < 0) {
    teardownActiveTextarea();
    return;
  }
  var ta = getActiveTextarea();
  if (ta && (ta._lpComposing || ta.isComposing)) return;
  invalidateLiveRender();
  var idx = state.activeBlockIdx;
  var wasHeading = isHeadingBlock(state.lpBlocks[idx]?.initialText);
  flushActiveBlock();
  var full = bufferText();
  var oldCount = state.lpBlocks.length;
  state.lpBlocks = splitMarkdownBlocks(full);
  state.activeBlockIdx = -1;

  // 块数不变：就地恢复该块最终渲染，避免整页跳动
  if (state.lpBlocks.length === oldCount && getBlockEl(idx)) {
    var anchor = captureLiveScrollAnchor();
    restoreBlockView(idx);
    restoreLiveScrollAnchor(anchor);
    // 单块刷新拿不到整篇 TOC；只有标题变动才值得整篇渲染一次来更新目录。
    var a = api();
    if (a && a.preview_markdown && (wasHeading || isHeadingBlock(state.lpBlocks[idx].text))) {
      a.preview_markdown(bufferText(), state.editPath || state.currentPath).then(function (p2) {
        if (!state.editMode) return;
        if (state.toc && p2 && p2.toc != null) state.toc.innerHTML = p2.toc || "";
      }).catch(function () {});
    }
    return;
  }
  // 已 re-split：只拆 DOM，不要把旧 ta 全文写回
  teardownActiveTextarea({ skipWrite: true });
  runLiveRender({});
}

function applyLivePayload(p, opts) {
  opts = opts || {};
  if (!state.editMode || !state.editorLive) return;
  if (!p) return;
  if (p.ok === false && !p.content) {
    // 不销毁激活中的 textarea：只提示状态
    if (abortEnter(p.error || "渲染失败")) return;
    setEditStatus(p.error || "渲染失败", "warn");
    return;
  }
  // 没有标记（单块文档）时 splitPreviewHtml 本就退化成 [html]，不需要单独分支。
  var parts = splitPreviewHtml(p.content || "", state.lpBlocks.length);
  mountLiveDom(parts, { keepActive: !!opts.keepActive, texts: opts.texts });
}

function captureWantActiveSelection(wantActive) {
  if (wantActive < 0) return null;
  var ta = getActiveTextarea();
  if (ta && wantActive === state.activeBlockIdx) {
    return { start: ta.selectionStart, end: ta.selectionEnd, text: ta.value };
  }
  return null;
}

function restoreSavedSelection(savedSel) {
  if (!savedSel || !getActiveTextarea()) return;
  var t2 = getActiveTextarea();
  if (t2.value === savedSel.text) {
    // textarea 对越界的 selectionStart/End 会自动夹取到合法范围，不会抛异常。
    t2.selectionStart = savedSel.start;
    t2.selectionEnd = savedSel.end;
  }
  focusNoScroll(t2);
}

// preview_markdown 返回失败（如语法解析异常）：不整页 wipe，尽量就地保留激活块。
function applyRenderFailure(p, afterIdx) {
  if (abortEnter(p.error || "渲染失败")) return;
  setEditStatus(p.error || "渲染失败", "warn");
  if (afterIdx < 0 || afterIdx >= state.lpBlocks.length) return;
  state.activeBlockIdx = -1;
  if (getBlockEl(afterIdx)) activateBlockInPlace(afterIdx, {});
  else {
    state.activeBlockIdx = afterIdx;
    mountLiveDom(state.lastHtmlParts, { keepActive: true });
  }
}

function applyRenderSuccess(p, ctx) {
  // 块标记是 HTML 注释，不影响标题结构，这次带标记渲染的 toc 与不带标记的完全
  // 一致，直接用它，不必再整篇多请求一次 preview_markdown。
  if (ctx.afterIdx >= 0 && ctx.afterIdx < state.lpBlocks.length && state.activeBlockIdx < 0) {
    applyLivePayload(p, { keepActive: false, texts: ctx.texts });
    // 先恢复滚动，再就地激活（activate 自带锚点，勿再用旧 preAnchor 覆盖）
    if (ctx.preAnchor) restoreLiveScrollAnchor(ctx.preAnchor);
    activateBlockInPlace(ctx.afterIdx, {});
    restoreSavedSelection(ctx.savedSel);
  } else {
    applyLivePayload(p, { keepActive: false, texts: ctx.texts });
    if (ctx.preAnchor) restoreLiveScrollAnchor(ctx.preAnchor);
    requestAnimationFrame(function () {
      if (ctx.preAnchor) restoreLiveScrollAnchor(ctx.preAnchor);
    });
  }
  if (state.toc && p && p.toc != null) state.toc.innerHTML = p.toc || "";
  if (p && p.ok === false) setEditStatus(p.error || "渲染失败", "warn");
  else if (ctx.fromButton) setEditStatus("已刷新渲染", "ok");
}

export function runLiveRender(opts) {
  opts = opts || {};
  if (!state.editMode) return;
  var a = api();
  if (!a || !a.preview_markdown) return;
  flushActiveBlock();

  var wantActive = (opts.afterActivate != null)
    ? opts.afterActivate
    : (opts.keepActive ? state.activeBlockIdx : -1);
  if (wantActive != null && wantActive < 0) wantActive = -1;
  var savedSel = captureWantActiveSelection(wantActive);

  // 全页重渲前记住锚点；首次渲染时编辑区还没显示，滚动位置由 mountLiveDom 取自阅读区
  var preAnchor = editSurfaceShown() ? captureLiveScrollAnchor() : null;

  state.lpBlocks = splitMarkdownBlocks(bufferText());
  if (wantActive >= state.lpBlocks.length) wantActive = -1;
  state.activeBlockIdx = -1;

  var marked = bufferWithMarkers(state.lpBlocks);
  var seq = ++state.editPreviewSeq;
  var ctx = {
    afterIdx: wantActive, preAnchor: preAnchor, savedSel: savedSel, fromButton: opts.fromButton,
    texts: state.lpBlocks.map(function (block) { return block.text; }),
  };
  a.preview_markdown(marked, state.editPath || state.currentPath).then(function (p) {
    if (!state.editMode || seq !== state.editPreviewSeq) return;
    if (p && p.ok === false && !p.content) { applyRenderFailure(p, ctx.afterIdx); return; }
    applyRenderSuccess(p, ctx);
  }).catch(function () {
    if (!state.editMode || seq !== state.editPreviewSeq) return;
    if (abortEnter("渲染请求失败")) return;
    setEditStatus("渲染请求失败", "warn");
  });
}

// "刷新渲染"：flush + 拆掉激活块 + 全页重渲，供刷新按钮 / 全局快捷键 / 测试钩子共用。
export function refreshLiveRender() {
  flushActiveBlock();
  teardownActiveTextarea();
  state.activeBlockIdx = -1;
  runLiveRender({ fromButton: true });
}
