// 入口：boot、事件绑定、全局快捷键、window.__ink 测试钩子。
import { state } from "./state.js";
import { $, api, isMacPlatform, applyShortcutTitles } from "./util.js";
import {
  setTheme, setDocTitle, onResize, setupWindowResize, setupWindowDrag, setupFontZoom,
  setupResizer, toggleSidebar, bindChromeEvents,
} from "./chrome.js";
import {
  initContent, onContentActionClick, clearVarHighlights, runSearch, nextHit,
  openSearch, closeSearch, bindReaderEvents, highlightToken, renderMath,
} from "./reader.js";
import { onCopy, sanitizeForCopy, buildCopyPayload } from "./copy.js";
import {
  onContentImageClick, imageViewerIsOpen, closeImageViewer, openImageViewer,
  fitImageViewer, zoomImageViewer, openSvgViewer, imageToPngBlob, clearSelectedImage,
} from "./viewer.js";
import { onContentDiagramClick, renderMermaidBlock, toggleMermaid, zoomMermaid } from "./mermaid.js";
import {
  navBack, navForward, navigateToMd, openFileDialog, onContentLinkClick,
  applyPayload, applyInitialPayload, openFromFinder, pullInitial, bindNavEvents,
} from "./nav.js";
import { findImageAt, findProtectedRanges, splitMarkdownBlocks, joinMarkdownBlocks } from "./edit/blocks.js";
import { deactivateBlock, activateBlock, refreshLiveRender, getEditText, setEditText } from "./edit/live.js";
import { enterEditMode, exitEditMode, toggleEditMode, saveEdit, bindEditorUI } from "./edit/editor.js";

function bindContentAreaEvents() {
  // 正文（#content）与 Live Preview 编辑区（#editorLive）都承载相同的可交互内容；
  // 编辑区自己的这套绑定在 edit/editor.js 的 bindEditorUI 里。
  state.content.addEventListener("click", onContentActionClick);
  state.content.addEventListener("click", onContentImageClick);
  state.content.addEventListener("click", onContentDiagramClick);
  state.content.addEventListener("click", onContentLinkClick);
  state.content.addEventListener("mousedown", function (e) {
    if (!(e.target.closest && e.target.closest("img"))) clearSelectedImage();
  });
  document.addEventListener("copy", onCopy, true);
}

function handleEditModeShortcut(e, ctrl) {
  if (ctrl && e.shiftKey && (e.key === "p" || e.key === "P")) {
    e.preventDefault();
    refreshLiveRender();
    return true;
  }
  if (e.key === "Escape") {
    if (imageViewerIsOpen()) { closeImageViewer(); return true; }
    e.preventDefault();
    // Obsidian 风格：先退出当前块编辑，再退出编辑模式
    if (state.activeBlockIdx >= 0) deactivateBlock();
    else exitEditMode();
    return true;
  }
  // 编辑态屏蔽阅读向快捷键（搜索 / 目录切换仍允许）
  if (ctrl && (e.key === "f" || e.key === "F")) { e.preventDefault(); return true; }
  return false;
}

function onGlobalKeydown(e) {
  var ctrl = e.ctrlKey || e.metaKey;
  // 编辑器内的 Ctrl+S / Tab 由 textarea 自己处理；此处拦截全局
  if (ctrl && (e.key === "e" || e.key === "E")) {
    e.preventDefault();
    toggleEditMode();
    return;
  }
  if (ctrl && (e.key === "s" || e.key === "S")) {
    if (state.editMode) { e.preventDefault(); saveEdit(); }
    return;
  }
  if (state.editMode && handleEditModeShortcut(e, ctrl)) return;

  if (ctrl && (e.key === "f" || e.key === "F")) { e.preventDefault(); openSearch(); }
  else if (ctrl && (e.key === "b" || e.key === "B")) { e.preventDefault(); toggleSidebar(); }
  else if (ctrl && (e.key === "o" || e.key === "O")) { e.preventDefault(); openFileDialog(); }
  else if (ctrl && e.key === "[") { e.preventDefault(); navBack(); }
  else if (ctrl && e.key === "]") { e.preventDefault(); navForward(); }
  else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); navBack(); }
  else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); navForward(); }
  else if (e.key === "Escape") {
    if (imageViewerIsOpen()) closeImageViewer();
    else if (state.searchBar.classList.contains("open")) closeSearch();
    else clearVarHighlights();
  }
  else if (e.key === "F3") { e.preventDefault(); nextHit(e.shiftKey ? -1 : 1); }
}

function bind() {
  bindChromeEvents();
  bindReaderEvents();
  bindNavEvents();
  bindEditorUI();
  bindContentAreaEvents();
  document.addEventListener("keydown", onGlobalKeydown);
}

function boot() {
  state.content = $("content"); state.main = $("main"); state.app = $("app");
  state.sidebar = $("sidebar"); state.toc = $("toc"); state.dragRegion = $("dragRegion");
  state.docTitle = $("docTitle");
  state.searchBar = $("searchbar"); state.searchInput = $("searchInput"); state.searchCount = $("searchCount");

  var bootData = window.__BOOT__ || {};
  var preferences = bootData.preferences || {};
  var t = preferences.theme || document.documentElement.getAttribute("data-theme") || "light";
  setTheme(t, false);
  setDocTitle(bootData.title || "Inkwell");
  // 用初始文档播种跳转历史
  if (bootData.path) {
    state.currentPath = bootData.path;
    state.navHistory = [{ path: bootData.path, anchor: "", scrollTop: 0 }];
    state.navIndex = 0;
  }

  if (isMacPlatform()) document.documentElement.classList.add("platform-mac");
  applyShortcutTitles();
  bind();
  setupResizer();
  setupWindowResize();
  setupWindowDrag();
  setupFontZoom(preferences.font);
  onResize();
  initContent();

  // KaTeX 以 <script defer> 放在本模块之前加载，defer 脚本与 module 脚本按文档
  // 顺序在解析完成后依次执行，进入这里时 window.katex 必然已经就绪，不需要轮询。

  // pull_initial 已就绪就直接调用；否则等 pywebviewready 事件，只调用一次。
  var bootApi = api();
  if (bootApi && bootApi.pull_initial) {
    pullInitial();
  } else {
    window.addEventListener("pywebviewready", pullInitial);
  }
}

// Python 注入的全局入口
window.__applyPayload = applyPayload;
window.__openFromFinder = openFromFinder;

// 测试钩子（无害；供自动化探针验证净化/渲染/高亮）
window.__ink = {
  sanitize: sanitizeForCopy, render: renderMath,
  highlight: highlightToken, clearHL: clearVarHighlights, runSearch: runSearch,
  copyPayload: buildCopyPayload,
  applyInitial: applyInitialPayload,
  image: {
    toPng: imageToPngBlob, selected: function () { return state.selectedImage; },
    open: openImageViewer, close: closeImageViewer, fit: fitImageViewer,
    zoom: zoomImageViewer, openSvg: openSvgViewer,
    state: function () {
      var stage = state.imageViewer && state.imageViewer.querySelector(".image-viewer-stage");
      var el = state.imageViewer && (state.imageViewerKind === "svg"
        ? state.imageViewerSvg
        : state.imageViewer.querySelector(".image-viewer-image"));
      return { open: imageViewerIsOpen(), scale: state.imageViewerScale, fit: state.imageViewerFitScale,
               kind: state.imageViewerKind, panning: !!state.imagePan,
               width: el ? el.getBoundingClientRect().width : 0,
               height: el ? el.getBoundingClientRect().height : 0,
               stageWidth: stage ? stage.clientWidth : 0, stageHeight: stage ? stage.clientHeight : 0 };
    }
  },
  mermaid: {
    render: renderMermaidBlock,
    toggle: toggleMermaid,
    zoom: zoomMermaid,
    state: function (block) {
      block = block || state.content.querySelector(".mermaid-block");
      if (!block) return null;
      return {
        diagram: block.classList.contains("is-diagram"),
        rendered: !!block.querySelector(".mermaid-diagram svg"),
        source: !!block.querySelector(".codehilite"),
        error: block.getAttribute("data-mermaid-error") || ""
      };
    }
  },
  nav: {
    to: navigateToMd, back: navBack, forward: navForward,
    state: function () {
      return { index: state.navIndex, len: state.navHistory.length, path: state.currentPath,
               title: (state.docTitle && state.docTitle.textContent) || "" };
    }
  },
  edit: {
    enter: enterEditMode, exit: function () { exitEditMode({ force: true }); },
    toggle: toggleEditMode, save: saveEdit,
    findImageAt: findImageAt, findProtectedRanges: findProtectedRanges,
    splitBlocks: splitMarkdownBlocks, joinBlocks: joinMarkdownBlocks,
    activate: activateBlock, deactivate: deactivateBlock,
    refresh: refreshLiveRender,
    state: function () {
      return {
        mode: state.editMode, dirty: state.editDirty, path: state.editPath, mtime: state.editMtimeNs,
        length: getEditText().length,
        baseline: state.editBaseline.length,
        blocks: state.lpBlocks.length,
        active: state.activeBlockIdx
      };
    },
    getText: function () { return getEditText(); },
    setText: function (t) {
      if (!state.editMode) return;
      state.activeBlockIdx = -1;
      setEditText(t, { render: true, activate: -1 });
    }
  }
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
