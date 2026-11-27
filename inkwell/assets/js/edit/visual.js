import { renderMath } from "../reader.js";
import { prepareStructured } from "./structured.js";

// 只有渲染内容能与原文逐段对应时才开放直接编辑，保留其余 Markdown 语法。
const delimiters = { STRONG: "**", B: "**", EM: "*", I: "*", CODE: "`" };

const originalRuns = new WeakMap();

const entities = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };

// 浮层相对 .lp-block 定位，贴在目标下方且不超出块的左右边界
function placeBelow(floating, target) {
  const block = target.closest(".lp-block");
  const blockRect = block.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  const left = Math.min(rect.left - blockRect.left, blockRect.width - floating.offsetWidth);
  floating.style.left = Math.max(0, left) + "px";
  floating.style.top = rect.bottom - blockRect.top + 4 + "px";
}

function bindInlineFormula(node, raw) {
  node.contentEditable = "false";
  node.tabIndex = 0;
  node.title = "编辑行内公式";
  originalRuns.set(node, { markdown: raw });
  function open() {
    const host = node.closest('[contenteditable="true"]');
    if (!host) return;
    const view = node.closest(".lp-view");
    view.querySelector(".lp-inline-formula")?.remove();
    const label = document.createElement("label");
    label.className = "lp-inline-formula";
    label.textContent = "LaTeX ";
    const input = document.createElement("input");
    input.value = node.dataset.latex;
    label.append(input);
    view.append(label);
    placeBelow(label, node);
    input.focus({ preventScroll: true });
    let timer;
    input.addEventListener("input", () => {
      const value = input.value;
      const dollars = raw.startsWith("$") && value && !/[$\n]/.test(value) && value.trim() === value;
      originalRuns.set(node, { markdown: dollars ? "$" + value + "$" : "\\(" + value + "\\)" });
      node.dataset.latex = value;
      host.dispatchEvent(new Event("input", { bubbles: true }));
      clearTimeout(timer);
      timer = setTimeout(() => { if (node.isConnected) renderMath(host); }, 120);
    });
    input.addEventListener("blur", () => label.remove());
    input.addEventListener("keydown", event => {
      if (!event.isComposing && (event.key === "Enter" || event.key === "Escape")) {
        event.preventDefault();
        node.focus();
      }
    });
  }
  node.addEventListener("click", open);
  node.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); open(); }
  });
}

function matchInline(parent, source, offset) {
  for (const node of parent.childNodes) {
    const start = offset;
    if (node.nodeType === Node.TEXT_NODE) {
      for (const character of node.nodeValue) {
        if (source[offset] === "\\" && source[offset + 1] === character && parent.tagName !== "CODE") offset++;
        if (source[offset] === "&") {
          const numeric = /^&#(\d{1,7});/.exec(source.slice(offset, offset + 11));
          if (numeric && Number(numeric[1]) === character.codePointAt(0)) {
            offset += numeric[0].length;
            continue;
          }
          const entity = Object.keys(entities).find(key => entities[key] === character && source.startsWith(key, offset));
          if (entity) { offset += entity.length; continue; }
        }
        if (!source.startsWith(character, offset)) return -1;
        offset += character.length;
      }
      originalRuns.set(node, { text: node.nodeValue, markdown: source.slice(start, offset) });
      continue;
    }
    const name = node.tagName;
    if (node.matches?.(".math-inline[data-latex]")) {
      const raw = node.textContent;
      if (!source.startsWith(raw, offset)) return -1;
      bindInlineFormula(node, raw);
      offset += raw.length;
      continue;
    }
    if (name === "IMG" && source.startsWith("![", offset)) {
      const image = /^!\[([^\]\n]*)\](?:\((?:\\.|[^()\\]|\((?:\\.|[^()\\])*\))*\)|\[[^\]\n]*\])/.exec(source.slice(offset));
      if (!image || image[1] !== node.alt) return -1;
      const atom = document.createElement("span");
      atom.contentEditable = "false";
      node.replaceWith(atom);
      atom.append(node);
      originalRuns.set(atom, { markdown: image[0] });
      offset += image[0].length;
      continue;
    }
    let mark = delimiters[name];
    if (name === "A") mark = "[";
    if (name === "BR" && source.startsWith("  \n", offset)) {
      originalRuns.set(node, { markdown: "  " });
      offset += 2;
      continue;
    }
    if (!mark) return -1;
    if (name === "CODE") {
      const opening = /^`+/.exec(source.slice(offset));
      if (!opening) return -1;
      mark = opening[0];
    }
    if (name === "EM" || name === "STRONG") {
      const underscore = "_".repeat(mark.length);
      if (source.startsWith(underscore, offset)) mark = underscore;
    }
    if (!source.startsWith(mark, offset)) return -1;
    offset = matchInline(node, source, offset + mark.length);
    if (offset < 0) return -1;
    let close = mark;
    if (name === "A") {
      const suffix = source.slice(offset);
      const link = /^\](?:\((?:\\.|[^()\\]|\((?:\\.|[^()\\])*\))*\)|\[[^\]\n]*\])/.exec(suffix);
      if (!suffix.startsWith("]")) return -1;
      close = link?.[0] || "]";
    }
    if (!source.startsWith(close, offset)) return -1;
    offset += close.length;
    const implicitLabel = name === "A" && (close === "]" || close === "][]")
      ? source.slice(start + 1, offset - close.length) : null;
    originalRuns.set(node, { mark, close, implicitLabel });
  }
  return offset;
}

function inlineMarkdown(parent, paragraphBreak) {
  let result = "";
  for (const node of parent.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.nodeValue.replace(/\u00a0/g, " ");
      const original = originalRuns.get(node);
      if (original?.text === node.nodeValue) {
        result += original.markdown;
        continue;
      }
      result += parent.tagName !== "CODE"
        ? text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\$/g, "&#36;")
          .replace(/^(\s*)([#=~+\-]|\d+[.)])(?=\s|$)/gm, (_, space, token) =>
            space + "&#" + token.charCodeAt(0) + ";" + token.slice(1))
          .replace(/([\\`*_[\]|])/g, "\\$1") : text;
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const name = node.tagName;
    const original = originalRuns.get(node);
    if (original?.markdown !== undefined) {
      result += original.markdown;
      continue;
    }
    const inner = inlineMarkdown(node, paragraphBreak);
    if (name === "CODE") {
      let ticks = original?.mark?.length || 1;
      for (const match of inner.matchAll(/`+/g)) ticks = Math.max(ticks, match[0].length + 1);
      const fence = "`".repeat(ticks);
      const pad = inner.startsWith("`") || inner.endsWith("`") ? " " : "";
      result += fence + pad + inner + pad + fence;
    } else if (original) {
      const close = original.implicitLabel && inner !== original.implicitLabel
        ? "][" + original.implicitLabel + "]" : original.close;
      result += original.mark + inner + close;
    }
    else if (delimiters[name]) result += delimiters[name] + inner + delimiters[name];
    else if (name === "A") result += "[" + inner + "](" + node.getAttribute("href") + ")";
    else if (name === "BR" && node.nextSibling) result += "  \n";
    else if (name === "BR") continue;
    else if (name === "DIV" || name === "P") result += (node.previousSibling ? paragraphBreak : "") + inner;
    else result += inner;
  }
  return result;
}

function htmlFields(view, source) {
  if (!/^\s*<[A-Za-z]/.test(source)) return [];
  // 按顺序核对标签之间的原文；净化器移除节点后，不能再靠索引猜它的位置。
  const tokens = [...source.matchAll(/<(?:[^>"']|"[^"]*"|'[^']*')*>|[^<]+/g)]
    .filter(match => match[0][0] !== "<" && match[0].trim());
  const walker = document.createTreeWalker(view, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    if (walker.currentNode.textContent.trim()) nodes.push(walker.currentNode);
  }
  if (nodes.length !== tokens.length || !nodes.length) return [];
  const decoder = document.createElement("textarea");
  if (!tokens.every((token, i) => {
    decoder.innerHTML = token[0];
    return decoder.value === nodes[i].nodeValue;
  })) return [];
  return nodes.map((node, i) => {
    const field = document.createElement("span");
    node.replaceWith(field);
    field.append(node);
    const value = tokens[i][0];
    return { field, value, from: tokens[i].index, to: tokens[i].index + value.length,
      serialize: () => field.textContent.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") };
  });
}

function mapFields(view, source) {
  if (!source && view.querySelector(".lp-empty-hint")) {
    const field = view.firstElementChild;
    field.className = "";
    field.textContent = "";
    return [{ field, from: 0, to: 0, value: "" }];
  }
  if (view.querySelector("img") && !view.textContent.trim()) return [];
  for (const item of view.querySelectorAll("li")) {
    const list = item.querySelector(":scope > ul, :scope > ol");
    if (!list || item.querySelector(":scope > p")) continue;
    const label = document.createElement("span");
    label.className = "lp-list-label";
    while (item.firstChild !== list) label.append(item.firstChild);
    if (label.lastChild?.nodeType === Node.TEXT_NODE) {
      label.lastChild.nodeValue = label.lastChild.nodeValue.replace(/\n$/, "");
    }
    item.prepend(label);
  }
  const fields = [...view.querySelectorAll("p,h1,h2,h3,h4,h5,h6,li,.lp-list-label,td,th")]
    .filter(field => !field.querySelector("p,li,table"));
  let offset = 0;
  const mapped = [];
  for (const field of fields) {
    let prefix;
    if (/^H[1-6]$/.test(field.tagName)) prefix = /^\s*(?:#{1,6}[ \t]+)?/;
    else if (field.matches("li,.lp-list-label")) prefix = /^\s*(?:[-*+]|\d+\.)[ \t]+/;
    else if (field.matches("td,th")) {
      if (field.cellIndex > 0) prefix = /^[ \t]*\|[ \t]*/;
      else if (!offset) prefix = /^[ \t]*\|?[ \t]*/;
      else prefix = /^[ \t]*\|?[ \t]*\n(?:[ \t|:\-]+\n)?[ \t]*\|?[ \t]*/;
    }
    else prefix = field.closest("blockquote") ? /^\s*(?:>[ \t]?)+/ : /^\s*/;
    const start = source.slice(offset).match(prefix);
    if (!start) return [];
    const from = offset + start[0].length;
    // 跳过标题、引用、列表和表格的结构符号。隐藏链接目标或 HTML 会使匹配失败。
    offset = matchInline(field, source, from);
    if (offset < 0) return [];
    mapped.push({ field, from, to: offset, value: source.slice(from, offset) });
  }
  return /^(?:[\s#|:=\-]*|\s*\{[^{}\n]*\}\s*)$/.test(source.slice(offset)) ? mapped : [];
}

export function prepareVisualBlock(view, source, onChange) {
  const html = htmlFields(view, source);
  const fields = html.length ? html : mapFields(view, source);
  for (const entry of fields) {
    const field = entry.field;
    field.contentEditable = entry.serialize ? "plaintext-only" : "true";
    field.setAttribute("role", "textbox");
    field.setAttribute("aria-label", "编辑正文");
    field.addEventListener("paste", event => {
      event.preventDefault();
      document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
    });
    field.addEventListener("drop", event => event.preventDefault());
    field.addEventListener("beforeinput", event => {
      if (event.inputType.startsWith("format") || event.inputType.includes("List")) event.preventDefault();
    });
    field.addEventListener("input", () => {
      let paragraphBreak = "\n\n";
      if (field.matches("td,th")) paragraphBreak = "<br>";
      else if (field.matches("li,.lp-list-label")) {
        const lineStart = source.lastIndexOf("\n", entry.from - 1) + 1;
        paragraphBreak = "\n" + source.slice(lineStart, entry.from);
      } else if (field.closest("blockquote")) paragraphBreak = "\n> ";
      const markdown = entry.serialize ? entry.serialize() : inlineMarkdown(field, paragraphBreak);
      entry.value = field.innerHTML === "<br>" ? "" : markdown;
      let end = 0;
      const parts = [];
      for (const segment of fields) {
        parts.push(source.slice(end, segment.from), segment.value);
        end = segment.to;
      }
      onChange(parts.join("") + source.slice(end));
    });
  }
  const controls = fields.length ? null : prepareStructured(view, source, onChange);
  appendBlockTools(view, controls, !fields.length);
}

// 块右上角的操作区：「源码」按钮，可选的「编辑…」控件。sourceOnly 时按钮常显。
export function appendBlockTools(view, controls, sourceOnly) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "lp-edit-source";
  button.textContent = "源码";
  button.classList.toggle("source-only", sourceOnly);
  button.setAttribute("aria-label", "编辑此段 Markdown 源码");
  const tools = document.createElement("div");
  tools.className = "lp-block-tools";
  tools.append(...(controls ? [controls, button] : [button]));
  view.appendChild(tools);
}
