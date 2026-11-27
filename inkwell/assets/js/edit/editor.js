// 编辑会话：进入/退出/保存、插入图片/代码块/公式/流程图、删图、工具栏绑定。
import { state } from "../state.js";
import { $, api, toast, samePath, baseName, confirmDiscard } from "../util.js";
import {
  imageViewerIsOpen, closeImageViewer, openImageViewer, onContentImageClick, selectImage, clearSelectedImage,
} from "../viewer.js";
import { clearVarHighlights, initContent, closeSearch, onContentActionClick } from "../reader.js";
import { onContentDiagramClick } from "../mermaid.js";
import { renderInto, onContentLinkClick } from "../nav.js";
import { setDocTitle } from "../chrome.js";
import { findImageAt, findImages, textWithoutImage, splitMarkdownBlocks, createEmbedTable } from "./blocks.js";
import {
  getEditText, getActiveTextarea, flushActiveBlock, deactivateBlock,
  activateBlock, runLiveRender, refreshLiveRender,
  setEditStatus, setDirty, updateDirtyUI, markDirtyFromBuffer,
  syncBufferFromBlocks, autosizeTextarea, focusNoScroll, editSurfaceShown,
  collapseForEdit, readSource, refreshSingleBlockView,
} from "./live.js";

function pauseWatcher(paused) {
  var a = api();
  if (a && a.set_watch_paused) a.set_watch_paused(!!paused);
}

export function enterEditMode() {
  if (state.editMode) return;
  if (!state.currentPath) {
    toast("请先打开一个 Markdown 文件再编辑");
    return;
  }
  var a = api();
  if (!a || !a.get_source) {
    toast("编辑接口不可用");
    return;
  }
  if (imageViewerIsOpen()) closeImageViewer();
  closeSearch();
  clearVarHighlights();
  clearSelectedImage();
  pauseWatcher(true);

  var gen = ++state.editEnterGen;
  var pathAtRequest = state.currentPath;
  a.get_source(pathAtRequest).then(function (res) {
    if (gen !== state.editEnterGen) {
      // 进入被取消：若仍未处于编辑态则恢复监视
      if (!state.editMode) pauseWatcher(false);
      return;
    }
    if (!samePath(state.currentPath, pathAtRequest)) {
      pauseWatcher(false);
      return;
    }
    if (state.editMode) return;
    if (!res || !res.ok) {
      pauseWatcher(false);
      toast((res && res.error) || "无法读取源文件");
      return;
    }
    // editorLive/editorLiveWrap/editorPane/editorStatus 是页面骨架里的静态元素，
    // bindEditorUI() 已在 boot 时取好；这里能跑到就说明 boot 已完成，不必重取。
    state.editMode = true;
    state.editPath = res.path || state.currentPath;
    state.editMtimeNs = res.mtime_ns != null ? String(res.mtime_ns) : null;
    var raw = res.text || "";
    state.lpBlocks = splitMarkdownBlocks(raw);
    state.editEmbeds = createEmbedTable();
    state.editBaseline = raw;
    state.editorText = state.editBaseline;
    state.activeBlockIdx = -1;
    state.lastHtmlParts = [];
    state.lastHtmlTexts = [];
    state.editSaved = false;
    setDirty(false);
    updateDirtyUI();
    setEditStatus("编辑中 · 点击段落即可修改 · " + (res.title || baseName(state.editPath)), "ok");
    runLiveRender({});
  }).catch(function () {
    if (gen !== state.editEnterGen) {
      if (!state.editMode) pauseWatcher(false);
      return;
    }
    pauseWatcher(false);
    toast("读取源文件失败");
  });
}

export function exitEditMode(opts) {
  opts = opts || {};
  if (!state.editMode) {
    // 取消进行中的 enter，并确保监视恢复
    state.editEnterGen += 1;
    pauseWatcher(false);
    return;
  }
  if (!opts.force && !confirmDiscard("有未保存的修改，确定放弃并退出编辑？")) return;
  state.editEnterGen += 1;
  state.editPreviewSeq += 1;
  state.editSaveGen += 1;
  flushActiveBlock();
  // 编辑区还没显示出来（首次渲染未完成）时，阅读区没动过，不用还原滚动
  var exitScroll = editSurfaceShown() && state.editorLiveWrap ? state.editorLiveWrap.scrollTop : null;
  // 没保存过也没有被丢弃的修改时，#content 与磁盘一致，不必重新渲染
  var needReload = state.editDirty || state.editSaved;
  state.editMode = false;
  setDirty(false);
  state.editBaseline = "";
  state.editMtimeNs = null;
  state.editPath = null;
  state.lpBlocks = [];
  state.editEmbeds = createEmbedTable();
  state.activeBlockIdx = -1;
  state.lastHtmlParts = [];
  state.lastHtmlTexts = [];
  document.body.classList.remove("edit-mode");
  if (state.editorPane) state.editorPane.hidden = true;
  state.editorText = "";
  if (state.editorLive) state.editorLive.innerHTML = "";
  pauseWatcher(false);
  updateDirtyUI();
  setEditStatus("");
  // 阅读区在编辑期间是隐藏的，scrollTop 已被夹成 0；切回后立刻写回，避免先显示在顶部
  if (exitScroll != null && state.main) state.main.scrollTop = exitScroll;
  if (needReload && !opts.skipReload && state.currentPath) {
    var a = api();
    if (a && a.render_path) {
      var exitGen = state.editEnterGen;
      a.render_path(state.currentPath).then(function (p) {
        if (state.editMode || exitGen !== state.editEnterGen) return;
        if (p) {
          renderInto(p);
          if (exitScroll != null && state.main) state.main.scrollTop = exitScroll;
        }
      });
    }
  }
}

export function toggleEditMode() {
  if (state.editMode) exitEditMode();
  else enterEditMode();
}

export function saveEdit(opts) {
  opts = opts || {};
  if (!state.editMode || state.editSaving) return Promise.resolve(null);
  var a = api();
  if (!a || !a.save_document) {
    toast("保存接口不可用");
    return Promise.resolve(null);
  }
  flushActiveBlock();
  state.editSaving = true;
  var saveGen = ++state.editSaveGen;
  setEditStatus("保存中…");
  // 磁盘快照：finishSave 的 baseline 必须与此一致
  var text = getEditText();
  var path = state.editPath || state.currentPath;
  function unlock() { state.editSaving = false; }
  return a.save_document(text, path, state.editMtimeNs).then(function (p) {
    if (saveGen !== state.editSaveGen) { unlock(); return null; }
    if (!p) { unlock(); setEditStatus("保存失败", "warn"); return null; }
    if (p.conflict) {
      var force = window.confirm((p.error || "文件冲突") + "\n\n是否强制覆盖？");
      if (!force) {
        unlock();
        setEditStatus("已取消（磁盘文件已变更）", "warn");
        return p;
      }
      return a.save_document(text, path, null).then(function (p2) {
        if (saveGen !== state.editSaveGen) { unlock(); return null; }
        unlock();
        return finishSave(p2, opts, saveGen, text);
      }).catch(function () {
        unlock();
        setEditStatus("保存失败", "warn");
        toast("保存失败");
        return null;
      });
    }
    unlock();
    return finishSave(p, opts, saveGen, text);
  }).catch(function () {
    unlock();
    setEditStatus("保存失败", "warn");
    toast("保存失败");
    return null;
  });
}

function finishSave(p, opts, saveGen, savedText) {
  if (saveGen != null && saveGen !== state.editSaveGen) return p;
  if (!p || p.ok === false) {
    setEditStatus((p && p.error) || "保存失败", "warn");
    toast((p && p.error) || "保存失败");
    return p;
  }
  if (!state.editMode) {
    toast("已保存到磁盘");
    return p;
  }
  // baseline = 已落盘内容；保存飞行中的新键入应标 dirty
  state.editBaseline = savedText != null ? savedText : state.editorText;
  state.editMtimeNs = p.mtime_ns != null ? String(p.mtime_ns) : state.editMtimeNs;
  if (p.path) {
    state.editPath = p.path;
    state.currentPath = p.path;
    setDocTitle(p.title || baseName(p.path));
  }
  if (state.content && p.content != null) {
    state.editSaved = true;
    state.content.innerHTML = p.content || "";
    if (state.toc && p.toc != null) state.toc.innerHTML = p.toc || "";
    initContent();
  }
  flushActiveBlock();
  markDirtyFromBuffer();
  if (p.encoding_changed) {
    setEditStatus("已保存（编码已改为 " + p.encoding_changed + "）", "ok");
  } else {
    setEditStatus(state.editDirty ? "已保存（之后还有未保存修改）" : "已保存", "ok");
  }
  toast(state.editDirty ? "已保存（有后续修改未写入）" : "已保存");
  if (opts && opts.exit) {
    if (state.editDirty && !window.confirm("保存后又有修改，仍要退出并丢弃？")) return p;
    exitEditMode({ force: true, skipReload: true });
  }
  return p;
}

// 光标不在源码框里：新块插在选区所在块之后（没有选区就放文末），图片类的插入不进源码框，只把新块滚进可视区。
function insertBlockAfterSelection(snippet, opts) {
  const selection = window.getSelection();
  const anchor = selection?.anchorNode;
  const element = anchor?.nodeType === Node.ELEMENT_NODE ? anchor : anchor?.parentElement;
  const selected = element?.closest("#editorLive .lp-block");
  let newIdx = selected ? Number(selected.dataset.idx) + 1 : state.lpBlocks.length;
  if (state.lpBlocks.length === 1 && !state.lpBlocks[0].text.trim()) {
    state.lpBlocks[0].text = snippet;
    newIdx = 0;
  } else {
    const previous = state.lpBlocks[newIdx - 1];
    const inserted = { text: snippet, after: previous.after || "" };
    previous.after = "";
    state.lpBlocks.splice(newIdx, 0, inserted);
  }
  syncBufferFromBlocks();
  if (opts.image) {
    runLiveRender({ revealBlock: newIdx });
    return;
  }
  runLiveRender({ afterActivate: newIdx });
  // 选中占位文本：等 textarea 挂上后（渲染回调里 mount）
  var tries = 0;
  var pick = function () {
    var t = getActiveTextarea();
    if (!t) {
      if (++tries < 40) setTimeout(pick, 50);
      return;
    }
    if (opts.selectInner) {
      var a = t.value.indexOf(opts.selectInner);
      if (a >= 0) {
        t.selectionStart = a;
        t.selectionEnd = a + opts.selectInner.length;
      }
    }
    focusNoScroll(t);
  };
  setTimeout(pick, 30);
}

// 源码框里的插入：偏移都在折叠后的文本上算，所以片段先折叠。
function insertIntoTextarea(ta, snippet, opts) {
  var start = ta.selectionStart;
  var end = ta.selectionEnd;
  var val = ta.value;
  var before = val.slice(0, start);
  var after = val.slice(end);
  var piece = collapseForEdit(snippet);
  if (opts.block) {
    if (before && !/\n\n$/.test(before) && !/\n$/.test(before)) piece = "\n\n" + piece;
    else if (before && /\n$/.test(before) && !/\n\n$/.test(before)) piece = "\n" + piece;
    if (after && !/^\n/.test(after)) piece = piece + "\n";
  }
  ta.value = before + piece + after;
  var caret = (before + piece).length;
  if (opts.selectInner) {
    var ai = (before + piece).indexOf(opts.selectInner);
    if (ai >= 0) {
      ta.selectionStart = ai;
      ta.selectionEnd = ai + opts.selectInner.length;
    } else {
      ta.selectionStart = ta.selectionEnd = caret;
    }
  } else {
    ta.selectionStart = ta.selectionEnd = caret;
  }
  if (state.activeBlockIdx >= 0) state.lpBlocks[state.activeBlockIdx].text = readSource(ta);
  syncBufferFromBlocks();
  autosizeTextarea(ta);
  ta.focus();
}

export function insertAtCursor(snippet, opts) {
  opts = opts || {};
  if (!state.editMode) return;
  flushActiveBlock();
  var ta = getActiveTextarea();
  if (ta) insertIntoTextarea(ta, snippet, opts);
  else insertBlockAfterSelection(snippet, opts);
}

// 块文本改完后让这一块重新渲染；文本空了就把整块去掉（块序号会变，整页重渲）。
function replaceBlockText(idx, text) {
  if (text.trim() || state.lpBlocks.length === 1) {
    state.lpBlocks[idx].text = text;
    syncBufferFromBlocks();
    refreshSingleBlockView(idx);
    return;
  }
  var gone = state.lpBlocks.splice(idx, 1)[0];
  // 首块带着文档开头的空白和换行风格，末块带着结尾，去掉时交给相邻的块
  if (idx === 0) {
    state.lpBlocks[0].before = gone.before;
    state.lpBlocks[0].newline = gone.newline;
  } else if (idx === state.lpBlocks.length) {
    state.lpBlocks[idx - 1].after = gone.after;
  }
  syncBufferFromBlocks();
  runLiveRender({});
}

// 没有源码框时删除当前选中的图片：按它在块里的出现顺序对应到源码里的那一处。
function deleteSelectedImage() {
  var img = state.selectedImage;
  var blockEl = img && img.isConnected ? img.closest("#editorLive .lp-block") : null;
  if (!blockEl) return false;
  var idx = Number(blockEl.dataset.idx);
  var text = state.lpBlocks[idx].text;
  var hits = findImages(text);
  var rendered = Array.from(blockEl.querySelectorAll(".lp-view img"));
  var order = rendered.indexOf(img);
  if (order < 0 || hits.length !== rendered.length) {
    setEditStatus("这个块里的图片写法无法直接定位，请点「源码」后在源码里删除", "warn");
    toast("请点「源码」，在源码里删除这张图片");
    return true;
  }
  clearSelectedImage();
  replaceBlockText(idx, textWithoutImage(text, hits[order]).text);
  setEditStatus("已删除图片", "ok");
  return true;
}

// Delete / Backspace 删除选中的图片；焦点在输入框或可编辑字段里时留给它们自己处理。
export function deleteSelectedImageOnKey() {
  var focused = document.activeElement;
  if (!state.editMode || imageViewerIsOpen() || !state.selectedImage) return false;
  if (focused && (focused.matches("input, textarea") || focused.isContentEditable)) return false;
  return deleteSelectedImage();
}

export function deleteImageAtCursor() {
  if (!state.editMode) return;
  flushActiveBlock();
  var ta = getActiveTextarea();
  if (!ta) {
    if (deleteSelectedImage()) return;
    setEditStatus("请先点击图片选中它，或点击块的「源码」后把光标放到图片上，再删图", "warn");
    toast("请先选中要删除的图片");
    return;
  }
  var text = ta.value;
  var pos = ta.selectionStart;
  var mid = Math.floor((ta.selectionStart + ta.selectionEnd) / 2);
  var hit = findImageAt(text, pos) || findImageAt(text, mid);
  if (!hit) {
    setEditStatus("当前块里没有 ![…](…) 写法的图片；引用式 ![…][…] 和 <img> 写法请直接在源码里删除", "warn");
    toast("当前块没有可删除的图片，引用式和 <img> 写法请直接在源码里删除");
    return;
  }
  var removed = textWithoutImage(text, hit);
  ta.value = removed.text;
  ta.selectionStart = ta.selectionEnd = removed.caret;
  state.lpBlocks[state.activeBlockIdx].text = readSource(ta);
  syncBufferFromBlocks();
  autosizeTextarea(ta);
  ta.focus();
  setEditStatus("已删除图片", "ok");
}

function insertCodeBlock() {
  insertAtCursor("```\ncode\n```", { block: true, selectInner: "code" });
  setEditStatus("已插入代码块", "ok");
}

function insertMermaidBlock() {
  var sample = "```mermaid\nflowchart LR\n  A[开始] --> B[结束]\n```";
  insertAtCursor(sample, { block: true, selectInner: "A[开始] --> B[结束]" });
  setEditStatus("已插入流程图（Mermaid）", "ok");
}

function insertMathBlock() {
  insertAtCursor("$$\nE = mc^2\n$$", { block: true, selectInner: "E = mc^2" });
  setEditStatus("已插入块级公式", "ok");
}

function insertImage(mode) {
  if (!state.editMode) return;
  var a = api();
  if (!a || !a.pick_image) {
    toast("插图接口不可用");
    return;
  }
  setEditStatus(mode === "embed" ? "选择要内嵌的图片…" : "选择图片…");
  var gen = state.editEnterGen;
  a.pick_image(mode || "file").then(function (res) {
    if (!state.editMode || gen !== state.editEnterGen) return;
    if (!res) return;
    if (res.cancelled) { setEditStatus("已取消", ""); return; }
    if (!res.ok) {
      setEditStatus(res.error || "插入失败", "warn");
      toast(res.error || "插入失败");
      return;
    }
    insertAtCursor(res.markdown, { block: true, image: true });
    setEditStatus(mode === "embed" ? "已内嵌图片" : ("已插入 " + (res.relative || "图片")), "ok");
  }).catch(function () {
    if (!state.editMode) return;
    setEditStatus("插入图片失败", "warn");
  });
}

// 与后端 api.py 的内嵌图片上限一致。
const MAX_EMBED_IMAGE_BYTES = 8 * 1024 * 1024;

function readAsDataUrl(file) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () { resolve(reader.result); };
    reader.onerror = function () { reject(reader.error); };
    reader.readAsDataURL(file);
  });
}

function imageFilesOf(transfer) {
  return transfer ? Array.from(transfer.files || []).filter(function (file) { return file.type.startsWith("image/"); }) : [];
}

// 粘贴和拖入的图片都按「内嵌图」插入，与工具栏的结果一致。
function insertImageFiles(files) {
  if (!files.length) {
    toast("拖入的不是图片文件");
    return;
  }
  if (files.some(function (file) { return file.size > MAX_EMBED_IMAGE_BYTES; })) {
    setEditStatus("图片超过 8 MB，不能内嵌，请改用工具栏的「插图」", "warn");
    toast("图片超过 8 MB，请改用「插图」");
    return;
  }
  setEditStatus("正在读取图片…");
  var gen = state.editEnterGen;
  Promise.all(files.map(readAsDataUrl)).then(function (urls) {
    if (!state.editMode || gen !== state.editEnterGen) return;
    insertAtCursor(urls.map(function (url) { return "![粘贴的图片](" + url + ")"; }).join("\n\n"),
      { block: true, image: true });
    setEditStatus("已插入图片", "ok");
  }).catch(function () {
    setEditStatus("读取图片失败", "warn");
    toast("读取图片失败");
  });
}

function onLivePaste(e) {
  var files = imageFilesOf(e.clipboardData);
  // Word、Excel、飞书复制文字或表格时会同时带一张图片；有文字就留给纯文本粘贴
  if (!files.length || e.clipboardData.getData("text/plain").trim()) return;
  // 捕获阶段拦下：可编辑字段自己的粘贴处理只认纯文本
  e.preventDefault();
  e.stopPropagation();
  insertImageFiles(files);
}

function hasFiles(transfer) {
  return !!transfer && Array.from(transfer.types || []).includes("Files");
}

// 拖入文件时 WebView 默认会整页跳到那个文件，未保存的修改随之丢失；任何时候都不能放行。
export function preventFileNavigation(e) {
  if (hasFiles(e.dataTransfer)) e.preventDefault();
}

export function onFileDrop(e) {
  if (!hasFiles(e.dataTransfer)) return;
  e.preventDefault();
  if (state.editMode) insertImageFiles(imageFilesOf(e.dataTransfer));
}

function onLiveClick(e) {
  if (!state.editMode || !state.editorLive) return;
  const button = e.target.closest(".lp-edit-source");
  if (button) activateBlock(Number(button.closest(".lp-block").dataset.idx));
  if (e.target.closest("[contenteditable] a")) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }
}

export function bindEditorUI() {
  state.editorLive = $("editorLive");
  state.editorLiveWrap = $("editorLiveWrap");
  state.editorPane = $("editorPane");
  state.editorStatus = $("editorStatus");
  var editBtn = $("editBtn");
  if (editBtn) editBtn.addEventListener("click", toggleEditMode);
  if ($("editSaveBtn")) $("editSaveBtn").addEventListener("click", function () { saveEdit(); });
  if ($("editRefreshBtn")) $("editRefreshBtn").addEventListener("click", refreshLiveRender);
  if ($("editInsertImageBtn")) $("editInsertImageBtn").addEventListener("click", function () { insertImage("file"); });
  if ($("editEmbedImageBtn")) $("editEmbedImageBtn").addEventListener("click", function () { insertImage("embed"); });
  // WebKit 里按钮不接受鼠标焦点，按下会让源码框失焦并退出当前块；拦下按下事件，焦点和选区留在原处。
  if ($("editorToolbar")) $("editorToolbar").addEventListener("mousedown", function (e) {
    if (e.target.closest("button")) e.preventDefault();
  });
  if ($("editDeleteImageBtn")) $("editDeleteImageBtn").addEventListener("click", deleteImageAtCursor);
  if ($("editInsertCodeBtn")) $("editInsertCodeBtn").addEventListener("click", insertCodeBlock);
  if ($("editInsertMermaidBtn")) $("editInsertMermaidBtn").addEventListener("click", insertMermaidBlock);
  if ($("editInsertMathBtn")) $("editInsertMathBtn").addEventListener("click", insertMathBlock);
  if ($("editExitBtn")) $("editExitBtn").addEventListener("click", function () { exitEditMode(); });
  if (state.editorLive) {
    // pointerdown 抢在 blur 前标记激活，避免 blur→deactivate 与 click 竞态
    state.editorLive.addEventListener("pointerdown", function (e) {
      if (!state.editMode) return;
      if (e.target.closest && e.target.closest("button, a, textarea, input, img, .image-block, .mermaid-diagram, .mermaid-block")) return;
      var block = e.target.closest && e.target.closest(".lp-block");
      if (block && state.editorLive.contains(block)) {
        state.lpActivating = true;
        setTimeout(function () { state.lpActivating = false; }, 50);
      }
    }, true);
    state.editorLive.addEventListener("focusin", event => {
      if (event.target.isContentEditable && getActiveTextarea()) deactivateBlock();
    });
    state.editorLive.addEventListener("paste", onLivePaste, true);
    state.editorLive.addEventListener("click", onLiveClick);
    state.editorLive.addEventListener("click", onContentActionClick);
    state.editorLive.addEventListener("click", onContentImageClick);
    state.editorLive.addEventListener("dblclick", function (e) {
      var img = e.target.closest && e.target.closest(".image-block img");
      if (img) openImageViewer(img);
    });
    state.editorLive.addEventListener("click", onContentDiagramClick);
    state.editorLive.addEventListener("click", onContentLinkClick);
    state.editorLive.addEventListener("mousedown", function (e) {
      var img = e.target.closest && e.target.closest("img");
      if (img) {
        // 不让浏览器把焦点和光标移进所属的可编辑字段，Delete 才能作用在选中的图片上
        e.preventDefault();
        selectImage(img);
        img.focus({ preventScroll: true });
        setEditStatus("已选中图片：按 Delete 删除，双击放大", "");
      } else if (!(e.target.closest && e.target.closest(".lp-block.is-active"))) clearSelectedImage();
    });
  }
}
