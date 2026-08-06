/* ============================================================
   Inkwell — 前端逻辑
   公式渲染 / 飞书无底色复制 / 代码双击高亮 / 搜索 / 目录 / 主题 / 无边框窗口
   ============================================================ */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var api = function () { return (window.pywebview && window.pywebview.api) || null; };

  var content, main, app, sidebar, toc, dragRegion, docTitle;
  var currentPath = null;
  var headings = [];
  var selectedImage = null;
  var imageViewer = null;
  var imageViewerReturnFocus = null;
  var imageViewerScale = 1;
  var imageViewerFitScale = 1;
  var imageViewerUserAdjusted = false;
  var imageViewerKind = "image";          // "image" | "svg"（Mermaid 图示复用同一灯箱）
  var imageViewerNatural = { w: 0, h: 0 };
  var imageViewerSvg = null;
  var imagePan = null;                    // 右键按住拖动平移状态

  // ---------- 工具 ----------
  function escapeReg(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function flash(btn, label) {
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
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  function writeClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(function () { fallbackCopy(text); });
        return;
      }
    } catch (e) {}
    fallbackCopy(text);
  }

  function savePreference(key, value) {
    var a = api();
    if (a && a.set_preference) a.set_preference(key, String(value));
  }

  // ============================================================
  // 1) 公式渲染：直接用 data-latex 调 katex.render（不扫描正文）
  // ============================================================
  function renderMath(root) {
    if (!window.katex) return;
    root.querySelectorAll(".math-block[data-latex]").forEach(function (el) {
      var tex = el.getAttribute("data-latex");
      var btn = el.querySelector(".math-copy-btn");
      try {
        window.katex.render(tex, el, {
          displayMode: true, throwOnError: false, strict: false, output: "htmlAndMathml"
        });
      } catch (e) { el.textContent = "$$" + tex + "$$"; }
      if (btn) el.appendChild(btn);            // katex.render 会清空子节点，重新挂回复制按钮
    });
    root.querySelectorAll(".math-inline[data-latex]").forEach(function (el) {
      var tex = el.getAttribute("data-latex");
      try {
        window.katex.render(tex, el, {
          displayMode: false, throwOnError: false, strict: false, output: "htmlAndMathml"
        });
      } catch (e) { el.textContent = "$" + tex + "$"; }
    });
  }

  // 复制单条公式的 LaTeX（由正文容器统一事件委托）
  function copyLatex(btn) {
    var host = btn.closest("[data-latex]");
    if (!host) return;
    var tex = host.getAttribute("data-latex") || "";
    writeClipboard(tex);
    flash(btn, "已复制");
  }

  // ============================================================
  // 2) 飞书无底色复制：拦截 copy，净化选区后写 text/html + text/plain
  // ============================================================
  var STRIP_PROPS = [
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

  function sanitizeForCopy(holder) {
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
  function buildCopyPayload(sel) {
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

  function onCopy(e) {
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
      if (selectedImage && selectedImage.isConnected) {
        e.preventDefault();
        e.stopImmediatePropagation();
        var imageButton = selectedImage.closest(".image-block");
        copyImage(selectedImage, imageButton && imageButton.querySelector(".image-copy-btn"));
      }
    } catch (err) { /* 出错则放行默认复制 */ }
  }

  // ============================================================
  // Mermaid：仅在文档实际包含 Mermaid 围栏时才按需加载离线引擎。
  // 源码由后端编码到 data-mermaid-source，图示与源码可随时切换。
  // ============================================================
  var mermaidLoadPromise = null;
  var mermaidRenderSequence = 0;

  function decodeMermaidSource(encoded) {
    try {
      var binary = window.atob(encoded || "");
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      if (window.TextDecoder) return new TextDecoder("utf-8").decode(bytes);
      var escaped = "";
      for (var j = 0; j < bytes.length; j++) escaped += "%" + ("0" + bytes[j].toString(16)).slice(-2);
      return decodeURIComponent(escaped);
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
    if (mermaidLoadPromise) return mermaidLoadPromise;
    mermaidLoadPromise = new Promise(function (resolve, reject) {
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
    return mermaidLoadPromise;
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

  function renderMermaidBlock(block, notifyOnError) {
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
      var id = "inkwell-mermaid-" + (++mermaidRenderSequence);
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

  function toggleMermaid(block) {
    if (!block) return;
    if (block.classList.contains("is-diagram")) {
      setMermaidView(block, false);
      return;
    }
    renderMermaidBlock(block, true);
  }

  // 灯箱放大查看 Mermaid 图示：未渲染时先渲染，成功后克隆进灯箱。
  function zoomMermaid(block) {
    if (!block) return;
    var existing = block.querySelector(".mermaid-diagram svg");
    if (existing) { openSvgViewer(existing); return; }
    renderMermaidBlock(block, true).then(function (ok) {
      var svg = ok && block.querySelector(".mermaid-diagram svg");
      if (svg) openSvgViewer(svg);
    });
  }

  // 点击图示本体也能进入灯箱，与点击图片的交互保持一致。
  function onContentDiagramClick(e) {
    var diagram = e.target.closest && e.target.closest(".mermaid-diagram");
    if (!diagram || !inRenderableRoot(diagram)) return;
    var block = diagram.closest(".mermaid-block");
    if (!block || !block.classList.contains("is-diagram")) return;
    e.preventDefault();
    zoomMermaid(block);
  }

  function initMermaidDiagrams(root) {
    if (!root) return;
    root.querySelectorAll(".mermaid-block[data-mermaid-source]").forEach(function (block) {
      setMermaidView(block, false);
      renderMermaidBlock(block, false);
    });
  }

  function refreshMermaidDiagrams() {
    if (!getMermaidApi()) return;
    var roots = [];
    if (content) roots.push(content);
    var el = editorLive || $("editorLive");
    if (el) roots.push(el);
    roots.forEach(function (root) {
      root.querySelectorAll(".mermaid-block.is-diagram").forEach(function (block) {
        block.removeAttribute("data-mermaid-rendered-theme");
        renderMermaidBlock(block, false);
      });
    });
  }

  // ============================================================
  // 3) 图片复制：悬停按钮 + 点击图片后 Ctrl+C
  // ============================================================
  function imageToPngBlob(img) {
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
        canvas.toBlob(function (blob) {
          if (blob) resolve(blob);
          else reject(new Error("无法编码图片"));
        }, "image/png");
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

  function copyImage(img, btn) {
    if (!img) return;
    imageToPngBlob(img).then(function (png) {
      if (!navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem) {
        throw new Error("浏览器不支持图片剪贴板 API");
      }
      return navigator.clipboard.write([new window.ClipboardItem({ "image/png": png })]);
    }).catch(function () {
      // pywebview / WebView2 权限策略不允许异步 Clipboard API 时，复制临时本地化资源。
      return copyImageThroughHost(img);
    }).then(function () {
      if (btn) flash(btn, "已复制");
      else toast("图片已复制");
    }).catch(function (err) {
      toast((err && err.message) || "图片复制失败");
    });
  }

  function clearSelectedImage() {
    selectedImage = null;
  }

  function selectImage(img) {
    if (!img || !inRenderableRoot(img)) return;
    selectedImage = img;
  }

  function createImageCopyButton() {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "image-copy-btn";
    btn.setAttribute("data-copy-action", "image");
    btn.setAttribute("aria-label", "复制图片");
    btn.innerHTML = '<svg viewBox="0 0 24 24" class="copy-ico" aria-hidden="true"><rect x="8" y="8" width="10" height="11" rx="1.5"/><path d="M6 15H5.5A1.5 1.5 0 0 1 4 13.5v-8A1.5 1.5 0 0 1 5.5 4h8A1.5 1.5 0 0 1 15 5.5V6"/></svg><span class="copy-label">复制</span>';
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

  function setupImageCopySupport(root) {
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
    if (imageViewer) return imageViewer;
    var viewer = document.createElement("div");
    viewer.className = "image-viewer";
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-label", "图片预览");
    viewer.innerHTML = '<div class="image-viewer-actions"><button type="button" class="image-viewer-btn image-viewer-copy" aria-label="复制图片"><svg viewBox="0 0 24 24" class="copy-ico" aria-hidden="true"><rect x="8" y="8" width="10" height="11" rx="1.5"/><path d="M6 15H5.5A1.5 1.5 0 0 1 4 13.5v-8A1.5 1.5 0 0 1 5.5 4h8A1.5 1.5 0 0 1 15 5.5V6"/></svg><span class="copy-label">复制</span></button><button type="button" class="image-viewer-btn image-viewer-close" aria-label="关闭图片预览"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div><div class="image-viewer-stage"><img class="image-viewer-image" alt=""></div><div class="image-viewer-zoom"><button type="button" class="image-zoom-btn image-zoom-out" aria-label="缩小图片" title="缩小 (Ctrl+滚轮向下)"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M7.5 10.5h6"/></svg></button><span class="image-zoom-level" aria-live="polite">100%</span><button type="button" class="image-zoom-btn image-zoom-in" aria-label="放大图片" title="放大 (Ctrl+滚轮向上)"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M7.5 10.5h6M10.5 7.5v6"/></svg></button><span class="image-zoom-hint">右键拖动平移</span></div>';
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
      imagePan = { x: e.clientX, y: e.clientY, left: stage.scrollLeft, top: stage.scrollTop, moved: false };
      stage.classList.add("panning");
    });
    window.addEventListener("mousemove", function (e) {
      if (!imagePan) return;
      var dx = e.clientX - imagePan.x, dy = e.clientY - imagePan.y;
      if (!imagePan.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      imagePan.moved = true;
      stage.scrollLeft = imagePan.left - dx;
      stage.scrollTop = imagePan.top - dy;
    });
    window.addEventListener("mouseup", function (e) {
      if (!imagePan || e.button !== 2) return;
      endImagePan();
    });
    window.addEventListener("blur", endImagePan);   // 拖出窗口松开时复位
    viewer.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    document.body.appendChild(viewer);
    imageViewer = viewer;
    return viewer;
  }

  var IMAGE_ZOOM_MIN = .02, IMAGE_ZOOM_MAX = 8, IMAGE_ZOOM_STEP = 1.2;

  function imageViewerIsOpen() {
    return !!(imageViewer && imageViewer.classList.contains("open"));
  }

  function updateImageZoomControls() {
    if (!imageViewer) return;
    var level = imageViewer.querySelector(".image-zoom-level");
    var out = imageViewer.querySelector(".image-zoom-out");
    var plus = imageViewer.querySelector(".image-zoom-in");
    if (level) level.textContent = Math.round(imageViewerScale * 100) + "%";
    if (out) out.disabled = imageViewerScale <= IMAGE_ZOOM_MIN + .0001;
    if (plus) plus.disabled = imageViewerScale >= IMAGE_ZOOM_MAX - .0001;
  }

  // 灯箱当前缩放的内容元素：图片是 <img>，Mermaid 图示是克隆的 <svg>。
  function viewerContentEl() {
    if (!imageViewer) return null;
    if (imageViewerKind === "svg") return imageViewerSvg;
    return imageViewer.querySelector(".image-viewer-image");
  }

  // 图片模式的固有尺寸来自 <img>.naturalWidth；SVG 模式在打开时已写入。
  function syncViewerNatural() {
    if (imageViewerKind !== "image" || !imageViewer) return;
    var preview = imageViewer.querySelector(".image-viewer-image");
    if (preview && preview.naturalWidth && preview.naturalHeight) {
      imageViewerNatural.w = preview.naturalWidth;
      imageViewerNatural.h = preview.naturalHeight;
    }
  }

  function applyImageViewerScale(next, preserveCenter, anchor) {
    if (!imageViewer) return;
    syncViewerNatural();
    var content = viewerContentEl();
    var stage = imageViewer.querySelector(".image-viewer-stage");
    if (!content || !stage || !imageViewerNatural.w || !imageViewerNatural.h) return;
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
    var oldWidth = oldRect.width || imageViewerNatural.w * imageViewerScale;
    var oldHeight = oldRect.height || imageViewerNatural.h * imageViewerScale;
    var contentX = stage.scrollLeft + anchorClientX - stageRect.left;
    var contentY = stage.scrollTop + anchorClientY - stageRect.top;
    var imageX = oldWidth ? (contentX - oldLeft) / oldWidth : .5;
    var imageY = oldHeight ? (contentY - oldTop) / oldHeight : .5;
    imageViewerScale = next;
    var width = imageViewerNatural.w * next;
    var height = imageViewerNatural.h * next;
    var top = Math.max(24, (stage.clientHeight - height) / 2);
    content.style.width = width.toFixed(3) + "px";
    // <img> 按宽度等比自适应；<svg> 需要显式高度才能稳定占位。
    content.style.height = imageViewerKind === "svg" ? height.toFixed(3) + "px" : "auto";
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

  function fitImageViewer() {
    if (!imageViewerIsOpen()) return;
    syncViewerNatural();
    var stage = imageViewer && imageViewer.querySelector(".image-viewer-stage");
    if (!stage || !imageViewerNatural.w || !imageViewerNatural.h) return;
    var availableWidth = Math.max(1, stage.clientWidth - 48);
    var availableHeight = Math.max(1, stage.clientHeight - 48);
    // contain 比例既会缩小超大图，也会主动放大小图来利用当前窗口空间。
    imageViewerFitScale = Math.max(IMAGE_ZOOM_MIN, Math.min(4,
      Math.min(availableWidth / imageViewerNatural.w, availableHeight / imageViewerNatural.h)));
    imageViewerUserAdjusted = false;
    applyImageViewerScale(imageViewerFitScale, false);
  }

  function zoomImageViewer(direction) {
    if (!imageViewerIsOpen()) return;
    imageViewerUserAdjusted = true;
    var factor = direction > 0 ? IMAGE_ZOOM_STEP : (1 / IMAGE_ZOOM_STEP);
    applyImageViewerScale(imageViewerScale * factor, true);
  }

  function zoomImageViewerByWheel(deltaY, clientX, clientY) {
    if (!imageViewerIsOpen()) return;
    imageViewerUserAdjusted = true;
    // Trackpad and wheel deltas vary widely. Exponential scaling keeps both
    // continuous, while the clamp prevents one unusually large event jumping.
    var factor = Math.exp(Math.max(-80, Math.min(80, -deltaY)) * .0025);
    applyImageViewerScale(imageViewerScale * factor, true, { x: clientX, y: clientY });
  }

  function openImageViewer(img) {
    if (!img || !img.currentSrc && !img.src) return;
    var viewer = ensureImageViewer();
    var preview = viewer.querySelector(".image-viewer-image");
    // 从 SVG 模式切回图片：撤下 staged svg、恢复 <img> 显示。
    if (imageViewerSvg) { imageViewerSvg.remove(); imageViewerSvg = null; }
    preview.style.display = "";
    imageViewerKind = "image";
    imageViewerReturnFocus = img;
    selectImage(img);
    preview.src = img.currentSrc || img.src;
    preview.alt = img.alt || "图片预览";
    viewer.classList.add("open");
    document.body.classList.add("image-viewer-open");
    imageViewerUserAdjusted = false;
    if (preview.complete && preview.naturalWidth) requestAnimationFrame(fitImageViewer);
    viewer.querySelector(".image-viewer-close").focus({ preventScroll: true });
  }

  // Mermaid 图示灯箱：克隆渲染好的 SVG，与图片共享缩放 / 右键平移 / 关闭交互。
  function openSvgViewer(svg) {
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
    if (imageViewerSvg) imageViewerSvg.remove();
    imageViewerSvg = clone;
    preview.style.display = "none";
    stage.appendChild(clone);
    imageViewerKind = "svg";
    imageViewerNatural = { w: w, h: h };
    imageViewerReturnFocus = svg.closest(".mermaid-diagram") || svg;
    viewer.classList.add("open");
    document.body.classList.add("image-viewer-open");
    imageViewerUserAdjusted = false;
    requestAnimationFrame(fitImageViewer);
    viewer.querySelector(".image-viewer-close").focus({ preventScroll: true });
  }

  function closeImageViewer() {
    if (!imageViewer || !imageViewer.classList.contains("open")) return;
    endImagePan();
    imageViewer.classList.remove("open");
    document.body.classList.remove("image-viewer-open");
    imageViewerUserAdjusted = false;
    if (imageViewerReturnFocus && imageViewerReturnFocus.isConnected) {
      imageViewerReturnFocus.focus({ preventScroll: true });
    }
  }

  function endImagePan() {
    imagePan = null;
    if (!imageViewer) return;
    var stage = imageViewer.querySelector(".image-viewer-stage");
    if (stage) stage.classList.remove("panning");
  }

  // 灯箱复制：图片直接复制像素；Mermaid 图示序列化后栅格化成 PNG 再复制。
  function copyViewerContent(btn) {
    if (!imageViewer) return;
    if (imageViewerKind === "svg" && imageViewerSvg) {
      copySvgAsPng(imageViewerSvg, btn);
      return;
    }
    copyImage(imageViewer.querySelector(".image-viewer-image"), btn);
  }

  function copySvgAsPng(svg, btn) {
    svgToPngBlob(svg).then(function (png) {
      if (!navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem) {
        throw new Error("浏览器不支持图片剪贴板 API");
      }
      return navigator.clipboard.write([new window.ClipboardItem({ "image/png": png })]);
    }).then(function () {
      if (btn) flash(btn, "已复制");
      else toast("图示已复制");
    }).catch(function (err) {
      toast((err && err.message) || "图示复制失败");
    });
  }

  function svgToPngBlob(svg) {
    return new Promise(function (resolve, reject) {
      var w = imageViewerNatural.w, h = imageViewerNatural.h;
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
          canvas.toBlob(function (blob) {
            if (blob) resolve(blob);
            else reject(new Error("无法编码图片"));
          }, "image/png");
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

  function onContentImageClick(e) {
    var img = e.target.closest && e.target.closest(".image-block img");
    if (!img || !inRenderableRoot(img)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    openImageViewer(img);
  }

  // ============================================================
  // 4) 代码块双击高亮（VSCode 风格）
  // ============================================================
  function getWordAtPoint(x, y) {
    var pos = document.caretRangeFromPoint ? document.caretRangeFromPoint(x, y) : null;
    if (!pos) return "";
    var node = pos.startContainer;
    if (node.nodeType !== 3) {
      var tt = (node.textContent || "").trim();
      return /^[A-Za-z_]\w*$/.test(tt) ? tt : "";
    }
    var text = node.textContent;
    var s = pos.startOffset, en = s;
    while (s > 0 && /\w/.test(text[s - 1])) s--;
    while (en < text.length && /\w/.test(text[en])) en++;
    return text.slice(s, en);
  }

  function clearVarHighlights() {
    if (!content) return;
    content.querySelectorAll(".var-highlight").forEach(function (sp) {
      var p = sp.parentNode;
      if (!p) return;
      p.replaceChild(document.createTextNode(sp.textContent), sp);
      p.normalize();
    });
  }

  function highlightToken(block, word) {
    block.normalize();
    var re;
    try { re = new RegExp("(?<!\\w)" + escapeReg(word) + "(?!\\w)", "g"); }
    catch (e) { re = new RegExp(escapeReg(word), "g"); }
    var walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, null);
    var nodes = [], n;
    while ((n = walker.nextNode())) nodes.push(n);
    var first = true;
    nodes.forEach(function (tn) {
      var text = tn.nodeValue;
      if (text.indexOf(word) < 0) return;
      re.lastIndex = 0;
      var frag = document.createDocumentFragment();
      var last = 0, m, matched = false;
      while ((m = re.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var span = document.createElement("span");
        span.className = "var-highlight" + (first ? " current" : "");
        span.textContent = m[0];
        frag.appendChild(span);
        matched = true; first = false;
        last = m.index + m[0].length;
        if (m[0].length === 0) re.lastIndex++;
      }
      if (!matched) return;
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (tn.parentNode) tn.parentNode.replaceChild(frag, tn);
    });
    var cur = block.querySelector(".var-highlight.current");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  function onDblClick(e) {
    var block = e.target.closest && e.target.closest(".codehilite, pre");
    if (!block || !content.contains(block)) return;
    var word = getWordAtPoint(e.clientX, e.clientY);
    clearVarHighlights();
    if (!word || !/^[A-Za-z_]\w*$/.test(word)) return;
    highlightToken(block, word);
    e.stopPropagation();
  }

  // ============================================================
  // 代码复制按钮
  // ============================================================
  function copyCode(btn) {
    var box = btn.closest(".code-block-wrapper") || btn.closest(".codehilite") || btn.closest("pre");
    if (!box) return;
    var pre = box.querySelector("pre") || box;
    var clone = pre.cloneNode(true);
    clone.querySelectorAll(".code-copy-float, .code-copy-btn").forEach(function (b) { b.remove(); });
    var text = (clone.textContent || "").replace(/\n+$/, "");
    writeClipboard(text);
    flash(btn, "已复制");
  }
  function addCodeCopyButtons(root) {
    root.querySelectorAll(".codehilite").forEach(function (box) {
      if (box.closest(".code-block-wrapper")) return;        // 已有头部按钮
      if (box.querySelector(".code-copy-float")) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy-float";
      btn.setAttribute("data-copy-action", "code");
      btn.textContent = "复制";
      box.appendChild(btn);
    });
  }

  function inRenderableRoot(el) {
    if (!el) return false;
    if (content && content.contains(el)) return true;
    var elive = editorLive || $("editorLive");
    return !!(elive && elive.contains(el));
  }

  function onContentActionClick(e) {
    var mermaidButton = e.target.closest && e.target.closest("[data-mermaid-action]");
    if (mermaidButton && inRenderableRoot(mermaidButton)) {
      e.preventDefault();
      e.stopPropagation();
      var mAction = mermaidButton.getAttribute("data-mermaid-action");
      var mBlock = mermaidButton.closest(".mermaid-block");
      if (mAction === "toggle") toggleMermaid(mBlock);
      else if (mAction === "zoom") zoomMermaid(mBlock);
      return;
    }
    var btn = e.target.closest && e.target.closest("[data-copy-action]");
    if (!btn || !inRenderableRoot(btn)) return;
    var action = btn.getAttribute("data-copy-action");
    if (action !== "latex" && action !== "code" && action !== "image") return;
    e.preventDefault();
    e.stopPropagation();
    if (action === "latex") copyLatex(btn);
    else if (action === "code") copyCode(btn);
    else {
      var block = btn.closest(".image-block");
      copyImage(block && block.querySelector("img"), btn);
    }
  }

  // ============================================================
  // 5) 目录：滚动高亮 + 点击跳转
  // ============================================================
  function collectHeadings() {
    headings = Array.prototype.slice.call(
      content.querySelectorAll("h1[id],h2[id],h3[id],h4[id]")
    );
  }

  // 仅当激活项滚出侧栏可视区时才把它滚进来——避免每次 spy 都 scrollIntoView 造成抖动
  function ensureTocVisible(link) {
    if (!sidebar) return;
    var lr = link.getBoundingClientRect(), sr = sidebar.getBoundingClientRect();
    if (lr.top < sr.top + 6 || lr.bottom > sr.bottom - 6) {
      link.scrollIntoView({ block: "nearest" });
    }
  }

  var activeLink = null;
  function setActiveToc(id) {
    if (!toc) return;
    var link = id ? toc.querySelector('a[href="#' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]') : null;
    if (link === activeLink) return;             // 无变化则不动，杜绝重复 class 抖动
    if (activeLink) activeLink.classList.remove("active");
    activeLink = link;
    if (link) { link.classList.add("active"); ensureTocVisible(link); }
  }

  var spyTick = false, spyLock = false, spyLockTimer = null;
  function lockSpy() {                            // 平滑滚动期间锁住 spy，避免高亮反复跳动闪烁
    spyLock = true;
    clearTimeout(spyLockTimer);
    spyLockTimer = setTimeout(function () { spyLock = false; }, 1500);
  }
  function unlockSpy() { spyLock = false; clearTimeout(spyLockTimer); }

  function onScrollSpy() {
    if (spyLock || spyTick) return;
    spyTick = true;
    requestAnimationFrame(function () {
      spyTick = false;
      if (!headings.length) return;
      var top = main.getBoundingClientRect().top + 90;
      var activeId = headings[0].id;
      for (var i = 0; i < headings.length; i++) {
        if (headings[i].getBoundingClientRect().top <= top) activeId = headings[i].id;
        else break;
      }
      setActiveToc(activeId);
    });
  }

  function scrollToHeading(id) {
    if (!id) return;
    var h = null;
    // 编辑态：标题在 #editorLive 内（与隐藏的 #content 可能同 id）
    if (editMode && editorLive) {
      try {
        h = editorLive.querySelector("#" + (window.CSS && CSS.escape ? CSS.escape(id) : id.replace(/([^a-zA-Z0-9\-_])/g, "\\$1")));
      } catch (e) { h = null; }
    }
    if (!h) h = document.getElementById(id);
    if (h) h.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function onTocClick(e) {
    var a = e.target.closest("a");
    if (!a || !toc.contains(a)) return;
    var href = a.getAttribute("href") || "";
    if (href.charAt(0) !== "#") return;
    e.preventDefault();
    var id = decodeURIComponent(href.slice(1));
    lockSpy();                                   // 先锁 spy，再平滑滚动 + 一次性设激活
    setActiveToc(id);
    scrollToHeading(id);
    if (app.classList.contains("drawer")) closeDrawer();
  }

  // ============================================================
  // 5) 搜索
  // ============================================================
  var searchBar, searchInput, searchCount, searchHits = [], searchIdx = -1, searchTimer = null;

  function clearSearch() {
    if (!content) return;
    content.querySelectorAll("mark.search-hit").forEach(function (m) {
      var p = m.parentNode; if (!p) return;
      p.replaceChild(document.createTextNode(m.textContent), m);
      p.normalize();
    });
    searchHits = []; searchIdx = -1;
    if (searchCount) searchCount.textContent = "";
  }

  function runSearch(query) {
    clearSearch();
    if (!query) return;
    clearVarHighlights();
    var lower = query.toLowerCase();
    var walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        var p = node.parentNode;
        if (p && (p.tagName === "SCRIPT" || p.tagName === "STYLE")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var textNodes = [], nn;
    while ((nn = walker.nextNode())) textNodes.push(nn);

    textNodes.forEach(function (tn) {
      var text = tn.nodeValue;
      var hay = text.toLowerCase();
      var idx = hay.indexOf(lower);
      if (idx < 0) return;
      var frag = document.createDocumentFragment();
      var last = 0;
      while (idx >= 0) {
        if (idx > last) frag.appendChild(document.createTextNode(text.slice(last, idx)));
        var mk = document.createElement("mark");
        mk.className = "search-hit";
        mk.textContent = text.slice(idx, idx + query.length);
        frag.appendChild(mk);
        searchHits.push(mk);
        last = idx + query.length;
        idx = hay.indexOf(lower, last);
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (tn.parentNode) tn.parentNode.replaceChild(frag, tn);
    });

    if (searchHits.length) { searchIdx = 0; focusHit(); }
    updateSearchCount();
  }

  function updateSearchCount() {
    if (!searchCount) return;
    searchCount.textContent = searchHits.length
      ? (searchIdx + 1) + "/" + searchHits.length
      : "无结果";
  }

  function focusHit() {
    searchHits.forEach(function (m) { m.classList.remove("current"); });
    var m = searchHits[searchIdx];
    if (m) { m.classList.add("current"); m.scrollIntoView({ block: "center" }); }
    updateSearchCount();
  }

  function nextHit(dir) {
    if (!searchHits.length) return;
    searchIdx = (searchIdx + dir + searchHits.length) % searchHits.length;
    focusHit();
  }

  function openSearch() {
    searchBar.classList.add("open");
    $("searchBtn").classList.add("active");
    searchInput.focus(); searchInput.select();
  }
  function closeSearch() {
    searchBar.classList.remove("open");
    $("searchBtn").classList.remove("active");
    clearSearch();
  }

  // ============================================================
  // 侧栏 / 抽屉 / 主题 / 窗口
  // ============================================================
  function closeDrawer() { app.classList.remove("sidebar-open"); }

  function toggleSidebar() {
    if (app.classList.contains("drawer")) {
      app.classList.toggle("sidebar-open");
    } else {
      app.classList.toggle("sidebar-hidden");
    }
  }

  function onResize() {
    var drawer = window.innerWidth < 760;
    app.classList.toggle("drawer", drawer);
    // 抽屉态用 .sidebar-open 控制侧栏；.sidebar-hidden 只属于宽屏态，二者互斥，
    // 否则 .sidebar-hidden 的 display:none 会让抽屉永远拉不出来。
    if (drawer) app.classList.remove("sidebar-hidden");
    else app.classList.remove("sidebar-open");
    var a = api();
    if (a && a.win_is_maximized) {
      a.win_is_maximized().then(function (maximized) {
        document.documentElement.classList.toggle("window-maximized", !!maximized);
      });
    }
  }

  function setTheme(t, persist) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("inkwell-theme", t); } catch (e) {}
    if (persist !== false) savePreference("theme", t);
    var link = $("pygments-style");
    if (link) link.href = "/assets/pygments-" + (t === "dark" ? "dark" : "light") + ".css";
    refreshMermaidDiagrams();
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    setTheme(cur === "dark" ? "light" : "dark");
  }

  function setDocTitle(title) {
    if (docTitle) docTitle.textContent = title || "";
    document.title = title ? (title + " — Inkwell") : "Inkwell";
  }

  // ============================================================
  // 原生窗口行为：8 向缩放 + 标题栏拖动（Aero Snap 并列 / 拖回还原）
  // 通过 js_api 调 Win32 ReleaseCapture()+SendMessage(WM_NCLBUTTONDOWN,...)
  // ============================================================
  var RH = { n: "top", s: "bottom", w: "left", e: "right",
             nw: "topleft", ne: "topright", sw: "bottomleft", se: "bottomright" };

  function setupWindowResize() {
    Object.keys(RH).forEach(function (k) {
      var h = document.createElement("div");
      h.className = "resize-handle rh-" + k;
      h.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        var a = api(); if (a && a.win_native_resize) a.win_native_resize(RH[k]);
      });
      document.body.appendChild(h);
    });
  }

  function setupWindowDrag() {
    var bar = document.querySelector(".titlebar");
    if (!bar) return;
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
  var FONT_MIN = 10, FONT_MAX = 26, FONT_DEFAULT = 15;
  function readerFont() {
    var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--reader-font"));
    return isNaN(v) ? FONT_DEFAULT : v;
  }
  function setReaderFont(px, persist) {
    px = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px * 2) / 2));
    document.documentElement.style.setProperty("--reader-font", px + "px");
    try { localStorage.setItem("inkwell-font", px); } catch (e) {}
    if (persist !== false) savePreference("font", px);
  }
  function setupFontZoom(preferredFont) {
    var saved = parseFloat(preferredFont);
    if (isNaN(saved)) saved = parseFloat(localStorage.getItem("inkwell-font"));
    if (!isNaN(saved)) setReaderFont(saved, false);
    // Ctrl+滚轮：放大/缩小正文（拦截 WebView2 的整页缩放）
    window.addEventListener("wheel", function (e) {
      if (!e.ctrlKey) return;
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
  function setupResizer() {
    var rz = document.createElement("div");
    rz.className = "sidebar-resizer";
    app.appendChild(rz);
    var dragging = false;
    rz.addEventListener("mousedown", function (e) { dragging = true; e.preventDefault(); document.body.style.cursor = "col-resize"; });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var w = Math.min(460, Math.max(180, e.clientX));
      document.documentElement.style.setProperty("--sidebar-w", w + "px");
    });
    window.addEventListener("mouseup", function () { dragging = false; document.body.style.cursor = ""; });
  }

  // ============================================================
  // 内容初始化（首帧 + 每次换文件）
  // ============================================================
  function initContent() {
    clearSearch();
    clearVarHighlights();
    renderMath(content);
    addCodeCopyButtons(content);
    initMermaidDiagrams(content);
    setupImageCopySupport(content);
    collectHeadings();
    activeLink = null;          // 换文件后旧目录链接失效，重置激活引用
    unlockSpy();
    onScrollSpy();
  }

  // ============================================================
  // 编辑模式：Obsidian 风格 Live Preview
  // Markdown 全文为真相；未激活块显示最终渲染，点击块后就地编辑源码
  // ============================================================
  var editMode = false;
  var editDirty = false;
  var editBaseline = "";
  var editMtimeNs = null;
  var editPath = null;
  var editPreviewTimer = null;
  var editPreviewSeq = 0;
  var editEnterGen = 0;
  var editSaveGen = 0;
  var editSaving = false;
  var editorSource = null;
  var editorLive = null;
  var editorLiveWrap = null;
  var editorPane = null;
  var editorStatus = null;
  var lpBlocks = [];
  var activeBlockIdx = -1;
  var lastHtmlParts = [];
  var lpMarkerPrefix = "<!--inkwell-lp-block:";
  var lpMarkerSuffix = "-->";
  var lpActivating = false;

  function setEditStatus(msg, kind) {
    if (!editorStatus) return;
    editorStatus.textContent = msg || "";
    editorStatus.classList.remove("warn", "ok");
    if (kind) editorStatus.classList.add(kind);
  }

  function updateDirtyUI() {
    if (docTitle) {
      if (editDirty) docTitle.classList.add("dirty");
      else docTitle.classList.remove("dirty");
    }
    var btn = $("editBtn");
    if (btn) {
      btn.classList.toggle("active", editMode);
      btn.setAttribute("aria-pressed", editMode ? "true" : "false");
      btn.title = editMode
        ? (editDirty ? "退出编辑（有未保存更改）" : "退出编辑模式 (Ctrl+E)")
        : "编辑模式 (Ctrl+E)";
    }
    var saveBtn = $("editSaveBtn");
    if (saveBtn) saveBtn.classList.toggle("primary", editDirty);
  }

  function setDirty(next) {
    editDirty = !!next;
    updateDirtyUI();
  }

  function markDirtyFromBuffer() {
    if (!editMode || !editorSource) return;
    setDirty(editorSource.value !== editBaseline);
  }

  // —— 围栏 / 行内代码保护：找图片、插片段时不能破坏代码块边界 ——
  function findProtectedRanges(text) {
    var ranges = [];
    var i = 0, n = text.length;
    while (i < n) {
      if ((i === 0 || text.charAt(i - 1) === "\n")) {
        var j = i;
        var spaces = 0;
        while (spaces < 3 && j < n && text.charAt(j) === " ") { spaces++; j++; }
        var ch = text.charAt(j);
        if (ch === "`" || ch === "~") {
          var marker = ch;
          var openLen = 0;
          while (j < n && text.charAt(j) === marker) { openLen++; j++; }
          if (openLen >= 3) {
            while (j < n && text.charAt(j) !== "\n") j++;
            if (j < n && text.charAt(j) === "\n") j++;
            var closed = false;
            while (j < n) {
              var lineStart = j;
              var ls = 0;
              while (ls < 3 && j < n && text.charAt(j) === " ") { ls++; j++; }
              var closeLen = 0;
              while (j < n && text.charAt(j) === marker) { closeLen++; j++; }
              if (closeLen >= openLen) {
                var k = j;
                while (k < n && (text.charAt(k) === " " || text.charAt(k) === "\t")) k++;
                if (k >= n || text.charAt(k) === "\n") {
                  while (k < n && text.charAt(k) !== "\n") k++;
                  if (k < n && text.charAt(k) === "\n") k++;
                  ranges.push({ start: i, end: k, kind: "fence" });
                  i = k;
                  closed = true;
                  break;
                }
              }
              j = lineStart;
              while (j < n && text.charAt(j) !== "\n") j++;
              if (j < n && text.charAt(j) === "\n") j++;
            }
            if (closed) continue;
            ranges.push({ start: i, end: n, kind: "fence" });
            break;
          }
        }
      }
      if (text.charAt(i) === "`") {
        var ticks = 0, t = i;
        while (t < n && text.charAt(t) === "`") { ticks++; t++; }
        if (ticks > 0) {
          var close = text.indexOf(new Array(ticks + 1).join("`"), t);
          if (close !== -1) {
            var mid = text.slice(t, close);
            if (mid.indexOf("\n") === -1) {
              ranges.push({ start: i, end: close + ticks, kind: "inline" });
              i = close + ticks;
              continue;
            }
          }
        }
      }
      i++;
    }
    return ranges;
  }

  function inProtectedRange(ranges, pos) {
    for (var r = 0; r < ranges.length; r++) {
      if (pos >= ranges[r].start && pos < ranges[r].end) return ranges[r];
    }
    return null;
  }

  function findImageAt(text, pos) {
    var ranges = findProtectedRanges(text);
    var re = /!\[([^\]]*)\]\(/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      var start = m.index;
      var destStart = re.lastIndex;
      if (inProtectedRange(ranges, start)) continue;
      var depth = 1;
      var p = destStart;
      var inAngle = false;
      var quote = null;
      while (p < text.length && depth > 0) {
        var c = text.charAt(p);
        if (quote) {
          if (c === quote) quote = null;
          p++;
          continue;
        }
        if (inAngle) {
          if (c === ">") inAngle = false;
          p++;
          continue;
        }
        if (c === "<") { inAngle = true; p++; continue; }
        if (c === '"' || c === "'") { quote = c; p++; continue; }
        if (c === "(") { depth++; p++; continue; }
        if (c === ")") {
          depth--;
          p++;
          if (depth === 0) break;
          continue;
        }
        if (c === "\n" && depth === 1 && !quote && !inAngle) break;
        p++;
      }
      if (depth !== 0) continue;
      var end = p;
      if (pos >= start && pos <= end) {
        return {
          start: start,
          end: end,
          alt: m[1],
          dest: text.slice(destStart, end - 1),
          markdown: text.slice(start, end)
        };
      }
      re.lastIndex = end;
    }
    return null;
  }

  // —— 块切分：空行分隔，围栏 / $$ 公式视为原子块 ——
  function _atLineStart(text, pos) {
    return pos === 0 || text.charAt(pos - 1) === "\n";
  }

  function _matchFenceEnd(text, pos) {
    if (!_atLineStart(text, pos)) return null;
    var n = text.length;
    var j = pos, spaces = 0;
    while (spaces < 3 && j < n && text.charAt(j) === " ") { spaces++; j++; }
    var ch = text.charAt(j);
    if (ch !== "`" && ch !== "~") return null;
    var openLen = 0;
    while (j < n && text.charAt(j) === ch) { openLen++; j++; }
    if (openLen < 3) return null;
    while (j < n && text.charAt(j) !== "\n") j++;
    if (j < n && text.charAt(j) === "\n") j++;
    while (j < n) {
      var lineStart = j;
      var ls = 0;
      while (ls < 3 && j < n && text.charAt(j) === " ") { ls++; j++; }
      var closeLen = 0;
      while (j < n && text.charAt(j) === ch) { closeLen++; j++; }
      if (closeLen >= openLen) {
        var k = j;
        while (k < n && (text.charAt(k) === " " || text.charAt(k) === "\t")) k++;
        if (k >= n || text.charAt(k) === "\n") {
          while (k < n && text.charAt(k) !== "\n") k++;
          if (k < n && text.charAt(k) === "\n") k++;
          return k;
        }
      }
      j = lineStart;
      while (j < n && text.charAt(j) !== "\n") j++;
      if (j < n && text.charAt(j) === "\n") j++;
    }
    return n;
  }

  function _matchMathEnd(text, pos) {
    if (!_atLineStart(text, pos)) return null;
    var n = text.length;
    var j = pos;
    while (j < n && (text.charAt(j) === " " || text.charAt(j) === "\t")) j++;
    if (text.slice(j, j + 2) !== "$$") return null;
    j += 2;
    var lineEnd = j;
    while (lineEnd < n && text.charAt(lineEnd) !== "\n") lineEnd++;
    var after = text.slice(j, lineEnd).replace(/\s+$/, "");
    if (after.length >= 2 && after.slice(-2) === "$$") {
      var end = lineEnd;
      if (end < n && text.charAt(end) === "\n") end++;
      return end;
    }
    if (lineEnd < n && text.charAt(lineEnd) === "\n") j = lineEnd + 1;
    else j = lineEnd;
    while (j < n) {
      var ls = j;
      while (j < n && text.charAt(j) !== "\n") j++;
      var line = text.slice(ls, j).replace(/^\s+|\s+$/g, "");
      if (line === "$$") {
        if (j < n && text.charAt(j) === "\n") j++;
        return j;
      }
      if (j < n && text.charAt(j) === "\n") j++;
    }
    return n;
  }

  function classifyBlockKind(raw) {
    var t = (raw || "").replace(/^\s+/, "");
    if (/^(`{3,}|~{3,})/.test(t)) {
      if (/^(`{3,}|~{3,})\s*mermaid\b/i.test(t)) return "fence-mermaid";
      return "fence";
    }
    if (/^\$\$/.test(t)) return "math";
    // ATX 标题
    var hm = /^(#{1,6})\s+\S/.exec(t);
    if (hm) return "heading:" + hm[1].length;
    // GFM 表格：至少表头 + 分隔行，且多行含 |
    var lines = t.split("\n").filter(function (l) { return l.replace(/^\s+|\s+$/g, "").length; });
    if (lines.length >= 2) {
      var pipeLines = 0;
      for (var li = 0; li < lines.length; li++) {
        if (lines[li].indexOf("|") >= 0) pipeLines++;
      }
      // GFM 分隔行：| --- | :---: | 或 ---|---
      if (pipeLines >= 2 && /^\s*\|?[\t \-:|]+\|?[\t \-:|]*$/.test(lines[1])
          && /[-:]/.test(lines[1])) {
        return "table";
      }
    }
    // 列表
    if (/^([-*+]|\d+\.)\s+/.test(t)) return "list";
    return "text";
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

  function splitMarkdownBlocks(text) {
    text = text == null ? "" : String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    var n = text.length;
    var blocks = [];
    var i = 0;
    while (i < n) {
      while (i < n && text.charAt(i) === "\n") i++;
      if (i >= n) break;
      var start = i;
      var fenceEnd = _matchFenceEnd(text, i);
      if (fenceEnd != null) {
        blocks.push({ text: text.slice(start, fenceEnd).replace(/\n+$/, "") });
        i = fenceEnd;
        continue;
      }
      var mathEnd = _matchMathEnd(text, i);
      if (mathEnd != null) {
        blocks.push({ text: text.slice(start, mathEnd).replace(/\n+$/, "") });
        i = mathEnd;
        continue;
      }
      var j = i;
      while (j < n) {
        if (text.charAt(j) === "\n") {
          var k = j + 1;
          if (k >= n || text.charAt(k) === "\n") break;
          if (_matchFenceEnd(text, k) != null || _matchMathEnd(text, k) != null) break;
        }
        j++;
      }
      var raw = text.slice(start, j).replace(/\n+$/, "");
      blocks.push({ text: raw });
      i = j;
      while (i < n && text.charAt(i) === "\n") i++;
    }
    if (!blocks.length) blocks.push({ text: "" });
    return blocks;
  }

  function joinMarkdownBlocks(blocks) {
    if (!blocks || !blocks.length) return "";
    return blocks.map(function (b) { return b.text || ""; }).join("\n\n");
  }

  function bufferWithMarkers(blocks) {
    var parts = [];
    for (var i = 0; i < blocks.length; i++) {
      if (i > 0) parts.push("\n\n" + lpMarkerPrefix + i + lpMarkerSuffix + "\n\n");
      parts.push(blocks[i].text || "");
    }
    return parts.join("");
  }

  function splitPreviewHtml(html, count) {
    html = html || "";
    var re = /<!--inkwell-lp-block:(\d+)-->/g;
    var parts = [];
    var last = 0;
    var m;
    while ((m = re.exec(html)) !== null) {
      parts.push(html.slice(last, m.index));
      last = m.index + m[0].length;
    }
    parts.push(html.slice(last));
    while (parts.length < count) parts.push("");
    if (parts.length > count) {
      var head = parts.slice(0, count - 1);
      var tail = parts.slice(count - 1).join("");
      parts = head.concat([tail]);
    }
    return parts;
  }

  function syncBufferFromBlocks() {
    if (!editorSource) return;
    editorSource.value = joinMarkdownBlocks(lpBlocks);
    markDirtyFromBuffer();
  }

  function getActiveTextarea() {
    if (!editorLive || activeBlockIdx < 0) return null;
    // 必须用 data-idx 对齐，避免 DOM .is-active 与 activeBlockIdx 竞态错配
    return editorLive.querySelector(
      '.lp-block[data-idx="' + activeBlockIdx + '"] .lp-source'
    );
  }

  function flushActiveBlock() {
    var ta = getActiveTextarea();
    // 兜底：索引失效时仍从 DOM 读孤立 textarea
    if (!ta && editorLive) {
      ta = editorLive.querySelector("textarea.lp-source");
    }
    if (!ta) return;
    var idx = activeBlockIdx >= 0 ? activeBlockIdx : resolveBlockIdxFromEl(ta);
    if (idx < 0 || !lpBlocks[idx]) return;
    lpBlocks[idx].text = ta.value;
    syncBufferFromBlocks();
  }

  function invalidateLiveRender() {
    // 让进行中的 preview 回调失效，避免把用户新激活的块盖掉
    editPreviewSeq += 1;
  }

  function getEditText() {
    flushActiveBlock();
    return editorSource ? editorSource.value : "";
  }

  function setEditText(t, opts) {
    opts = opts || {};
    if (!editorSource) return;
    var text = t == null ? "" : String(t);
    editorSource.value = text;
    lpBlocks = splitMarkdownBlocks(text);
    activeBlockIdx = opts.activate == null ? activeBlockIdx : opts.activate;
    if (activeBlockIdx >= lpBlocks.length) activeBlockIdx = lpBlocks.length - 1;
    markDirtyFromBuffer();
    if (opts.render !== false) runLiveRender({ force: true });
  }

  function isStructuredKind(kind) {
    if (!kind) return false;
    return kind === "table" || kind === "math" || kind.indexOf("fence") === 0;
  }

  function autosizeTextarea(ta) {
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

  function teardownActiveTextarea(opts) {
    // 拆掉孤立 textarea。opts.skipWrite：结构已 re-split 后禁止把旧全文写回新块
    opts = opts || {};
    if (!editorLive) return;
    if (!opts.skipWrite) {
      var tas = editorLive.querySelectorAll(".lp-block.is-active .lp-source, textarea.lp-source");
      for (var i = 0; i < tas.length; i++) {
        var ta = tas[i];
        var idx = resolveBlockIdxFromEl(ta);
        if (idx >= 0 && lpBlocks[idx]) {
          lpBlocks[idx].text = ta.value;
        }
      }
      if (tas.length) syncBufferFromBlocks();
    }
    var actives = editorLive.querySelectorAll(".lp-block.is-active");
    for (var j = 0; j < actives.length; j++) {
      var idx2 = parseInt(actives[j].dataset.idx, 10);
      if (!isNaN(idx2) && lpBlocks[idx2]) {
        fillBlockView(actives[j], idx2, { preferText: !!opts.skipWrite, preserveHeight: true });
      } else {
        // 索引失效：直接拆掉 DOM，不写缓冲
        actives[j].classList.remove("is-active");
        actives[j].innerHTML = "";
      }
    }
    // 仍残留的 textarea 强制移除
    var leftovers = editorLive.querySelectorAll("textarea.lp-source");
    for (var k = 0; k < leftovers.length; k++) {
      var host = leftovers[k].closest(".lp-block");
      if (host) {
        host.classList.remove("is-active");
        var hi = parseInt(host.dataset.idx, 10);
        if (!isNaN(hi) && lpBlocks[hi]) fillBlockView(host, hi, { preferText: true });
        else host.innerHTML = "";
      }
    }
  }

  // —— 滚动锚点：全页重挂 / 激活块时避免跳到顶部 ——
  var pendingEnterScroll = null; // 进入编辑时从阅读区继承的 scrollTop

  function captureLiveScrollAnchor() {
    if (!editorLiveWrap || !editorLive) return null;
    var wrap = editorLiveWrap;
    var wrapRect = wrap.getBoundingClientRect();
    var blocks = editorLive.querySelectorAll(".lp-block");
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
    if (!anchor || !editorLiveWrap) return;
    if (anchor.idx >= 0 && !isNaN(anchor.idx)) {
      var el = getBlockEl(anchor.idx);
      if (el) {
        var wrapRect = editorLiveWrap.getBoundingClientRect();
        var r = el.getBoundingClientRect();
        editorLiveWrap.scrollTop += (r.top - wrapRect.top) - anchor.offset;
        return;
      }
    }
    editorLiveWrap.scrollTop = anchor.scrollTop;
  }

  function withLiveScroll(fn) {
    var anchor = captureLiveScrollAnchor();
    fn();
    restoreLiveScrollAnchor(anchor);
    requestAnimationFrame(function () {
      restoreLiveScrollAnchor(anchor);
    });
  }

  function getBlockEl(idx) {
    if (!editorLive || idx < 0) return null;
    return editorLive.querySelector('.lp-block[data-idx="' + idx + '"]');
  }

  function focusNoScroll(el) {
    if (!el) return;
    var top = editorLiveWrap ? editorLiveWrap.scrollTop : 0;
    try {
      if (typeof el.focus === "function") el.focus({ preventScroll: true });
    } catch (e) {
      try { el.focus(); } catch (e2) {}
      if (editorLiveWrap) editorLiveWrap.scrollTop = top;
    }
    if (editorLiveWrap) editorLiveWrap.scrollTop = top;
  }

  function fillBlockView(blockEl, idx, opts) {
    opts = opts || {};
    if (!blockEl) return;
    var holdH = opts.holdHeight || 0;
    if (!holdH && opts.preserveHeight) {
      holdH = Math.ceil(blockEl.getBoundingClientRect().height) || 0;
    }
    var kind = classifyBlockKind(lpBlocks[idx] ? lpBlocks[idx].text : "");
    applyBlockKindClass(blockEl, kind);
    blockEl.classList.remove("is-active");
    delete blockEl.dataset.lpKindSticky;
    blockEl.style.height = "";
    blockEl.style.maxHeight = "";
    if (holdH > 0) blockEl.style.minHeight = holdH + "px";
    else blockEl.style.minHeight = "";
    blockEl.innerHTML = "";
    var view = document.createElement("div");
    view.className = "lp-view";
    var part = (lastHtmlParts && lastHtmlParts[idx]) || "";
    var raw = (lpBlocks[idx] && lpBlocks[idx].text) || "";
    var trimmed = raw.replace(/^\s+|\s+$/g, "");
    // 源码已改且缓存 HTML 可能过期：先显示纯文本，避免闪旧渲染
    if (opts.preferText && trimmed) {
      view.textContent = raw;
    } else if (!part.replace(/^\s+|\s+$/g, "") && !trimmed) {
      view.innerHTML = "<p class='lp-empty-hint'>点击此处开始写作…</p>";
    } else if (!part.replace(/^\s+|\s+$/g, "") && trimmed) {
      view.textContent = raw;
    } else {
      view.innerHTML = part;
    }
    blockEl.appendChild(view);
    if (!opts.preferText) initPreviewContent(view);
    if (holdH > 0 && !opts.keepHold) {
      // 下一帧若内容已有自然高度则释放锁
      requestAnimationFrame(function () {
        if (!blockEl.classList.contains("is-active")) {
          blockEl.style.minHeight = "";
        }
      });
    }
  }

  function activateBlockInPlace(idx, opts) {
    opts = opts || {};
    var blockEl = getBlockEl(idx);
    if (!blockEl || !lpBlocks[idx]) return false;
    var view = blockEl.querySelector(".lp-view");
    var minH = 0;
    if (view) {
      minH = Math.ceil(view.getBoundingClientRect().height);
    } else if (blockEl.getBoundingClientRect) {
      minH = Math.ceil(blockEl.getBoundingClientRect().height);
    }
    var anchor = captureLiveScrollAnchor();
    var kind = classifyBlockKind(lpBlocks[idx].text);
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

    var ta = document.createElement("textarea");
    ta.className = "lp-source";
    ta.spellcheck = false;
    ta.setAttribute("aria-label", "编辑块 " + (idx + 1));
    ta.value = lpBlocks[idx].text || "";
    if (minH > 0) ta.style.minHeight = minH + "px";
    if (isStructuredKind(kind) && minH > 0) {
      ta.style.height = "100%";
      ta.style.overflow = "auto";
    }
    blockEl.appendChild(ta);
    bindActiveTextarea(ta);
    activeBlockIdx = idx;
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
      if (idx >= 0 && lpBlocks[idx]) {
        lpBlocks[idx].text = ta.value;
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
      if (idx < 0 || !lpBlocks[idx]) return;
      lpBlocks[idx].text = ta.value;
      syncBufferFromBlocks();
      // sticky kind：编辑过程中不降级（避免表格 thrash）
      var bel = getBlockEl(idx);
      var sticky = bel && bel.dataset.lpKindSticky;
      if (sticky) applyBlockKindClass(bel, sticky);
      autosizeTextarea(ta);
    });
    ta.addEventListener("keydown", function (e) {
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
        if (tidx >= 0) lpBlocks[tidx].text = ta.value;
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
    });
    ta.addEventListener("blur", function () {
      setTimeout(function () {
        if (!editMode || lpActivating) return;
        if (ta._lpComposing || ta.isComposing) return;
        var ae = document.activeElement;
        if (ae && ae.classList && ae.classList.contains("lp-source")) return;
        if (ae && editorPane && editorPane.contains(ae) && ae !== editorLive && !(editorLive && editorLive.contains(ae))) {
          flushActiveBlock();
          return;
        }
        if (ae && editorLive && editorLive.contains(ae) && ae !== editorLive) return;
        deactivateBlock();
      }, 0);
    });
  }

  function mountLiveDom(htmlParts, opts) {
    opts = opts || {};
    if (!editorLive) return;
    var anchor = captureLiveScrollAnchor();
    // 进入编辑首次渲染：用阅读区滚动位置
    if (pendingEnterScroll != null && (!anchor || anchor.scrollTop === 0)) {
      anchor = { idx: -1, offset: 0, scrollTop: pendingEnterScroll };
    }
    var keepActive = !!opts.keepActive && activeBlockIdx >= 0 && activeBlockIdx < lpBlocks.length;
    var restoreIdx = keepActive ? activeBlockIdx : -1;
    var restoreVal = null;
    var selStart = null, selEnd = null;
    if (restoreIdx >= 0) {
      var oldTa = getActiveTextarea();
      if (oldTa) {
        restoreVal = oldTa.value;
        selStart = oldTa.selectionStart;
        selEnd = oldTa.selectionEnd;
      } else if (lpBlocks[restoreIdx]) {
        restoreVal = lpBlocks[restoreIdx].text;
      }
    }

    lastHtmlParts = htmlParts || lastHtmlParts;
    editorLive.innerHTML = "";
    if (pendingEnterScroll != null && editorLiveWrap) {
      editorLiveWrap.classList.add("lp-scroll-pending");
    }

    for (var i = 0; i < lpBlocks.length; i++) {
      var blockEl = document.createElement("div");
      blockEl.className = "lp-block";
      blockEl.dataset.idx = String(i);
      var kind = classifyBlockKind(lpBlocks[i].text);
      applyBlockKindClass(blockEl, kind);

      if (i === restoreIdx) {
        blockEl.classList.add("is-active");
        var ta = document.createElement("textarea");
        ta.className = "lp-source";
        ta.spellcheck = false;
        ta.setAttribute("aria-label", "编辑块 " + (i + 1));
        ta.value = restoreVal != null ? restoreVal : (lpBlocks[i].text || "");
        blockEl.appendChild(ta);
        bindActiveTextarea(ta);
      } else {
        var view = document.createElement("div");
        view.className = "lp-view";
        var part = (lastHtmlParts && lastHtmlParts[i]) || "";
        var trimmed = (lpBlocks[i].text || "").replace(/^\s+|\s+$/g, "");
        if (!part.replace(/^\s+|\s+$/g, "") && !trimmed) {
          view.innerHTML = "<p class='lp-empty-hint'>点击此处开始写作…</p>";
        } else if (!part.replace(/^\s+|\s+$/g, "") && trimmed) {
          view.textContent = lpBlocks[i].text;
        } else {
          view.innerHTML = part;
        }
        blockEl.appendChild(view);
      }
      editorLive.appendChild(blockEl);
    }

    editorLive.querySelectorAll(".lp-view").forEach(function (v) {
      initPreviewContent(v);
    });

    activeBlockIdx = restoreIdx;
    if (restoreIdx >= 0) {
      var focusTa = getActiveTextarea();
      if (focusTa) {
        autosizeTextarea(focusTa);
        if (selStart != null) {
          try {
            focusTa.selectionStart = selStart;
            focusTa.selectionEnd = selEnd;
          } catch (e) {}
        }
        focusNoScroll(focusTa);
      }
    }
    restoreLiveScrollAnchor(anchor);
    if (pendingEnterScroll != null) {
      if (editorLiveWrap) editorLiveWrap.scrollTop = pendingEnterScroll;
      // 延迟清除，等图片/mermaid 撑开后再补一次
      var enterTarget = pendingEnterScroll;
      pendingEnterScroll = null;
      var applyEnter = function () {
        if (editorLiveWrap && enterTarget != null) editorLiveWrap.scrollTop = enterTarget;
      };
      applyEnter();
      requestAnimationFrame(function () {
        applyEnter();
        if (editorLiveWrap) editorLiveWrap.classList.remove("lp-scroll-pending");
        requestAnimationFrame(applyEnter);
        setTimeout(function () {
          applyEnter();
          if (editorLiveWrap) editorLiveWrap.classList.remove("lp-scroll-pending");
        }, 120);
        setTimeout(applyEnter, 400);
      });
    } else if (editorLiveWrap) {
      editorLiveWrap.classList.remove("lp-scroll-pending");
    }
    requestAnimationFrame(function () {
      if (anchor) restoreLiveScrollAnchor(anchor);
    });
  }

  var lpSingleRefreshSeq = 0;

  function activateBlock(idx, opts) {
    opts = opts || {};
    if (!editMode || idx < 0 || idx >= lpBlocks.length) return;
    if (idx === activeBlockIdx && getActiveTextarea()) {
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
    lpActivating = true;
    var prev = activeBlockIdx;
    flushActiveBlock();

    // 切换块：尽量就地改 DOM，不全页重挂（减少跳动）
    if (prev >= 0 && prev !== idx) {
      var full = editorSource ? editorSource.value : joinMarkdownBlocks(lpBlocks);
      var oldCount = lpBlocks.length;
      lpBlocks = splitMarkdownBlocks(full);
      // 结构未变：只刷新上一块 HTML，再就地激活目标块
      if (lpBlocks.length === oldCount) {
        var prevStill = Math.min(prev, lpBlocks.length - 1);
        if (idx >= lpBlocks.length) idx = lpBlocks.length - 1;
        // 先同步拆掉上一块 textarea（防止孤立）
        var prevEl = getBlockEl(prevStill);
        activeBlockIdx = -1;
        if (prevEl) {
          fillBlockView(prevEl, prevStill, { preferText: true, preserveHeight: true, keepHold: true });
        }
        refreshSingleBlockView(prevStill);
        activateBlockInPlace(idx, opts);
        lpActivating = false;
        setEditStatus("编辑块 " + (idx + 1) + " / " + lpBlocks.length, "");
        return;
      }
      // 结构变化：缓冲已 flush+resplit，拆 DOM 时禁止把旧全文写回新块
      teardownActiveTextarea({ skipWrite: true });
      if (idx >= lpBlocks.length) idx = lpBlocks.length - 1;
      activeBlockIdx = -1;
      lpActivating = false;
      runLiveRender({ afterActivate: idx });
      return;
    }

    // 首次激活：就地替换当前块
    if (!activateBlockInPlace(idx, opts)) {
      activeBlockIdx = -1;
      runLiveRender({ afterActivate: idx });
    }
    lpActivating = false;
    setEditStatus("编辑块 " + (idx + 1) + " / " + lpBlocks.length, "");
  }

  function refreshSingleBlockView(idx) {
    if (idx < 0 || idx >= lpBlocks.length) return;
    var a = api();
    if (!a || !a.preview_markdown) return;
    var text = (lpBlocks[idx] && lpBlocks[idx].text) || "";
    var token = ++lpSingleRefreshSeq;
    var expected = text;
    a.preview_markdown(text || "\n", editPath || currentPath).then(function (p) {
      if (!editMode || token !== lpSingleRefreshSeq) return;
      if (!p || p.ok === false) {
        var el0 = getBlockEl(idx);
        if (el0 && activeBlockIdx !== idx) {
          fillBlockView(el0, idx, { preferText: true });
        }
        return;
      }
      // 源码在请求期间又改了则丢弃
      if ((lpBlocks[idx] && lpBlocks[idx].text) !== expected) return;
      if (!lastHtmlParts) lastHtmlParts = [];
      lastHtmlParts[idx] = p.content || "";
      if (activeBlockIdx === idx) return;
      var el = getBlockEl(idx);
      if (el) {
        var hold = Math.ceil(el.getBoundingClientRect().height) || 0;
        fillBlockView(el, idx, { holdHeight: hold });
      }
    }).catch(function () {
      if (!editMode || token !== lpSingleRefreshSeq) return;
      var el1 = getBlockEl(idx);
      if (el1 && activeBlockIdx !== idx) fillBlockView(el1, idx, { preferText: true });
    });
  }

  function deactivateBlock() {
    if (activeBlockIdx < 0) {
      teardownActiveTextarea();
      return;
    }
    var ta = getActiveTextarea();
    if (ta && (ta._lpComposing || ta.isComposing)) return;
    invalidateLiveRender();
    var idx = activeBlockIdx;
    flushActiveBlock();
    var full = editorSource ? editorSource.value : joinMarkdownBlocks(lpBlocks);
    var oldCount = lpBlocks.length;
    lpBlocks = splitMarkdownBlocks(full);
    var el = getBlockEl(idx);
    var holdH = el ? Math.ceil(el.getBoundingClientRect().height) : 0;
    activeBlockIdx = -1;

    // 块数不变：就地恢复该块最终渲染，避免整页跳动
    if (lpBlocks.length === oldCount && getBlockEl(idx)) {
      var anchor = captureLiveScrollAnchor();
      el = getBlockEl(idx);
      // 保持高度直到新预览回来，避免二次跳动
      if (el) fillBlockView(el, idx, { preferText: true, holdHeight: holdH, keepHold: true });
      restoreLiveScrollAnchor(anchor);
      refreshSingleBlockView(idx);
      var a = api();
      if (a && a.preview_markdown) {
        a.preview_markdown(editorSource.value, editPath || currentPath).then(function (p2) {
          if (!editMode) return;
          if (toc && p2 && p2.toc != null) toc.innerHTML = p2.toc || "";
        }).catch(function () {});
      }
      return;
    }
    // 已 re-split：只拆 DOM，不要把旧 ta 全文写回
    teardownActiveTextarea({ skipWrite: true });
    runLiveRender({});
  }

  function initPreviewContent(root) {
    if (!root) return;
    renderMath(root);
    addCodeCopyButtons(root);
    initMermaidDiagrams(root);
    setupImageCopySupport(root);
  }

  function applyLivePayload(p, opts) {
    opts = opts || {};
    if (!editMode || !editorLive) return;
    if (!p) return;
    if (p.ok === false && !p.content) {
      // 不销毁激活中的 textarea：只提示状态
      setEditStatus(p.error || "渲染失败", "warn");
      return;
    }
    var html = p.content || "";
    var parts;
    if (p._lpParts) {
      parts = p._lpParts;
    } else if (html.indexOf("inkwell-lp-block:") >= 0) {
      parts = splitPreviewHtml(html, lpBlocks.length);
    } else if (lpBlocks.length === 1) {
      parts = [html];
    } else {
      parts = [html];
      for (var i = 1; i < lpBlocks.length; i++) parts.push("");
    }
    mountLiveDom(parts, { keepActive: !!opts.keepActive });
    if (toc && p.toc != null && !opts.skipToc) {
      if (html.indexOf("inkwell-lp-block:") < 0) toc.innerHTML = p.toc || "";
    }
  }

  function runLiveRender(opts) {
    opts = opts || {};
    if (!editMode || !editorSource) return;
    var a = api();
    if (!a || !a.preview_markdown) return;
    flushActiveBlock();

    var wantActive = (opts.afterActivate != null)
      ? opts.afterActivate
      : (opts.keepActive ? activeBlockIdx : -1);
    if (wantActive != null && wantActive < 0) wantActive = -1;

    var savedSel = null;
    if (wantActive >= 0) {
      var ta = getActiveTextarea();
      if (ta && wantActive === activeBlockIdx) {
        savedSel = { start: ta.selectionStart, end: ta.selectionEnd, text: ta.value };
      }
    }
    // 全页重渲前记住锚点（含首次 pendingEnterScroll）
    var preAnchor = captureLiveScrollAnchor();
    if (pendingEnterScroll != null) {
      preAnchor = { idx: -1, offset: 0, scrollTop: pendingEnterScroll };
    }

    lpBlocks = splitMarkdownBlocks(editorSource.value);
    if (wantActive >= lpBlocks.length) wantActive = -1;
    activeBlockIdx = -1;

    var marked = bufferWithMarkers(lpBlocks);
    var seq = ++editPreviewSeq;
    var afterIdx = wantActive;
    a.preview_markdown(marked, editPath || currentPath).then(function (p) {
      if (!editMode || seq !== editPreviewSeq) return;
      if (p && p.ok === false && !p.content) {
        setEditStatus(p.error || "渲染失败", "warn");
        if (afterIdx >= 0 && afterIdx < lpBlocks.length) {
          // 不整页 wipe：尝试就地激活
          activeBlockIdx = -1;
          if (getBlockEl(afterIdx)) activateBlockInPlace(afterIdx, {});
          else {
            activeBlockIdx = afterIdx;
            mountLiveDom(lastHtmlParts, { keepActive: true });
          }
        }
        return;
      }
      if (p && p.content) {
        p._lpParts = splitPreviewHtml(p.content, lpBlocks.length);
      }
      if (afterIdx >= 0 && afterIdx < lpBlocks.length && activeBlockIdx < 0) {
        activeBlockIdx = -1;
        applyLivePayload(p, { keepActive: false, skipToc: true });
        // 先恢复滚动，再就地激活（activate 自带锚点，勿再用旧 preAnchor 覆盖）
        if (preAnchor) restoreLiveScrollAnchor(preAnchor);
        activateBlockInPlace(afterIdx, {});
        if (savedSel && getActiveTextarea()) {
          var t2 = getActiveTextarea();
          if (t2 && t2.value === savedSel.text) {
            try {
              t2.selectionStart = savedSel.start;
              t2.selectionEnd = savedSel.end;
            } catch (e) {}
          }
          focusNoScroll(t2);
        }
      } else {
        applyLivePayload(p, { keepActive: false, skipToc: true });
        if (preAnchor) restoreLiveScrollAnchor(preAnchor);
        requestAnimationFrame(function () {
          if (preAnchor) restoreLiveScrollAnchor(preAnchor);
        });
      }
      a.preview_markdown(editorSource.value, editPath || currentPath).then(function (p2) {
        if (!editMode || seq !== editPreviewSeq) return;
        if (toc && p2 && p2.toc != null) toc.innerHTML = p2.toc || "";
        if (p2 && p2.ok === false) setEditStatus(p2.error || "渲染失败", "warn");
        else if (opts.fromButton) setEditStatus("已刷新渲染", "ok");
      }).catch(function () {});
    }).catch(function () {
      if (!editMode || seq !== editPreviewSeq) return;
      setEditStatus("渲染请求失败", "warn");
    });
  }

  function insertAtCursor(snippet, opts) {
    opts = opts || {};
    if (!editMode) return;
    flushActiveBlock();

    var ta = getActiveTextarea();
    if (!ta) {
      // 无激活块：作为新块追加；渲染完成后激活
      if (lpBlocks.length === 1 && !(lpBlocks[0].text || "").replace(/^\s+|\s+$/g, "")) {
        lpBlocks[0].text = snippet;
      } else {
        lpBlocks.push({ text: snippet });
      }
      syncBufferFromBlocks();
      var newIdx = lpBlocks.length - 1;
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
    if (activeBlockIdx >= 0) lpBlocks[activeBlockIdx].text = ta.value;
    syncBufferFromBlocks();
    autosizeTextarea(ta);
    try { ta.focus(); } catch (e) {}
  }

  function deleteImageAtCursor() {
    if (!editMode) return;
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
    lpBlocks[activeBlockIdx].text = next;
    syncBufferFromBlocks();
    autosizeTextarea(ta);
    try { ta.focus(); } catch (e) {}
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

  function pauseWatcher(paused) {
    var a = api();
    if (a && a.set_watch_paused) a.set_watch_paused(!!paused);
  }

  function confirmLeaveEdit() {
    if (!editDirty) return true;
    return window.confirm("有未保存的修改，确定放弃并退出编辑？");
  }

  function enterEditMode() {
    if (editMode) return;
    if (!currentPath) {
      toast("请先打开一个 Markdown 文件再编辑");
      return;
    }
    var a = api();
    if (!a || !a.get_source) {
      toast("编辑接口不可用");
      return;
    }
    if (imageViewer && imageViewer.classList.contains("open")) closeImageViewer();
    closeSearch();
    clearVarHighlights();
    clearSelectedImage();
    pauseWatcher(true);

    var gen = ++editEnterGen;
    var pathAtRequest = currentPath;
    var savedMainScroll = main ? main.scrollTop : 0;
    a.get_source(pathAtRequest).then(function (res) {
      if (gen !== editEnterGen) {
        // 进入被取消：若仍未处于编辑态则恢复监视
        if (!editMode) pauseWatcher(false);
        return;
      }
      if (!samePath(currentPath, pathAtRequest)) {
        pauseWatcher(false);
        return;
      }
      if (editMode) return;
      if (!res || !res.ok) {
        pauseWatcher(false);
        toast((res && res.error) || "无法读取源文件");
        return;
      }
      editorSource = $("editorSource");
      editorLive = $("editorLive");
      editorLiveWrap = $("editorLiveWrap");
      editorPane = $("editorPane");
      editorStatus = $("editorStatus");
      if (!editorSource || !editorPane || !editorLive) {
        pauseWatcher(false);
        toast("编辑器界面未就绪");
        return;
      }
      editMode = true;
      editPath = res.path || currentPath;
      editMtimeNs = res.mtime_ns != null ? String(res.mtime_ns) : null;
      // 规范化 baseline，避免仅因 CRLF/空行归一化就误标 dirty
      var raw = res.text || "";
      lpBlocks = splitMarkdownBlocks(raw);
      editBaseline = joinMarkdownBlocks(lpBlocks);
      editorSource.value = editBaseline;
      activeBlockIdx = -1;
      lastHtmlParts = [];
      setDirty(false);
      // 阅读区可能已滚动：编辑态 main 溢出隐藏需归零，但把滚动位置交给 live 容器
      if (main) {
        main._editRestoreScroll = savedMainScroll;
        main.scrollTop = 0;
      }
      pendingEnterScroll = savedMainScroll || 0;
      document.body.classList.add("edit-mode");
      if (editorPane) editorPane.hidden = false;
      updateDirtyUI();
      setEditStatus("编辑中 · 点击段落即可修改 · " + (res.title || PathBase(editPath)), "ok");
      runLiveRender({});
    }).catch(function () {
      if (gen !== editEnterGen) {
        if (!editMode) pauseWatcher(false);
        return;
      }
      pauseWatcher(false);
      toast("读取源文件失败");
    });
  }

  function PathBase(p) {
    if (!p) return "";
    var s = String(p).replace(/\\/g, "/");
    var i = s.lastIndexOf("/");
    return i >= 0 ? s.slice(i + 1) : s;
  }

  function exitEditMode(opts) {
    opts = opts || {};
    if (!editMode) {
      // 取消进行中的 enter，并确保监视恢复
      editEnterGen += 1;
      pauseWatcher(false);
      return;
    }
    if (!opts.force && !confirmLeaveEdit()) return;
    clearTimeout(editPreviewTimer);
    editEnterGen += 1;
    editPreviewSeq += 1;
    editSaveGen += 1;
    flushActiveBlock();
    editMode = false;
    setDirty(false);
    editBaseline = "";
    editMtimeNs = null;
    editPath = null;
    lpBlocks = [];
    activeBlockIdx = -1;
    lastHtmlParts = [];
    document.body.classList.remove("edit-mode");
    if (editorPane) editorPane.hidden = true;
    if (editorSource) editorSource.value = "";
    if (editorLive) editorLive.innerHTML = "";
    pauseWatcher(false);
    updateDirtyUI();
    setEditStatus("");
    var restoreScroll = (main && typeof main._editRestoreScroll === "number")
      ? main._editRestoreScroll : (main ? main.scrollTop : 0);
    if (main) main._editRestoreScroll = null;
    if (!opts.skipReload && currentPath) {
      var a = api();
      if (a && a.render_path) {
        var exitGen = editEnterGen;
        a.render_path(currentPath).then(function (p) {
          if (editMode || exitGen !== editEnterGen) return;
          if (p) {
            renderInto(p);
            if (main) main.scrollTop = restoreScroll;
          }
        });
      }
    } else if (main) {
      main.scrollTop = restoreScroll;
    }
  }

  function toggleEditMode() {
    if (editMode) exitEditMode();
    else enterEditMode();
  }

  function saveEdit(opts) {
    opts = opts || {};
    if (!editMode || !editorSource || editSaving) return Promise.resolve(null);
    var a = api();
    if (!a || !a.save_document) {
      toast("保存接口不可用");
      return Promise.resolve(null);
    }
    flushActiveBlock();
    editSaving = true;
    var saveGen = ++editSaveGen;
    setEditStatus("保存中…");
    // 磁盘快照：finishSave 的 baseline 必须与此一致
    var text = editorSource.value;
    var path = editPath || currentPath;
    function unlock() { editSaving = false; }
    return a.save_document(text, path, editMtimeNs).then(function (p) {
      if (saveGen !== editSaveGen) { unlock(); return null; }
      if (!p) { unlock(); setEditStatus("保存失败", "warn"); return null; }
      if (p.conflict) {
        var force = window.confirm((p.error || "文件冲突") + "\n\n是否强制覆盖？");
        if (!force) {
          unlock();
          setEditStatus("已取消（磁盘文件已变更）", "warn");
          return p;
        }
        return a.save_document(text, path, null).then(function (p2) {
          if (saveGen !== editSaveGen) { unlock(); return null; }
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
    if (saveGen != null && saveGen !== editSaveGen) return p;
    if (!p || p.ok === false) {
      setEditStatus((p && p.error) || "保存失败", "warn");
      toast((p && p.error) || "保存失败");
      return p;
    }
    if (!editMode) {
      toast("已保存到磁盘");
      return p;
    }
    // baseline = 已落盘内容；保存飞行中的新键入应标 dirty
    editBaseline = savedText != null ? savedText : (editorSource ? editorSource.value : editBaseline);
    editMtimeNs = p.mtime_ns != null ? String(p.mtime_ns) : editMtimeNs;
    if (p.path) {
      editPath = p.path;
      currentPath = p.path;
      setDocTitle(p.title || PathBase(p.path));
    }
    if (content && p.content != null) {
      content.innerHTML = p.content || "";
      if (toc && p.toc != null) toc.innerHTML = p.toc || "";
      initContent();
    }
    // 拆掉激活态；若用户在保存期间又改了，flush 后与 baseline 比较
    flushActiveBlock();
    teardownActiveTextarea();
    activeBlockIdx = -1;
    markDirtyFromBuffer();
    runLiveRender({});
    if (p.path && api() && api().activate_path) api().activate_path(p.path);
    if (p.encoding_changed) {
      setEditStatus("已保存（编码已改为 " + p.encoding_changed + "）", "ok");
    } else {
      setEditStatus(editDirty ? "已保存（之后还有未保存修改）" : "已保存", "ok");
    }
    toast(editDirty ? "已保存（有后续修改未写入）" : "已保存");
    if (opts && opts.exit) {
      if (editDirty && !window.confirm("保存后又有修改，仍要退出并丢弃？")) return p;
      exitEditMode({ force: true, skipReload: true });
    }
    return p;
  }

  function insertImage(mode) {
    if (!editMode) return;
    var a = api();
    if (!a || !a.pick_image) {
      toast("插图接口不可用");
      return;
    }
    setEditStatus(mode === "embed" ? "选择要内嵌的图片…" : "选择图片…");
    var gen = editEnterGen;
    a.pick_image(mode || "file").then(function (res) {
      if (!editMode || !editorSource || gen !== editEnterGen) return;
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
      if (!editMode) return;
      setEditStatus("插入图片失败", "warn");
    });
  }

  function onLiveClick(e) {
    if (!editMode || !editorLive) return;
    // 交互控件 + 图片/图示本体：交给后续灯箱/复制处理器，不进入块编辑
    if (e.target.closest && e.target.closest(
      "button, a, textarea, input, img, .image-block, .mermaid-diagram, .mermaid-block, " +
      ".code-copy-float, .math-copy-btn, [data-mermaid-action], [data-copy-action]"
    )) {
      return;
    }
    var block = e.target.closest && e.target.closest(".lp-block");
    if (!block || !editorLive.contains(block)) {
      if (activeBlockIdx >= 0 && (e.target === editorLive || e.target === editorLiveWrap)) {
        deactivateBlock();
      }
      return;
    }
    var idx = parseInt(block.dataset.idx, 10);
    if (isNaN(idx)) return;
    if (idx === activeBlockIdx) {
      // 激活索引存在但 textarea 丢失时允许修复
      if (!getActiveTextarea()) activateBlock(idx);
      return;
    }
    e.preventDefault();
    activateBlock(idx);
  }

  function bindEditorUI() {
    editorSource = $("editorSource");
    editorLive = $("editorLive");
    editorLiveWrap = $("editorLiveWrap");
    editorPane = $("editorPane");
    editorStatus = $("editorStatus");
    var editBtn = $("editBtn");
    if (editBtn) editBtn.addEventListener("click", toggleEditMode);
    if ($("editSaveBtn")) $("editSaveBtn").addEventListener("click", function () { saveEdit(); });
    if ($("editRefreshBtn")) $("editRefreshBtn").addEventListener("click", function () {
      flushActiveBlock();
      teardownActiveTextarea();
      activeBlockIdx = -1;
      runLiveRender({ fromButton: true });
    });
    if ($("editInsertImageBtn")) $("editInsertImageBtn").addEventListener("click", function () { insertImage("file"); });
    if ($("editEmbedImageBtn")) $("editEmbedImageBtn").addEventListener("click", function () { insertImage("embed"); });
    if ($("editDeleteImageBtn")) $("editDeleteImageBtn").addEventListener("click", deleteImageAtCursor);
    if ($("editInsertCodeBtn")) $("editInsertCodeBtn").addEventListener("click", insertCodeBlock);
    if ($("editInsertMermaidBtn")) $("editInsertMermaidBtn").addEventListener("click", insertMermaidBlock);
    if ($("editInsertMathBtn")) $("editInsertMathBtn").addEventListener("click", insertMathBlock);
    if ($("editExitBtn")) $("editExitBtn").addEventListener("click", function () { exitEditMode(); });
    if (editorLive) {
      // pointerdown 抢在 blur 前标记激活，避免 blur→deactivate 与 click 竞态
      editorLive.addEventListener("pointerdown", function (e) {
        if (!editMode) return;
        if (e.target.closest && e.target.closest("button, a, textarea, input, img, .image-block, .mermaid-diagram, .mermaid-block")) return;
        var block = e.target.closest && e.target.closest(".lp-block");
        if (block && editorLive.contains(block)) {
          lpActivating = true;
          setTimeout(function () { lpActivating = false; }, 50);
        }
      }, true);
      editorLive.addEventListener("click", onLiveClick);
      editorLive.addEventListener("click", onContentActionClick);
      editorLive.addEventListener("click", onContentImageClick);
      editorLive.addEventListener("click", onContentDiagramClick);
      editorLive.addEventListener("click", onContentLinkClick);
      editorLive.addEventListener("mousedown", function (e) {
        if (e.target.closest && e.target.closest("img")) selectImage(e.target.closest("img"));
        else if (!(e.target.closest && e.target.closest(".lp-block.is-active"))) clearSelectedImage();
      });
    }
  }

  // ============================================================
  // 文档间跳转：历史栈（后退/前进）+ 正文 .md 链接拦截（支持递归深入）
  // ============================================================
  var navHistory = [], navIndex = -1, navGeneration = 0;

  function beginNavigation() { navGeneration += 1; return navGeneration; }
  function isCurrentNavigation(generation) { return generation === navGeneration; }
  function samePath(a, b) {
    if (!a || !b) return false;
    return String(a).replace(/\//g, "\\").toLowerCase() === String(b).replace(/\//g, "\\").toLowerCase();
  }

  function renderInto(p) {
    if (!p) return false;
    if (p.ok === false && !p.content) return false;
    content.innerHTML = p.content || "";
    if (toc) toc.innerHTML = p.toc || "";
    currentPath = p.path || currentPath;
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
    main.scrollTop = (typeof restoreScroll === "number") ? restoreScroll : 0;
  }

  function updateNavButtons() {
    var b = $("navBack"), f = $("navForward");
    if (b) b.disabled = navIndex <= 0;
    if (f) f.disabled = navIndex >= navHistory.length - 1;
  }

  function navSaveScroll() {
    // 快速连点前进/后退时 navIndex 可能已指向待加载页，不要把旧页滚动量写错条目。
    if (navIndex >= 0 && navHistory[navIndex]
        && samePath(navHistory[navIndex].path, currentPath)) {
      navHistory[navIndex].scrollTop = main.scrollTop;
    }
  }

  // 前进式跳转（点击链接 / 打开新文件）：截断 forward 分支，压入新条目
  function navTo(p, anchor) {
    if (editMode) {
      if (editDirty && !window.confirm("有未保存的修改，切换文档将丢失修改。继续？")) return;
      exitEditMode({ force: true, skipReload: true });
    }
    navSaveScroll();
    navHistory = navHistory.slice(0, navIndex + 1);
    navHistory.push({ path: p.path, anchor: anchor || "", scrollTop: 0 });
    navIndex = navHistory.length - 1;
    if (renderInto(p)) scrollAfterLoad(anchor, 0);
    updateNavButtons();
  }

  function navGo(delta) {
    var target = navIndex + delta;
    if (target < 0 || target >= navHistory.length) return;
    if (editMode) {
      if (editDirty && !window.confirm("有未保存的修改，切换文档将丢失修改。继续？")) return;
      exitEditMode({ force: true, skipReload: true });
    }
    var a = api(); if (!a || !a.render_path) return;
    navSaveScroll();
    navIndex = target;
    var e = navHistory[navIndex];
    var generation = beginNavigation();
    a.render_path(e.path).then(function (p) {
      if (!isCurrentNavigation(generation)) return;
      if (p && p.ok === false) toast("文件可能已被移动或删除");
      if (renderInto(p)) scrollAfterLoad(null, e.scrollTop);
      updateNavButtons();
    });
  }
  function navBack() { navGo(-1); }
  function navForward() { navGo(1); }

  function navigateToMd(href) {
    var a = api(); if (!a || !a.open_md_link) return;
    var generation = beginNavigation();
    a.open_md_link(href, currentPath).then(function (p) {
      if (!isCurrentNavigation(generation)) return;
      if (!p) return;
      if (p.samedoc) { if (p.anchor) { lockSpy(); scrollToHeading(p.anchor); } return; }
      if (p.ok === false) { toast(p.error || ("无法打开 " + href)); return; }
      navTo(p, p.anchor);
    });
  }

  // 含协议(http/mailto/...) 视为外链；但要排除 Windows 盘符 C:\ 这种"伪协议"
  function isExternalUrl(h) { return /^[a-z][a-z0-9+.\-]*:/i.test(h) && !/^[a-z]:[\\/]/i.test(h); }

  function onContentLinkClick(e) {
    var a = e.target.closest("a");
    if (!a || !inRenderableRoot(a)) return;
    var href = a.getAttribute("href");
    if (href == null || href === "") return;
    if (href.charAt(0) === "#") {                    // 同页锚点
      e.preventDefault();
      var id = decodeURIComponent(href.slice(1));
      // live 编辑区内锚点滚 live 容器；正文滚主滚动区
      var elive = editorLive || $("editorLive");
      if (editMode && elive && elive.contains(a)) {
        var target = null;
        try {
          target = elive.querySelector("#" + (window.CSS && CSS.escape ? CSS.escape(id) : id.replace(/([^a-zA-Z0-9\-_])/g, "\\$1")));
        } catch (err) { target = document.getElementById(id); }
        if (target && elive.contains(target)) target.scrollIntoView({ block: "start" });
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
      if (editMode && editDirty && !window.confirm("有未保存的修改，跳转将丢失修改。继续？")) return;
      navigateToMd(href); return;
    }
    var ay = api(); if (ay && ay.open_external) ay.open_external(href);              // 其它本地文件 → 系统默认程序
  }

  function openFileDialog() {
    if (editMode && editDirty && !window.confirm("有未保存的修改，打开其他文件将丢失修改。继续？")) return;
    var a = api(); if (!a) return;
    var generation = beginNavigation();
    var wasEdit = editMode;
    a.open_dialog().then(function (p) {
      if (!isCurrentNavigation(generation)) return;
      if (p && p.ok) {
        // 仅在真正打开成功后再退出编辑并跳转
        if (wasEdit && editMode) exitEditMode({ force: true, skipReload: true });
        navTo(p, "");
      }
      // 取消对话框：保持编辑会话
    });
  }

  // 轻量 toast 提示（打开失败等）
  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  // 文件变更自动刷新（同一文档）：保持滚动，不改动历史
  function applyPayload(p) {
    if (!p || p.cancelled) return;
    if (p.ok === false && !p.content) return;
    // 编辑模式中绝不接受热重载，防止覆盖源码缓冲。
    if (editMode) return;
    // watcher 可能在换页期间送达旧文档 payload，绝不允许它覆盖当前页。
    if (!samePath(p.path, currentPath)) return;
    var keep = main.scrollTop;
    renderInto(p);
    main.scrollTop = keep;
  }
  window.__applyPayload = applyPayload;

  // 启动首帧先显示轻量外壳，后台渲染完成后用此入口装载首篇文档。
  // 与文件监视热重载不同，这里不要求 p.path 已等于 currentPath，错误页也必须可显示。
  function applyInitialPayload(p) {
    if (!p || p.cancelled || (p.ok === false && !p.content)) return;
    var bootPath = (window.__BOOT__ && window.__BOOT__.path) || "";
    // 用户可能在后台首篇渲染结束前就打开了另一篇文档；旧启动结果不能覆盖新页面。
    if ((bootPath && currentPath && !samePath(currentPath, bootPath)) || (!bootPath && currentPath)) return;
    if (p.path) {
      currentPath = p.path;
      navHistory = [{ path: p.path, anchor: "", scrollTop: 0 }];
      navIndex = 0;
    }
    if (renderInto(p)) {
      main.scrollTop = 0;
      updateNavButtons();
    }
  }
  window.__applyInitialPayload = applyInitialPayload;

  // ============================================================
  // 绑定与启动
  // ============================================================
  function bind() {
    // 顶栏按钮
    $("sidebarToggle").addEventListener("click", toggleSidebar);
    $("themeBtn").addEventListener("click", toggleTheme);
    $("searchBtn").addEventListener("click", function () {
      if (editMode) return; // 编辑态搜索正文无意义
      if (searchBar.classList.contains("open")) closeSearch(); else openSearch();
    });
    $("openBtn").addEventListener("click", openFileDialog);
    bindEditorUI();

    // 文档跳转：后退/前进 + 正文链接拦截
    $("navBack").addEventListener("click", navBack);
    $("navForward").addEventListener("click", navForward);
    content.addEventListener("click", onContentActionClick);
    content.addEventListener("click", onContentImageClick);
    content.addEventListener("click", onContentDiagramClick);
    content.addEventListener("click", onContentLinkClick);
    content.addEventListener("mousedown", function (e) {
      if (!(e.target.closest && e.target.closest("img"))) clearSelectedImage();
    });
    // live 编辑区事件在 bindEditorUI 中绑定

    // 窗口控制（拖动 / 双击最大化由 setupWindowDrag 处理）
    $("winMin").addEventListener("click", function () { var a = api(); if (a) a.win_minimize(); });
    $("winMax").addEventListener("click", function () { var a = api(); if (a && a.win_toggle_maximize) a.win_toggle_maximize(); });
    $("winClose").addEventListener("click", function () {
      if (editMode && editDirty && !window.confirm("有未保存的修改，确定关闭？")) return;
      var a = api(); if (a) a.win_close();
    });

    // 搜索栏
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      var q = searchInput.value;
      searchTimer = setTimeout(function () { runSearch(q); }, 160);
    });
    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); nextHit(e.shiftKey ? -1 : 1); }
      else if (e.key === "Escape") { closeSearch(); }
    });
    $("searchNext").addEventListener("click", function () { nextHit(1); });
    $("searchPrev").addEventListener("click", function () { nextHit(-1); });
    $("searchClose").addEventListener("click", closeSearch);

    // 目录
    toc.addEventListener("click", onTocClick);
    main.addEventListener("scroll", onScrollSpy, { passive: true });
    main.addEventListener("scrollend", unlockSpy);   // 平滑滚动结束即解锁 spy

    // 抽屉遮罩
    $("scrim").addEventListener("click", closeDrawer);

    // 复制净化（捕获阶段）
    document.addEventListener("copy", onCopy, true);

    // 双击高亮 + 点击/Esc 清除
    content.addEventListener("dblclick", onDblClick);
    document.addEventListener("click", function (e) {
      if (!(e.target.closest && e.target.closest(".var-highlight"))) clearVarHighlights();
    });

    // 快捷键
    document.addEventListener("keydown", function (e) {
      var ctrl = e.ctrlKey || e.metaKey;
      // 编辑器内的 Ctrl+S / Tab 由 textarea 自己处理；此处拦截全局
      if (ctrl && (e.key === "e" || e.key === "E")) {
        e.preventDefault();
        toggleEditMode();
        return;
      }
      if (ctrl && (e.key === "s" || e.key === "S")) {
        if (editMode) { e.preventDefault(); saveEdit(); }
        return;
      }
      if (editMode && ctrl && e.shiftKey && (e.key === "p" || e.key === "P")) {
        e.preventDefault();
        flushActiveBlock();
        teardownActiveTextarea();
        activeBlockIdx = -1;
        runLiveRender({ fromButton: true });
        return;
      }
      if (editMode && e.key === "Escape") {
        if (imageViewer && imageViewer.classList.contains("open")) { closeImageViewer(); return; }
        e.preventDefault();
        // Obsidian 风格：先退出当前块编辑，再退出编辑模式
        if (activeBlockIdx >= 0) {
          deactivateBlock();
          return;
        }
        exitEditMode();
        return;
      }
      if (editMode) {
        // 编辑态屏蔽阅读向快捷键（搜索 / 目录切换仍允许）
        if (ctrl && (e.key === "f" || e.key === "F")) { e.preventDefault(); return; }
      }
      if (ctrl && (e.key === "f" || e.key === "F")) { e.preventDefault(); openSearch(); }
      else if (ctrl && (e.key === "b" || e.key === "B")) { e.preventDefault(); toggleSidebar(); }
      else if (ctrl && (e.key === "o" || e.key === "O")) { e.preventDefault(); openFileDialog(); }
      else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); navBack(); }
      else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); navForward(); }
      else if (e.key === "Escape") {
        if (imageViewer && imageViewer.classList.contains("open")) closeImageViewer();
        else if (searchBar.classList.contains("open")) closeSearch();
        else clearVarHighlights();
      }
      else if (e.key === "F3") { e.preventDefault(); nextHit(e.shiftKey ? -1 : 1); }
    });

    window.addEventListener("resize", function () {
      onResize();
      if (imageViewerIsOpen() && !imageViewerUserAdjusted) requestAnimationFrame(fitImageViewer);
    });
  }

  function boot() {
    content = $("content"); main = $("main"); app = $("app");
    sidebar = $("sidebar"); toc = $("toc"); dragRegion = $("dragRegion");
    docTitle = $("docTitle");
    searchBar = $("searchbar"); searchInput = $("searchInput"); searchCount = $("searchCount");

    var bootData = window.__BOOT__ || {};
    var preferences = bootData.preferences || {};
    var t = preferences.theme || document.documentElement.getAttribute("data-theme") || "light";
    setTheme(t, false);
    setDocTitle(bootData.title || "Inkwell");
    // 用初始文档播种跳转历史
    if (bootData.path) {
      currentPath = bootData.path;
      navHistory = [{ path: bootData.path, anchor: "", scrollTop: 0 }];
      navIndex = 0;
    }

    bind();
    setupResizer();
    setupWindowResize();
    setupWindowDrag();
    setupFontZoom(preferences.font);
    onResize();
    initContent();
    updateNavButtons();
  }

  // 测试钩子（无害；供自动化探针验证净化/渲染/高亮）
  window.__ink = {
    sanitize: sanitizeForCopy, render: renderMath,
    highlight: highlightToken, clearHL: clearVarHighlights, runSearch: runSearch,
    copyPayload: buildCopyPayload,
    image: {
      toPng: imageToPngBlob, selected: function () { return selectedImage; },
      open: openImageViewer, close: closeImageViewer, fit: fitImageViewer,
      zoom: zoomImageViewer, openSvg: openSvgViewer,
      state: function () {
        var el = viewerContentEl();
        var stage = imageViewer && imageViewer.querySelector(".image-viewer-stage");
        return { open: imageViewerIsOpen(), scale: imageViewerScale, fit: imageViewerFitScale,
                 kind: imageViewerKind, panning: !!imagePan,
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
        block = block || content.querySelector(".mermaid-block");
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
        return { index: navIndex, len: navHistory.length, path: currentPath,
                 title: (docTitle && docTitle.textContent) || "" };
      }
    },
    edit: {
      enter: enterEditMode, exit: function () { exitEditMode({ force: true }); },
      toggle: toggleEditMode, save: saveEdit,
      findImageAt: findImageAt, findProtectedRanges: findProtectedRanges,
      splitBlocks: splitMarkdownBlocks, joinBlocks: joinMarkdownBlocks,
      activate: activateBlock, deactivate: deactivateBlock,
      refresh: function () {
        flushActiveBlock();
        activeBlockIdx = -1;
        runLiveRender({ fromButton: true });
      },
      state: function () {
        flushActiveBlock();
        return {
          mode: editMode, dirty: editDirty, path: editPath, mtime: editMtimeNs,
          length: editorSource ? editorSource.value.length : 0,
          baseline: editBaseline.length,
          blocks: lpBlocks.length,
          active: activeBlockIdx
        };
      },
      getText: function () { return getEditText(); },
      setText: function (t) {
        if (!editMode || !editorSource) return;
        activeBlockIdx = -1;
        setEditText(t, { render: true, activate: -1 });
      }
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
