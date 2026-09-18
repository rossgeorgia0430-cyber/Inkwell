// 通用小工具：DOM 查询、宿主桥接、剪贴板、toast、路径比较、平台判断与快捷键文案。
// 运行环境只有 WebView2（Chromium）与 WKWebView（macOS 11+），CSS.escape 等 API
// 两边都原生支持；这里不做"这个 API 存不存在"的特性探测，但保留对运行时拒绝
// （如剪贴板权限被拒）的真实恢复路径。
import { state } from "./state.js";

export function $(id) { return document.getElementById(id); }
export function api() { return (window.pywebview && window.pywebview.api) || null; }

export function isMacPlatform() {
  // page.py 的 __BOOT__ 总会写入 platform（build_page 里恒为 sys.platform）。
  return (window.__BOOT__ && window.__BOOT__.platform) === "darwin";
}

export function formatModShortcut(spec) {
  if (isMacPlatform()) {
    return spec.replace(/^mod\+Shift\+/i, "⇧⌘").replace(/^mod\+/i, "⌘");
  }
  return spec.replace(/^mod/i, "Ctrl");
}

export function applyShortcutTitles() {
  var nodes = document.querySelectorAll("[data-shortcut]");
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    var spec = el.getAttribute("data-shortcut") || "";
    var label = el.getAttribute("aria-label") || (el.textContent || "").trim();
    var hint = spec === "back"
      ? (isMacPlatform() ? "⌘[" : "Alt+←")
      : spec === "forward"
        ? (isMacPlatform() ? "⌘]" : "Alt+→")
        : formatModShortcut(spec);
    if (label && hint) el.title = label.replace(/\s*\(.*\)$/, "") + " (" + hint + ")";
    else if (hint) el.title = hint;
  }
}

export function escapeReg(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

// 把 id 拼进 CSS 选择器（querySelector("#" + id) / a[href="#id"]）时统一转义。
export function idSelector(id) { return "#" + CSS.escape(id); }

export function flash(btn, label) {
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
  try { document.execCommand("copy"); } catch (e) { /* 剪贴板被禁用时静默放弃，无更好的恢复手段 */ }
  document.body.removeChild(ta);
}

export function writeClipboard(text) {
  // 页面未聚焦等场景下，Clipboard API 会在运行时拒绝（而不是不存在）；
  // 这是真实会发生的恢复路径，不是特性探测。
  navigator.clipboard.writeText(text).catch(function () { fallbackCopy(text); });
}

export function savePreference(key, value) {
  var a = api();
  if (a && a.set_preference) a.set_preference(key, String(value));
}

export function toast(msg) {
  var t = $("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(function () { t.classList.remove("show"); }, 2600);
}

// 有未保存修改时统一征求用户确认；message 按各调用场景措辞。
export function confirmDiscard(message) {
  return !state.editDirty || window.confirm(message);
}

export function samePath(a, b) {
  if (!a || !b) return false;
  return String(a).replace(/\//g, "\\").toLowerCase() === String(b).replace(/\//g, "\\").toLowerCase();
}

export function baseName(p) {
  if (!p) return "";
  var s = String(p).replace(/\\/g, "/");
  var i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}

// 正文与 Live Preview 编辑区都可能承载可交互内容（复制按钮、图片、Mermaid）；
// 判断某个元素是否落在其中之一，供点击委托统一处理。
export function inRenderableRoot(el) {
  if (!el) return false;
  if (state.content && state.content.contains(el)) return true;
  var elive = state.editorLive || $("editorLive");
  return !!(elive && elive.contains(el));
}
