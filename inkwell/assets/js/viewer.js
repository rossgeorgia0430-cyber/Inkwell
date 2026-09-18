// 图片：悬停复制按钮 + 点击进入灯箱；灯箱同时承载 Mermaid 图示（openSvgViewer）。
import { state } from "./state.js";
import { api, flash, toast, inRenderableRoot } from "./util.js";

const COPY_ICON_SVG = '<svg viewBox="0 0 24 24" class="copy-ico" aria-hidden="true"><rect x="8" y="8" width="10" height="11" rx="1.5"/><path d="M6 15H5.5A1.5 1.5 0 0 1 4 13.5v-8A1.5 1.5 0 0 1 5.5 4h8A1.5 1.5 0 0 1 15 5.5V6"/></svg>';

function canvasToPngBlob(canvas) {
  return new Promise(function (resolve, reject) {
    canvas.toBlob(function (blob) {
      if (blob) resolve(blob);
      else reject(new Error("无法编码图片"));
    }, "image/png");
  });
}

function writePngToClipboard(png) {
  if (!navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem) {
    return Promise.reject(new Error("浏览器不支持图片剪贴板 API"));
  }
  return navigator.clipboard.write([new window.ClipboardItem({ "image/png": png })]);
}

function reportCopyResult(promise, btn, successMsg, failMsg) {
  return promise.then(function () {
    if (btn) flash(btn, "已复制");
    else toast(successMsg);
  }).catch(function (err) {
    toast((err && err.message) || failMsg);
  });
}

export function imageToPngBlob(img) {
  return new Promise(function (resolve, reject) {
    if (!img || !img.complete || !img.naturalWidth || !img.naturalHeight) {
      reject(new Error("图片尚未加载完成"));
      return;
    }
    try {
      var canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      var ctx = canvas.getContext("2d");
      if (!ctx) { reject(new Error("浏览器不支持图片绘制")); return; }
      ctx.drawImage(img, 0, 0);
      canvasToPngBlob(canvas).then(resolve, reject);
    } catch (err) {
      // 跨域图片没有 CORS 许可时，canvas 会被污染；随后回退到本机 bridge。
      reject(err);
    }
  });
}

function copyImageThroughHost(img) {
  var a = api();
  var source = img && (img.currentSrc || img.src);
  if (!a || !a.copy_image || !source) {
    return Promise.reject(new Error("当前环境无法复制这张图片"));
  }
  return Promise.resolve(a.copy_image(source)).then(function (result) {
    if (result && result.ok) return result;
    throw new Error((result && result.error) || "无法复制这张图片");
  });
}

export function copyImage(img, btn) {
  if (!img) return;
  reportCopyResult(
    imageToPngBlob(img).then(writePngToClipboard).catch(function () {
      // pywebview / WebView2 权限策略不允许异步 Clipboard API 时，复制临时本地化资源。
      return copyImageThroughHost(img);
    }),
    btn, "图片已复制", "图片复制失败"
  );
}

export function clearSelectedImage() {
  state.selectedImage = null;
}

export function selectImage(img) {
  if (!img || !inRenderableRoot(img)) return;
  state.selectedImage = img;
}

function createImageCopyButton() {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "image-copy-btn";
  btn.setAttribute("data-copy-action", "image");
  btn.setAttribute("aria-label", "复制图片");
  btn.innerHTML = COPY_ICON_SVG + '<span class="copy-label">复制</span>';
  return btn;
}

function wrapImage(img) {
  var existing = img.closest(".image-block");
  if (existing) return existing;
  var host = img;
  if (host.parentElement && host.parentElement.tagName === "PICTURE") host = host.parentElement;
  if (host.parentElement && host.parentElement.tagName === "A") host = host.parentElement;
  var parent = host.parentNode;
  if (!parent) return null;
  var block = document.createElement("span");
  block.className = "image-block";
  parent.insertBefore(block, host);
  block.appendChild(host);
  block.appendChild(createImageCopyButton());
  return block;
}

export function setupImageCopySupport(root) {
  clearSelectedImage();
  root.querySelectorAll("img").forEach(function (img) {
    if (img.getAttribute("data-inkwell-copyable") === "true") return;
    img.setAttribute("data-inkwell-copyable", "true");
    if (!img.hasAttribute("tabindex")) img.tabIndex = 0;
    wrapImage(img);
    img.addEventListener("mousedown", function () { selectImage(img); });
  });
}

function ensureImageViewer() {
  if (state.imageViewer) return state.imageViewer;
  var viewer = document.createElement("div");
  viewer.className = "image-viewer";
  viewer.setAttribute("role", "dialog");
  viewer.setAttribute("aria-modal", "true");
  viewer.setAttribute("aria-label", "图片预览");
  viewer.innerHTML = '<div class="image-viewer-actions"><button type="button" class="image-viewer-btn image-viewer-copy" aria-label="复制图片">'
    + COPY_ICON_SVG + '<span class="copy-label">复制</span></button><button type="button" class="image-viewer-btn image-viewer-close" aria-label="关闭图片预览"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div><div class="image-viewer-stage"><img class="image-viewer-image" alt=""></div><div class="image-viewer-zoom"><button type="button" class="image-zoom-btn image-zoom-out" aria-label="缩小图片" title="缩小 (Ctrl+滚轮向下)"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M7.5 10.5h6"/></svg></button><span class="image-zoom-level" aria-live="polite">100%</span><button type="button" class="image-zoom-btn image-zoom-in" aria-label="放大图片" title="放大 (Ctrl+滚轮向上)"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M7.5 10.5h6M10.5 7.5v6"/></svg></button><span class="image-zoom-hint">右键拖动平移</span></div>';
  var close = viewer.querySelector(".image-viewer-close");
  var preview = viewer.querySelector(".image-viewer-image");
  var copy = viewer.querySelector(".image-viewer-copy");
  var stage = viewer.querySelector(".image-viewer-stage");
  close.addEventListener("click", closeImageViewer);
  copy.addEventListener("click", function () { copyViewerContent(copy); });
  viewer.querySelector(".image-zoom-out").addEventListener("click", function () { zoomImageViewer(-1); });
  viewer.querySelector(".image-zoom-in").addEventListener("click", function () { zoomImageViewer(1); });
  preview.addEventListener("load", function () {
    if (viewer.classList.contains("open")) requestAnimationFrame(fitImageViewer);
  });
  viewer.addEventListener("click", function (e) {
    if (e.target === viewer || e.target.classList.contains("image-viewer-stage")) closeImageViewer();
  });
  // 右键按住拖动平移：内容超出视口时直接抓取拖动，代替滚动条；
  // 灯箱内右键只作平移，因此屏蔽系统右键菜单避免干扰。
  stage.addEventListener("mousedown", function (e) {
    if (e.button !== 2) return;
    e.preventDefault();
    state.imagePan = { x: e.clientX, y: e.clientY, left: stage.scrollLeft, top: stage.scrollTop, moved: false };
    stage.classList.add("panning");
  });
  window.addEventListener("mousemove", function (e) {
    if (!state.imagePan) return;
    var dx = e.clientX - state.imagePan.x, dy = e.clientY - state.imagePan.y;
    if (!state.imagePan.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
    state.imagePan.moved = true;
    stage.scrollLeft = state.imagePan.left - dx;
    stage.scrollTop = state.imagePan.top - dy;
  });
  window.addEventListener("mouseup", function (e) {
    if (!state.imagePan || e.button !== 2) return;
    endImagePan();
  });
  window.addEventListener("blur", endImagePan);   // 拖出窗口松开时复位
  viewer.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  document.body.appendChild(viewer);
  state.imageViewer = viewer;
  return viewer;
}

const IMAGE_ZOOM_MIN = .02, IMAGE_ZOOM_MAX = 8, IMAGE_ZOOM_STEP = 1.2;

export function imageViewerIsOpen() {
  return !!(state.imageViewer && state.imageViewer.classList.contains("open"));
}

function updateImageZoomControls() {
  if (!state.imageViewer) return;
  var level = state.imageViewer.querySelector(".image-zoom-level");
  var out = state.imageViewer.querySelector(".image-zoom-out");
  var plus = state.imageViewer.querySelector(".image-zoom-in");
  if (level) level.textContent = Math.round(state.imageViewerScale * 100) + "%";
  if (out) out.disabled = state.imageViewerScale <= IMAGE_ZOOM_MIN + .0001;
  if (plus) plus.disabled = state.imageViewerScale >= IMAGE_ZOOM_MAX - .0001;
}

// 灯箱当前缩放的内容元素：图片是 <img>，Mermaid 图示是克隆的 <svg>。
function viewerContentEl() {
  if (!state.imageViewer) return null;
  if (state.imageViewerKind === "svg") return state.imageViewerSvg;
  return state.imageViewer.querySelector(".image-viewer-image");
}

// 图片模式的固有尺寸来自 <img>.naturalWidth；SVG 模式在打开时已写入。
function syncViewerNatural() {
  if (state.imageViewerKind !== "image" || !state.imageViewer) return;
  var preview = state.imageViewer.querySelector(".image-viewer-image");
  if (preview && preview.naturalWidth && preview.naturalHeight) {
    state.imageViewerNatural.w = preview.naturalWidth;
    state.imageViewerNatural.h = preview.naturalHeight;
  }
}

function applyImageViewerScale(next, preserveCenter, anchor) {
  if (!state.imageViewer) return;
  syncViewerNatural();
  var content = viewerContentEl();
  var stage = state.imageViewer.querySelector(".image-viewer-stage");
  if (!content || !stage || !state.imageViewerNatural.w || !state.imageViewerNatural.h) return;
  next = Math.max(IMAGE_ZOOM_MIN, Math.min(IMAGE_ZOOM_MAX, next));
  var stageRect = stage.getBoundingClientRect();
  var anchorClientX = anchor ? anchor.x : stageRect.left + stage.clientWidth / 2;
  var anchorClientY = anchor ? anchor.y : stageRect.top + stage.clientHeight / 2;
  anchorClientX = Math.max(stageRect.left, Math.min(stageRect.right, anchorClientX));
  anchorClientY = Math.max(stageRect.top, Math.min(stageRect.bottom, anchorClientY));
  // 用 getBoundingClientRect 统一度量：<img> 与 <svg> 都适用
  // （offsetLeft/offsetWidth 只存在于 HTMLElement，SVG 元素上是 undefined）。
  var oldRect = content.getBoundingClientRect();
  var oldLeft = oldRect.left - stageRect.left + stage.scrollLeft;
  var oldTop = oldRect.top - stageRect.top + stage.scrollTop;
  var oldWidth = oldRect.width || state.imageViewerNatural.w * state.imageViewerScale;
  var oldHeight = oldRect.height || state.imageViewerNatural.h * state.imageViewerScale;
  var contentX = stage.scrollLeft + anchorClientX - stageRect.left;
  var contentY = stage.scrollTop + anchorClientY - stageRect.top;
  var imageX = oldWidth ? (contentX - oldLeft) / oldWidth : .5;
  var imageY = oldHeight ? (contentY - oldTop) / oldHeight : .5;
  state.imageViewerScale = next;
  var width = state.imageViewerNatural.w * next;
  var height = state.imageViewerNatural.h * next;
  var top = Math.max(24, (stage.clientHeight - height) / 2);
  content.style.width = width.toFixed(3) + "px";
  // <img> 按宽度等比自适应；<svg> 需要显式高度才能稳定占位。
  content.style.height = state.imageViewerKind === "svg" ? height.toFixed(3) + "px" : "auto";
  content.style.marginTop = Math.round(top) + "px";
  content.style.marginBottom = "24px";
  updateImageZoomControls();
  if (preserveCenter) {
    // 读取 getBoundingClientRect 会强制一次布局，拿到新尺寸下的真实位置，
    // 再把同一图像点保持在指针（或视口中心）之下——避免宽度动画与滚动修正打架。
    var newRect = content.getBoundingClientRect();
    var newLeft = newRect.left - stageRect.left + stage.scrollLeft;
    var newTop = newRect.top - stageRect.top + stage.scrollTop;
    stage.scrollLeft = newLeft + imageX * newRect.width - (anchorClientX - stageRect.left);
    stage.scrollTop = newTop + imageY * newRect.height - (anchorClientY - stageRect.top);
  } else {
    stage.scrollLeft = 0;
    stage.scrollTop = 0;
  }
}

export function fitImageViewer() {
  if (!imageViewerIsOpen()) return;
  syncViewerNatural();
  var stage = state.imageViewer && state.imageViewer.querySelector(".image-viewer-stage");
  if (!stage || !state.imageViewerNatural.w || !state.imageViewerNatural.h) return;
  var availableWidth = Math.max(1, stage.clientWidth - 48);
  var availableHeight = Math.max(1, stage.clientHeight - 48);
  // contain 比例既会缩小超大图，也会主动放大小图来利用当前窗口空间。
  state.imageViewerFitScale = Math.max(IMAGE_ZOOM_MIN, Math.min(4,
    Math.min(availableWidth / state.imageViewerNatural.w, availableHeight / state.imageViewerNatural.h)));
  state.imageViewerUserAdjusted = false;
  applyImageViewerScale(state.imageViewerFitScale, false);
}

export function zoomImageViewer(direction) {
  if (!imageViewerIsOpen()) return;
  state.imageViewerUserAdjusted = true;
  var factor = direction > 0 ? IMAGE_ZOOM_STEP : (1 / IMAGE_ZOOM_STEP);
  applyImageViewerScale(state.imageViewerScale * factor, true);
}

export function zoomImageViewerByWheel(deltaY, clientX, clientY) {
  if (!imageViewerIsOpen()) return;
  state.imageViewerUserAdjusted = true;
  // 触控板与滚轮的 deltaY 量级差异很大；用指数缩放让两者的手感都连续，
  // clamp 防止个别异常大的事件导致缩放跳变。
  var factor = Math.exp(Math.max(-80, Math.min(80, -deltaY)) * .0025);
  applyImageViewerScale(state.imageViewerScale * factor, true, { x: clientX, y: clientY });
}

export function openImageViewer(img) {
  if (!img || !img.currentSrc && !img.src) return;
  var viewer = ensureImageViewer();
  var preview = viewer.querySelector(".image-viewer-image");
  // 从 SVG 模式切回图片：撤下 staged svg、恢复 <img> 显示。
  if (state.imageViewerSvg) { state.imageViewerSvg.remove(); state.imageViewerSvg = null; }
  preview.style.display = "";
  state.imageViewerKind = "image";
  state.imageViewerReturnFocus = img;
  selectImage(img);
  preview.src = img.currentSrc || img.src;
  preview.alt = img.alt || "图片预览";
  viewer.classList.add("open");
  document.body.classList.add("image-viewer-open");
  state.imageViewerUserAdjusted = false;
  if (preview.complete && preview.naturalWidth) requestAnimationFrame(fitImageViewer);
  viewer.querySelector(".image-viewer-close").focus({ preventScroll: true });
}

// Mermaid 图示灯箱：克隆渲染好的 SVG，与图片共享缩放 / 右键平移 / 关闭交互。
export function openSvgViewer(svg) {
  if (!svg) return;
  var viewer = ensureImageViewer();
  var stage = viewer.querySelector(".image-viewer-stage");
  var preview = viewer.querySelector(".image-viewer-image");
  // 固有尺寸：优先 viewBox（矢量固有尺寸），退回当前渲染尺寸。
  var w = 0, h = 0;
  var vb = (svg.getAttribute("viewBox") || "").trim().split(/[\s,]+/);
  if (vb.length === 4) {
    w = parseFloat(vb[2]) || 0;
    h = parseFloat(vb[3]) || 0;
  }
  if (!w || !h) {
    var rect = svg.getBoundingClientRect();
    w = rect.width; h = rect.height;
  }
  if (!w || !h) return;
  // 保留原 id：Mermaid 内嵌 <style> 与 marker 的 url(#…) 引用都按 id 寻址，
  // 克隆体沿用同一 id 时样式与箭头仍然命中（文档内原图与克隆体内容一致）。
  var clone = svg.cloneNode(true);
  clone.setAttribute("class", "image-viewer-svg");
  clone.setAttribute("width", w);
  clone.setAttribute("height", h);
  clone.style.maxWidth = "none";
  if (state.imageViewerSvg) state.imageViewerSvg.remove();
  state.imageViewerSvg = clone;
  preview.style.display = "none";
  stage.appendChild(clone);
  state.imageViewerKind = "svg";
  state.imageViewerNatural = { w: w, h: h };
  state.imageViewerReturnFocus = svg.closest(".mermaid-diagram") || svg;
  viewer.classList.add("open");
  document.body.classList.add("image-viewer-open");
  state.imageViewerUserAdjusted = false;
  requestAnimationFrame(fitImageViewer);
  viewer.querySelector(".image-viewer-close").focus({ preventScroll: true });
}

export function closeImageViewer() {
  if (!imageViewerIsOpen()) return;
  endImagePan();
  state.imageViewer.classList.remove("open");
  document.body.classList.remove("image-viewer-open");
  state.imageViewerUserAdjusted = false;
  if (state.imageViewerReturnFocus && state.imageViewerReturnFocus.isConnected) {
    state.imageViewerReturnFocus.focus({ preventScroll: true });
  }
}

function endImagePan() {
  state.imagePan = null;
  if (!state.imageViewer) return;
  var stage = state.imageViewer.querySelector(".image-viewer-stage");
  if (stage) stage.classList.remove("panning");
}

// 灯箱复制：图片直接复制像素；Mermaid 图示序列化后栅格化成 PNG 再复制。
function copyViewerContent(btn) {
  if (!state.imageViewer) return;
  if (state.imageViewerKind === "svg" && state.imageViewerSvg) {
    copySvgAsPng(state.imageViewerSvg, btn);
    return;
  }
  copyImage(state.imageViewer.querySelector(".image-viewer-image"), btn);
}

export function copySvgAsPng(svg, btn) {
  reportCopyResult(svgToPngBlob(svg).then(writePngToClipboard), btn, "图示已复制", "图示复制失败");
}

function svgToPngBlob(svg) {
  return new Promise(function (resolve, reject) {
    var w = state.imageViewerNatural.w, h = state.imageViewerNatural.h;
    if (!svg || !w || !h) { reject(new Error("图示尚未就绪")); return; }
    // 栅格化按 2 倍输出保证清晰度，同时限制在 4096px 内避免巨型画布。
    var scale = Math.max(1, Math.min(2, 4096 / Math.max(w, h)));
    var cw = Math.round(w * scale), ch = Math.round(h * scale);
    var clone = svg.cloneNode(true);
    clone.setAttribute("width", cw);
    clone.setAttribute("height", ch);
    clone.style.width = cw + "px";
    clone.style.height = ch + "px";
    clone.style.maxWidth = "none";
    var xml;
    try {
      xml = new XMLSerializer().serializeToString(clone);
    } catch (err) { reject(err); return; }
    var url = URL.createObjectURL(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }));
    var img = new Image();
    img.onload = function () {
      try {
        var canvas = document.createElement("canvas");
        canvas.width = cw; canvas.height = ch;
        var ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("浏览器不支持图片绘制");
        // SVG 透明底：垫上当前主题的代码底色，粘贴到飞书/Word 才不会发黑。
        var bg = getComputedStyle(document.documentElement).getPropertyValue("--code-bg") || "#ffffff";
        ctx.fillStyle = bg.trim();
        ctx.fillRect(0, 0, cw, ch);
        ctx.drawImage(img, 0, 0, cw, ch);
        URL.revokeObjectURL(url);
        canvasToPngBlob(canvas).then(resolve, reject);
      } catch (err) {
        URL.revokeObjectURL(url);
        reject(err);
      }
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      reject(new Error("无法读取图示"));
    };
    img.src = url;
  });
}

export function onContentImageClick(e) {
  var img = e.target.closest && e.target.closest(".image-block img");
  if (!img || !inRenderableRoot(img)) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  openImageViewer(img);
}
