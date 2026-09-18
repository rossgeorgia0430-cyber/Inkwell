// 文档间跳转：历史栈（后退/前进）、正文 .md 链接拦截、打开文件、热重载入口、首屏。
import { state } from "./state.js";
import { $, api, toast, samePath, confirmDiscard, idSelector } from "./util.js";
import { setDocTitle } from "./chrome.js";
import { initContent, lockSpy, scrollToHeading } from "./reader.js";
// editor.js 反过来也从本模块导入 renderInto/onContentLinkClick（保存后重渲染、
// 正文点链接可能命中编辑态）；两边都只在事件回调里互相调用，不在模块顶层求值
// 时调用，循环导入在这种用法下是安全的，见 edit/live.js 顶部注释。
import { exitEditMode } from "./edit/editor.js";

function beginNavigation() { state.navGeneration += 1; return state.navGeneration; }
function isCurrentNavigation(generation) { return generation === state.navGeneration; }

export function renderInto(p) {
  if (!p) return false;
  if (p.ok === false && !p.content) return false;
  state.content.innerHTML = p.content || "";
  if (state.toc) state.toc.innerHTML = p.toc || "";
  state.currentPath = p.path || state.currentPath;
  setDocTitle(p.title);
  initContent();
  var a = api();
  if (p.path && a && a.activate_path) a.activate_path(p.path);
  return true;
}

function scrollAfterLoad(anchor, restoreScroll) {
  if (anchor) {
    var h = document.getElementById(anchor);
    if (h) { h.scrollIntoView({ block: "start" }); return; }
  }
  state.main.scrollTop = (typeof restoreScroll === "number") ? restoreScroll : 0;
}

function updateNavButtons() {
  var b = $("navBack"), f = $("navForward");
  if (b) b.disabled = state.navIndex <= 0;
  if (f) f.disabled = state.navIndex >= state.navHistory.length - 1;
}

function navSaveScroll() {
  // 快速连点前进/后退时 navIndex 可能已指向待加载页，不要把旧页滚动量写错条目。
  if (state.navIndex >= 0 && state.navHistory[state.navIndex]
      && samePath(state.navHistory[state.navIndex].path, state.currentPath)) {
    state.navHistory[state.navIndex].scrollTop = state.main.scrollTop;
  }
}

// 前进式跳转（点击链接 / 打开新文件）：截断 forward 分支，压入新条目
export function navTo(p, anchor) {
  if (state.editMode) {
    if (!confirmDiscard("有未保存的修改，切换文档将丢失修改。继续？")) return;
    exitEditMode({ force: true, skipReload: true });
  }
  navSaveScroll();
  state.navHistory = state.navHistory.slice(0, state.navIndex + 1);
  state.navHistory.push({ path: p.path, anchor: anchor || "", scrollTop: 0 });
  state.navIndex = state.navHistory.length - 1;
  if (renderInto(p)) scrollAfterLoad(anchor, 0);
  updateNavButtons();
}

export function navGo(delta) {
  var target = state.navIndex + delta;
  if (target < 0 || target >= state.navHistory.length) return;
  if (state.editMode) {
    if (!confirmDiscard("有未保存的修改，切换文档将丢失修改。继续？")) return;
    exitEditMode({ force: true, skipReload: true });
  }
  var a = api(); if (!a || !a.render_path) return;
  navSaveScroll();
  state.navIndex = target;
  var e = state.navHistory[state.navIndex];
  var generation = beginNavigation();
  a.render_path(e.path).then(function (p) {
    if (!isCurrentNavigation(generation)) return;
    if (p && p.ok === false) toast("文件可能已被移动或删除");
    if (renderInto(p)) scrollAfterLoad(null, e.scrollTop);
    updateNavButtons();
  });
}
export function navBack() { navGo(-1); }
export function navForward() { navGo(1); }

export function navigateToMd(href) {
  var a = api(); if (!a || !a.open_md_link) return;
  var generation = beginNavigation();
  a.open_md_link(href, state.currentPath).then(function (p) {
    if (!isCurrentNavigation(generation)) return;
    if (!p) return;
    if (p.samedoc) { if (p.anchor) { lockSpy(); scrollToHeading(p.anchor); } return; }
    if (p.ok === false) { toast(p.error || ("无法打开 " + href)); return; }
    navTo(p, p.anchor);
  });
}

// 含协议(http/mailto/...) 视为外链；但要排除 Windows 盘符 C:\ 这种"伪协议"
function isExternalUrl(h) { return /^[a-z][a-z0-9+.\-]*:/i.test(h) && !/^[a-z]:[\\/]/i.test(h); }

export function onContentLinkClick(e) {
  var a = e.target.closest("a");
  if (!a) return;
  var inContent = state.content && state.content.contains(a);
  var inLive = state.editorLive && state.editorLive.contains(a);
  if (!inContent && !inLive) return;
  var href = a.getAttribute("href");
  if (href == null || href === "") return;
  if (href.charAt(0) === "#") {                    // 同页锚点
    e.preventDefault();
    var id = decodeURIComponent(href.slice(1));
    // live 编辑区内锚点滚 live 容器；正文滚主滚动区
    if (state.editMode && inLive) {
      var target = state.editorLive.querySelector(idSelector(id));
      if (target && state.editorLive.contains(target)) target.scrollIntoView({ block: "start" });
      return;
    }
    lockSpy(); scrollToHeading(id); return;
  }
  e.preventDefault();                              // 其余一律拦截，避免 webview 整页跳走
  if (isExternalUrl(href)) {                       // http/mailto/tel → 系统浏览器
    var ax = api(); if (ax && ax.open_external) ax.open_external(href); return;
  }
  var clean = href.split("#")[0];
  if (/\.(md|markdown|mdown|mkd)$/i.test(clean)) {
    if (state.editMode && !confirmDiscard("有未保存的修改，跳转将丢失修改。继续？")) return;
    navigateToMd(href); return;
  }
  var ay = api(); if (ay && ay.open_external) ay.open_external(href);              // 其它本地文件 → 系统默认程序
}

export function openFileDialog() {
  if (state.editMode && !confirmDiscard("有未保存的修改，打开其他文件将丢失修改。继续？")) return;
  var a = api(); if (!a) return;
  var generation = beginNavigation();
  var wasEdit = state.editMode;
  a.open_dialog().then(function (p) {
    if (!isCurrentNavigation(generation)) return;
    if (p && p.error) toast(p.error);
    if (p && p.ok) {
      // 仅在真正打开成功后再退出编辑并跳转
      if (wasEdit && state.editMode) exitEditMode({ force: true, skipReload: true });
      navTo(p, "");
    }
    // 取消对话框：保持编辑会话
  });
}

// 文件变更自动刷新（同一文档）：保持滚动，不改动历史
export function applyPayload(p) {
  if (!p || p.cancelled) return;
  if (p.ok === false && !p.content) return;
  // 编辑模式中绝不接受热重载，防止覆盖源码缓冲。
  if (state.editMode) return;
  // watcher 可能在换页期间送达旧文档 payload，绝不允许它覆盖当前页。
  if (!samePath(p.path, state.currentPath)) return;
  var keep = state.main.scrollTop;
  renderInto(p);
  state.main.scrollTop = keep;
}

// 启动首帧先显示轻量外壳，后台渲染完成后用此入口装载首篇文档。
// 与文件监视热重载不同，这里不要求 p.path 已等于 currentPath，错误页也必须可显示。
export function applyInitialPayload(p) {
  if (!p || p.cancelled || (p.ok === false && !p.content)) return;
  var bootPath = (window.__BOOT__ && window.__BOOT__.path) || "";
  // 用户可能在后台首篇渲染结束前就打开了另一篇文档；旧启动结果不能覆盖新页面。
  if ((bootPath && state.currentPath && !samePath(state.currentPath, bootPath)) || (!bootPath && state.currentPath)) return;
  if (p.path) {
    state.currentPath = p.path;
    state.navHistory = [{ path: p.path, anchor: "", scrollTop: 0 }];
    state.navIndex = 0;
  }
  if (renderInto(p)) {
    state.main.scrollTop = 0;
    updateNavButtons();
  }
}

// Finder / Open With：切到那篇文档。不能走 applyPayload（热重载要求 path
// 已是当前文件），也不能走 applyInitialPayload（欢迎页之后会被丢掉）。
export function openFromFinder(p) {
  if (!p || p.cancelled || (p.ok === false && !p.content)) return;
  navTo(p, "");
}

// boot 时若 pull_initial 已就绪就直接调用，否则等 pywebviewready 事件；
// 用 initialPulled 保证只调用一次，不再用定时器重试。
let initialPulled = false;
export function pullInitial() {
  if (initialPulled) return;
  var a = api();
  if (!a || !a.pull_initial) return;
  initialPulled = true;
  Promise.resolve(a.pull_initial()).then(applyInitialPayload);
}

export function bindNavEvents() {
  $("navBack").addEventListener("click", navBack);
  $("navForward").addEventListener("click", navForward);
  $("openBtn").addEventListener("click", openFileDialog);
  updateNavButtons();
}
