// Mermaid：仅在文档实际包含 Mermaid 围栏时才按需加载离线引擎。
// 源码由后端编码到 data-mermaid-source，图示与源码可随时切换。
import { state } from "./state.js";
import { $, toast, inRenderableRoot } from "./util.js";
import { openSvgViewer } from "./viewer.js";

function decodeMermaidSource(encoded) {
  try {
    var binary = window.atob(encoded || "");
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new TextDecoder("utf-8").decode(bytes);
  } catch (e) {
    return "";
  }
}

function mermaidTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "neutral";
}

function configureMermaid(lib) {
  lib.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: mermaidTheme(),
    flowchart: { htmlLabels: false, useMaxWidth: true },
  });
}

function getMermaidApi() {
  // 浏览器会把 id="mermaid" 的标题暴露为 window.mermaid；真实文档非常可能
  // 恰好有这个标题。因此优先读取离线 bundle 的私有命名空间，再退回普通全局。
  var bundle = window.__esbuild_esm_mermaid_nm;
  var bundled = bundle && bundle.mermaid && bundle.mermaid.default;
  if (bundled && typeof bundled.render === "function" && typeof bundled.initialize === "function") return bundled;
  var global = window.mermaid;
  if (global && typeof global.render === "function" && typeof global.initialize === "function") return global;
  return null;
}

function loadMermaid() {
  var existing = getMermaidApi();
  if (existing) return Promise.resolve(existing);
  if (state.mermaidLoadPromise) return state.mermaidLoadPromise;
  state.mermaidLoadPromise = new Promise(function (resolve, reject) {
    var script = document.createElement("script");
    script.src = "/assets/mermaid/mermaid.min.js";
    script.async = true;
    script.onload = function () {
      var loaded = getMermaidApi();
      if (loaded) resolve(loaded);
      else reject(new Error("Mermaid 引擎未正确加载"));
    };
    script.onerror = function () { reject(new Error("Mermaid 离线资源加载失败")); };
    document.head.appendChild(script);
  });
  return state.mermaidLoadPromise;
}

function setMermaidView(block, diagramVisible) {
  if (!block) return;
  block.classList.toggle("is-diagram", !!diagramVisible);
  var button = block.querySelector(".mermaid-toggle-btn");
  if (!button) return;
  var label = button.querySelector(".mermaid-toggle-label");
  button.setAttribute("aria-pressed", diagramVisible ? "true" : "false");
  button.title = diagramVisible ? "查看原始 Mermaid 代码" : "查看图示";
  if (label) label.textContent = diagramVisible ? "原始代码" : "图示";
}

function setMermaidError(block, message, notify) {
  if (!block || !block.isConnected) return;
  block.removeAttribute("data-mermaid-rendering");
  block.removeAttribute("data-mermaid-rendered-theme");
  block.setAttribute("data-mermaid-error", message || "图示无法渲染");
  setMermaidView(block, false);
  var button = block.querySelector(".mermaid-toggle-btn");
  if (button) button.title = "图示无法渲染，当前保留原始代码";
  if (notify) toast("Mermaid 图示无法渲染，已保留原始代码");
}

// Mermaid 在 strict 模式下已编码图中用户文本。foreignObject 是 Mermaid v11
// 绘制节点标签所必需的安全文本容器，不能整体删除；其余可执行/导航节点仍移除。
// 这样本地 Markdown 的图示既保留文字，也无法借 SVG 触及 pywebview 桥或外部链接。
function insertSafeMermaidSvg(host, svgText) {
  host.innerHTML = svgText;
  host.querySelectorAll("script, iframe, object, embed, audio, video, animate, set").forEach(function (node) {
    node.remove();
  });
  host.querySelectorAll("style").forEach(function (node) {
    node.textContent = (node.textContent || "")
      .replace(/@import[^;]*;?/gi, "")
      .replace(/expression\s*\([^)]*\)/gi, "")
      .replace(/url\s*\(\s*(['\"]?)\s*javascript:[^)]*\)/gi, "");
  });
  host.querySelectorAll("*").forEach(function (node) {
    Array.prototype.slice.call(node.attributes || []).forEach(function (attr) {
      var name = attr.name.toLowerCase();
      if (/^on/.test(name) || name === "href" || name === "xlink:href" || name === "src") {
        node.removeAttribute(attr.name);
      }
    });
  });
}

export function renderMermaidBlock(block, notifyOnError) {
  if (!block || !block.isConnected) return Promise.resolve(false);
  if (block.getAttribute("data-mermaid-rendering") === "1") return Promise.resolve(false);
  var source = decodeMermaidSource(block.getAttribute("data-mermaid-source"));
  var diagram = block.querySelector(".mermaid-diagram");
  if (!source || !diagram) {
    setMermaidError(block, "缺少 Mermaid 源码", notifyOnError);
    return Promise.resolve(false);
  }
  var theme = mermaidTheme();
  if (block.getAttribute("data-mermaid-rendered-theme") === theme && diagram.querySelector("svg")) {
    setMermaidView(block, true);
    return Promise.resolve(true);
  }
  block.setAttribute("data-mermaid-rendering", "1");
  diagram.innerHTML = '<div class="mermaid-loading">正在绘制图示…</div>';
  return loadMermaid().then(function (lib) {
    configureMermaid(lib);
    var id = "inkwell-mermaid-" + (++state.mermaidRenderSequence);
    return Promise.resolve(lib.render(id, source));
  }).then(function (result) {
    if (!block.isConnected || !result || typeof result.svg !== "string") return false;
    insertSafeMermaidSvg(diagram, result.svg);
    if (!diagram.querySelector("svg")) throw new Error("Mermaid 未生成 SVG");
    block.removeAttribute("data-mermaid-rendering");
    block.removeAttribute("data-mermaid-error");
    block.setAttribute("data-mermaid-rendered-theme", theme);
    setMermaidView(block, true);
    return true;
  }).catch(function (err) {
    setMermaidError(block, (err && err.message) || "图示无法渲染", notifyOnError);
    return false;
  });
}

export function toggleMermaid(block) {
  if (!block) return;
  if (block.classList.contains("is-diagram")) {
    setMermaidView(block, false);
    return;
  }
  renderMermaidBlock(block, true);
}

// 灯箱放大查看 Mermaid 图示：未渲染时先渲染，成功后克隆进灯箱。
export function zoomMermaid(block) {
  if (!block) return;
  var existing = block.querySelector(".mermaid-diagram svg");
  if (existing) { openSvgViewer(existing); return; }
  renderMermaidBlock(block, true).then(function (ok) {
    var svg = ok && block.querySelector(".mermaid-diagram svg");
    if (svg) openSvgViewer(svg);
  });
}

// 点击图示本体也能进入灯箱，与点击图片的交互保持一致。
export function onContentDiagramClick(e) {
  var diagram = e.target.closest && e.target.closest(".mermaid-diagram");
  if (!diagram || !inRenderableRoot(diagram)) return;
  var block = diagram.closest(".mermaid-block");
  if (!block || !block.classList.contains("is-diagram")) return;
  e.preventDefault();
  zoomMermaid(block);
}

export function initMermaidDiagrams(root) {
  if (!root) return;
  root.querySelectorAll(".mermaid-block[data-mermaid-source]").forEach(function (block) {
    setMermaidView(block, false);
    renderMermaidBlock(block, false);
  });
}

export function refreshMermaidDiagrams() {
  if (!getMermaidApi()) return;
  var roots = [];
  if (state.content) roots.push(state.content);
  var el = state.editorLive || $("editorLive");
  if (el) roots.push(el);
  roots.forEach(function (root) {
    root.querySelectorAll(".mermaid-block.is-diagram").forEach(function (block) {
      block.removeAttribute("data-mermaid-rendered-theme");
      renderMermaidBlock(block, false);
    });
  });
}
