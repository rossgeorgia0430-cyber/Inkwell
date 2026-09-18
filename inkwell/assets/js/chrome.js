// 窗口外壳：侧栏/抽屉、主题、无边框窗口拖动与八向缩放、字号缩放、侧栏拖宽。
import { state } from "./state.js";
import { $, api, savePreference, isMacPlatform, confirmDiscard } from "./util.js";
import { refreshMermaidDiagrams } from "./mermaid.js";
import { imageViewerIsOpen, zoomImageViewer, zoomImageViewerByWheel, fitImageViewer } from "./viewer.js";

export function closeDrawer() { state.app.classList.remove("sidebar-open"); }

export function toggleSidebar() {
  if (state.app.classList.contains("drawer")) {
    state.app.classList.toggle("sidebar-open");
  } else {
    state.app.classList.toggle("sidebar-hidden");
  }
}

export function onResize() {
  var drawer = window.innerWidth < 760;
  state.app.classList.toggle("drawer", drawer);
  // 抽屉态用 .sidebar-open 控制侧栏；.sidebar-hidden 只属于宽屏态，二者互斥，
  // 否则 .sidebar-hidden 的 display:none 会让抽屉永远拉不出来。
  if (drawer) state.app.classList.remove("sidebar-hidden");
  else state.app.classList.remove("sidebar-open");
  var a = api();
  if (a && a.win_is_maximized) {
    a.win_is_maximized().then(function (maximized) {
      document.documentElement.classList.toggle("window-maximized", !!maximized);
    });
  }
}

export function setTheme(t, persist) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("inkwell-theme", t); } catch (e) { /* 私密模式等场景禁用本地存储，主题仍按内存态生效 */ }
  if (persist !== false) savePreference("theme", t);
  var link = $("pygments-style");
  if (link) link.href = "/assets/pygments-" + (t === "dark" ? "dark" : "light") + ".css";
  refreshMermaidDiagrams();
}
export function toggleTheme() {
  var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  setTheme(cur === "dark" ? "light" : "dark");
}

export function setDocTitle(title) {
  if (state.docTitle) state.docTitle.textContent = title || "";
  document.title = title ? (title + " — Inkwell") : "Inkwell";
}

// ============================================================
// 原生窗口行为：8 向缩放 + 标题栏拖动（Aero Snap 并列 / 拖回还原）
// 通过 js_api 调 Win32 ReleaseCapture()+SendMessage(WM_NCLBUTTONDOWN,...)
// ============================================================
const RESIZE_HANDLE_EDGES = {
  n: "top", s: "bottom", w: "left", e: "right",
  nw: "topleft", ne: "topright", sw: "bottomleft", se: "bottomright"
};

export function setupWindowResize() {
  if (isMacPlatform()) return;
  Object.keys(RESIZE_HANDLE_EDGES).forEach(function (k) {
    var h = document.createElement("div");
    h.className = "resize-handle rh-" + k;
    h.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      var a = api(); if (a && a.win_native_resize) a.win_native_resize(RESIZE_HANDLE_EDGES[k]);
    });
    document.body.appendChild(h);
  });
}

export function setupWindowDrag() {
  var bar = document.querySelector(".titlebar");
  if (!bar) return;
  if (isMacPlatform() && state.dragRegion) {
    state.dragRegion.classList.add("pywebview-drag-region");
  }
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
const FONT_MIN = 10, FONT_MAX = 26, FONT_DEFAULT = 15;
function readerFont() {
  var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--reader-font"));
  return isNaN(v) ? FONT_DEFAULT : v;
}
function setReaderFont(px, persist) {
  px = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px * 2) / 2));
  document.documentElement.style.setProperty("--reader-font", px + "px");
  try { localStorage.setItem("inkwell-font", px); } catch (e) { /* 同上：本地存储不可用时仅放弃持久化 */ }
  if (persist !== false) savePreference("font", px);
}
export function setupFontZoom(preferredFont) {
  var saved = parseFloat(preferredFont);
  if (isNaN(saved)) saved = parseFloat(localStorage.getItem("inkwell-font"));
  if (!isNaN(saved)) setReaderFont(saved, false);
  // Ctrl/⌘+滚轮：放大/缩小正文（拦截 WebView 的整页缩放）
  window.addEventListener("wheel", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    if (imageViewerIsOpen()) {
      zoomImageViewerByWheel(e.deltaY, e.clientX, e.clientY);
      return;
    }
    setReaderFont(readerFont() + (e.deltaY < 0 ? 0.5 : -0.5));
  }, { passive: false });
  // Ctrl + 加/减/0：键盘缩放与重置
  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (imageViewerIsOpen()) {
      if (e.key === "=" || e.key === "+") { e.preventDefault(); zoomImageViewer(1); }
      else if (e.key === "-") { e.preventDefault(); zoomImageViewer(-1); }
      else if (e.key === "0") { e.preventDefault(); fitImageViewer(); }
      return;
    }
    if (e.key === "=" || e.key === "+") { e.preventDefault(); setReaderFont(readerFont() + 0.5); }
    else if (e.key === "-") { e.preventDefault(); setReaderFont(readerFont() - 0.5); }
    else if (e.key === "0") { e.preventDefault(); setReaderFont(FONT_DEFAULT); }
  });
}

// 侧栏拖拽改变宽度
export function setupResizer() {
  var rz = document.createElement("div");
  rz.className = "sidebar-resizer";
  state.app.appendChild(rz);
  var dragging = false;
  rz.addEventListener("mousedown", function (e) { dragging = true; e.preventDefault(); document.body.style.cursor = "col-resize"; });
  window.addEventListener("mousemove", function (e) {
    if (!dragging) return;
    var w = Math.min(460, Math.max(180, e.clientX));
    document.documentElement.style.setProperty("--sidebar-w", w + "px");
  });
  window.addEventListener("mouseup", function () { dragging = false; document.body.style.cursor = ""; });
}

export function bindChromeEvents() {
  $("sidebarToggle").addEventListener("click", toggleSidebar);
  $("themeBtn").addEventListener("click", toggleTheme);
  $("winMin").addEventListener("click", function () { var a = api(); if (a) a.win_minimize(); });
  $("winMax").addEventListener("click", function () { var a = api(); if (a && a.win_toggle_maximize) a.win_toggle_maximize(); });
  $("winClose").addEventListener("click", function () {
    if (state.editMode && !confirmDiscard("有未保存的修改，确定关闭？")) return;
    var a = api(); if (a) a.win_close();
  });
  $("scrim").addEventListener("click", closeDrawer);
  window.addEventListener("resize", function () {
    onResize();
    if (imageViewerIsOpen() && !state.imageViewerUserAdjusted) requestAnimationFrame(fitImageViewer);
  });
}
