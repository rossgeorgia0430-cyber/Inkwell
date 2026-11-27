import { api, holdHeightWhileImagesLoad } from "../util.js";
import { state } from "../state.js";
import { initRenderedFragment } from "../reader.js";
import { withReferenceDefinitions } from "./blocks.js";

// 围栏行和信息字符串属于文档格式，修改代码正文时应保留。
function fenceParts(source) {
  const match = /^(( {0,3})(`{3,}|~{3,})[^\n]*)\n([\s\S]*)$/.exec(source);
  if (!match) return null;
  const closing = new RegExp("(?:^|\\n)( {0,3})(" + match[3][0] + "{" + match[3].length + ",})([ \\t]*)$").exec(match[4]);
  if (!closing) return null;
  return { header: match[1], fence: match[3], body: match[4].slice(0, closing.index),
    close: closing[1] + closing[2] + closing[3], indent: match[2] };
}

function fencedText(parts, value) {
  const fence = parts.fence;
  let length = fence.length;
  for (const line of value.matchAll(/^ {0,3}(`+|~+)[ \t]*$/gm)) {
    if (line[1][0] === fence[0]) length = Math.max(length, line[1].length + 1);
  }
  const mark = fence[0].repeat(length);
  const header = parts.header.replace(fence, mark);
  const close = length === fence.length ? parts.close : parts.close.replace(/`+|~+/, mark);
  return header + "\n" + value + (value ? "\n" : "") + close;
}

function editCode(view, parts, onChange) {
  const pre = view.querySelector(".codehilite pre");
  if (!pre || parts.indent || pre.textContent !== parts.body + "\n") return false;
  const walker = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT);
  let last;
  while (walker.nextNode()) last = walker.currentNode;
  if (last) last.deleteData(last.length - 1, 1);
  pre.contentEditable = "plaintext-only";
  pre.setAttribute("aria-label", "编辑代码");
  pre.addEventListener("input", () => onChange(fencedText(parts, pre.innerText)));
  return true;
}

function editSpec(view, source, parts) {
  if (parts) return { label: view.querySelector(".mermaid-block") ? "编辑流程图" : "编辑代码",
    value: parts.body, write: value => fencedText(parts, value) };
  const math = /^(\s*(?:\$\$|\\\[)\s*)([\s\S]*?)(\s*(?:\$\$|\\\])\s*)$/.exec(source);
  if (math && view.querySelector(".math-block")) return { label: "编辑公式", value: math[2],
    write: value => math[1] + value + math[3] };
  const image = /^(!\[)([^\]\n]*)(\]\([\s\S]*\))$/.exec(source);
  if (image && view.querySelector("img") && view.querySelectorAll("img").length === 1)
    return { label: "编辑图片说明", value: image[2], write: value =>
      image[1] + value.replace(/[\r\n]+/g, " ").replace(/]/g, "\\]") + image[3] };
  return { label: "编辑并预览", value: source, write: value => value };
}

// 预览整体替换会让图片重建：图片加载完之前保持原高度，避免闪一下。
function replacePreview(preview, html) {
  const height = preview.offsetHeight;
  preview.innerHTML = html;
  initRenderedFragment(preview);
  holdHeightWhileImagesLoad(preview, height);
}

// 输入面板浮在块下方；下方放不下而上方放得下时翻到上方
function placePanel(panel, view) {
  panel.classList.remove("is-above");
  const wrap = view.closest(".editor-live-wrap")?.getBoundingClientRect();
  if (!wrap) return;
  const block = view.closest(".lp-block").getBoundingClientRect();
  const height = panel.offsetHeight + 8;
  if (block.bottom + height > wrap.bottom && block.top - wrap.top > height) panel.classList.add("is-above");
}

// 返回「编辑…」展开控件，由调用方放进块的操作区；代码块可直接编辑时返回 null。
export function prepareStructured(view, source, onChange) {
  const parts = fenceParts(source);
  if (parts && !view.querySelector(".mermaid-block") && editCode(view, parts, onChange)) return null;
  const spec = editSpec(view, source, parts);
  const preview = document.createElement("div");
  while (view.firstChild) preview.appendChild(view.firstChild);
  view.append(preview);
  const details = document.createElement("details");
  details.className = "lp-preview-controls";
  const summary = document.createElement("summary");
  summary.textContent = spec.label;
  details.append(summary);
  const panel = document.createElement("div");
  panel.className = "lp-preview-panel";
  panel.hidden = true;
  view.append(panel);
  details.addEventListener("toggle", () => {
    panel.hidden = !details.open;
    if (!details.open) return;
    if (!panel.firstChild) fillPanel(panel, view, preview, spec, onChange);
    placePanel(panel, view);
    panel.querySelector("textarea").focus({ preventScroll: true });
  });
  return details;
}

function fillPanel(panel, view, preview, spec, onChange) {
  const input = document.createElement("textarea");
  input.className = "lp-parameter";
  input.setAttribute("aria-label", spec.label);
  input.rows = Math.min(12, Math.max(2, spec.value.split("\n").length));
  input.value = spec.value;
  const status = document.createElement("span");
  status.setAttribute("role", "status");
  panel.append(input, status);
  let revision = 0, timer;
  input.addEventListener("input", () => {
    const text = spec.write(input.value);
    onChange(text);
    const pending = ++revision;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!view.isConnected) return;
      try {
        const index = Number(view.closest(".lp-block").dataset.idx);
        const result = await api().preview_markdown(withReferenceDefinitions(text, state.lpBlocks, index), state.editPath);
        if (!view.isConnected || revision !== pending) return;
        if (!result.ok) throw new Error(result.error);
        replacePreview(preview, result.content);
        status.textContent = "";
      } catch {
        if (revision === pending) status.textContent = "预览暂不可用，编辑内容已保留";
      }
    }, 250);
  });
}
