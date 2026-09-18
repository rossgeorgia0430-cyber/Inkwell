// 编辑会话：进入/退出/保存、插入图片/代码块/公式/流程图、删图、工具栏绑定。
import { state } from "../state.js";
import { $, api, toast, samePath, baseName, confirmDiscard } from "../util.js";
import { imageViewerIsOpen, closeImageViewer, onContentImageClick, selectImage, clearSelectedImage } from "../viewer.js";
import { clearVarHighlights, initContent, closeSearch, onContentActionClick } from "../reader.js";
import { onContentDiagramClick } from "../mermaid.js";
import { renderInto, onContentLinkClick } from "../nav.js";
import { setDocTitle } from "../chrome.js";
import { findImageAt, splitMarkdownBlocks, joinMarkdownBlocks } from "./blocks.js";
import {
  getActiveTextarea, flushActiveBlock, teardownActiveTextarea, deactivateBlock,
  activateBlock, runLiveRender, refreshLiveRender,
  setEditStatus, setDirty, updateDirtyUI, markDirtyFromBuffer,
  syncBufferFromBlocks, autosizeTextarea, focusNoScroll,
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
  var savedMainScroll = state.main ? state.main.scrollTop : 0;
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
    // 规范化 baseline，避免仅因 CRLF/空行归一化就误标 dirty
    var raw = res.text || "";
    state.lpBlocks = splitMarkdownBlocks(raw);
    state.editBaseline = joinMarkdownBlocks(state.lpBlocks);
    state.editorText = state.editBaseline;
    state.activeBlockIdx = -1;
    state.lastHtmlParts = [];
    setDirty(false);
    // 阅读区可能已滚动：编辑态 main 溢出隐藏需归零，但把滚动位置交给 live 容器
    if (state.main) state.main.scrollTop = 0;
    state.pendingEnterScroll = savedMainScroll || 0;
    state.editRestoreScroll = savedMainScroll;
    document.body.classList.add("edit-mode");
    if (state.editorPane) state.editorPane.hidden = false;
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
  state.editMode = false;
  setDirty(false);
  state.editBaseline = "";
  state.editMtimeNs = null;
  state.editPath = null;
  state.lpBlocks = [];
  state.activeBlockIdx = -1;
  state.lastHtmlParts = [];
  document.body.classList.remove("edit-mode");
  if (state.editorPane) state.editorPane.hidden = true;
  state.editorText = "";
  if (state.editorLive) state.editorLive.innerHTML = "";
  pauseWatcher(false);
  updateDirtyUI();
  setEditStatus("");
  var restoreScroll = (typeof state.editRestoreScroll === "number")
    ? state.editRestoreScroll : (state.main ? state.main.scrollTop : 0);
  state.editRestoreScroll = null;
  if (!opts.skipReload && state.currentPath) {
    var a = api();
    if (a && a.render_path) {
      var exitGen = state.editEnterGen;
      a.render_path(state.currentPath).then(function (p) {
        if (state.editMode || exitGen !== state.editEnterGen) return;
        if (p) {
          renderInto(p);
          if (state.main) state.main.scrollTop = restoreScroll;
        }
      });
    }
  } else if (state.main) {
    state.main.scrollTop = restoreScroll;
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
  var text = state.editorText;
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
    state.content.innerHTML = p.content || "";
    if (state.toc && p.toc != null) state.toc.innerHTML = p.toc || "";
    initContent();
  }
  // 拆掉激活态；若用户在保存期间又改了，flush 后与 baseline 比较
  flushActiveBlock();
  teardownActiveTextarea();
  state.activeBlockIdx = -1;
  markDirtyFromBuffer();
  runLiveRender({});
  if (p.path && api() && api().activate_path) api().activate_path(p.path);
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

export function insertAtCursor(snippet, opts) {
  opts = opts || {};
  if (!state.editMode) return;
  flushActiveBlock();

  var ta = getActiveTextarea();
  if (!ta) {
    // 无激活块：作为新块追加；渲染完成后激活
    if (state.lpBlocks.length === 1 && !(state.lpBlocks[0].text || "").replace(/^\s+|\s+$/g, "")) {
      state.lpBlocks[0].text = snippet;
    } else {
      state.lpBlocks.push({ text: snippet });
    }
    syncBufferFromBlocks();
    var newIdx = state.lpBlocks.length - 1;
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
    return;
  }

  var start = ta.selectionStart;
  var end = ta.selectionEnd;
  var val = ta.value;
  var before = val.slice(0, start);
  var after = val.slice(end);
  var piece = snippet;
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
  if (state.activeBlockIdx >= 0) state.lpBlocks[state.activeBlockIdx].text = ta.value;
  syncBufferFromBlocks();
  autosizeTextarea(ta);
  ta.focus();
}

export function deleteImageAtCursor() {
  if (!state.editMode) return;
  flushActiveBlock();
  var ta = getActiveTextarea();
  if (!ta) {
    setEditStatus("请先点击包含图片的块，再删图", "warn");
    toast("请先选中图片所在的段落");
    return;
  }
  var text = ta.value;
  var pos = ta.selectionStart;
  var mid = Math.floor((ta.selectionStart + ta.selectionEnd) / 2);
  var hit = findImageAt(text, pos) || findImageAt(text, mid);
  if (!hit) {
    setEditStatus("当前块未找到图片语法 ![…](…)", "warn");
    toast("当前块没有可删除的图片");
    return;
  }
  var lineStart = text.lastIndexOf("\n", hit.start - 1) + 1;
  var lineEnd = text.indexOf("\n", hit.end);
  if (lineEnd < 0) lineEnd = text.length;
  var line = text.slice(lineStart, lineEnd);
  var onlyImage = line.replace(/^\s+|\s+$/g, "") === hit.markdown;
  var delStart = onlyImage ? lineStart : hit.start;
  var delEnd = onlyImage ? (lineEnd < text.length ? lineEnd + 1 : lineEnd) : hit.end;
  if (onlyImage && delStart >= 2 && text.slice(delStart - 2, delStart) === "\n\n") {
    delStart -= 1;
  }
  var next = text.slice(0, delStart) + text.slice(delEnd);
  ta.value = next;
  ta.selectionStart = ta.selectionEnd = delStart;
  state.lpBlocks[state.activeBlockIdx].text = next;
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
    insertAtCursor(res.markdown, { block: true });
    setEditStatus(mode === "embed" ? "已内嵌图片" : ("已插入 " + (res.relative || "图片")), "ok");
  }).catch(function () {
    if (!state.editMode) return;
    setEditStatus("插入图片失败", "warn");
  });
}

function onLiveClick(e) {
  if (!state.editMode || !state.editorLive) return;
  // 交互控件 + 图片/图示本体：交给后续灯箱/复制处理器，不进入块编辑
  if (e.target.closest && e.target.closest(
    "button, a, textarea, input, img, .image-block, .mermaid-diagram, .mermaid-block, " +
    ".code-copy-float, .math-copy-btn, [data-mermaid-action], [data-copy-action]"
  )) {
    return;
  }
  var block = e.target.closest && e.target.closest(".lp-block");
  if (!block || !state.editorLive.contains(block)) {
    if (state.activeBlockIdx >= 0 && (e.target === state.editorLive || e.target === state.editorLiveWrap)) {
      deactivateBlock();
    }
    return;
  }
  var idx = parseInt(block.dataset.idx, 10);
  if (isNaN(idx)) return;
  if (idx === state.activeBlockIdx) {
    // 激活索引存在但 textarea 丢失时允许修复
    if (!getActiveTextarea()) activateBlock(idx);
    return;
  }
  e.preventDefault();
  activateBlock(idx);
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
    state.editorLive.addEventListener("click", onLiveClick);
    state.editorLive.addEventListener("click", onContentActionClick);
    state.editorLive.addEventListener("click", onContentImageClick);
    state.editorLive.addEventListener("click", onContentDiagramClick);
    state.editorLive.addEventListener("click", onContentLinkClick);
    state.editorLive.addEventListener("mousedown", function (e) {
      if (e.target.closest && e.target.closest("img")) selectImage(e.target.closest("img"));
      else if (!(e.target.closest && e.target.closest(".lp-block.is-active"))) clearSelectedImage();
    });
  }
}
