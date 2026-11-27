import { renderMath } from "../reader.js";
import { prepareStructured } from "./structured.js";
import { scanLinkDestination } from "./blocks.js";

// 只有渲染内容能与原文逐段对应时才开放直接编辑，保留其余 Markdown 语法。
const delimiters = { STRONG: "**", B: "**", EM: "*", I: "*", CODE: "`" };

const originalRuns = new WeakMap();

// 图片和行内公式的原始 Markdown 按序号存在这里，序号写在节点的 data-lp-atom 上。
// 浏览器合并段落时会重建节点，以节点为键的映射随之丢失，data-* 属性却会保留下来。
const atomRuns = new WeakMap();

function registerAtom(node, markdown) {
  const view = node.closest(".lp-view");
  let runs = atomRuns.get(view);
  if (!runs) atomRuns.set(view, runs = []);
  node.dataset.lpAtom = String(runs.push(markdown) - 1);
}

function atomMarkdown(node) {
  const view = node.closest(".lp-view");
  const index = node.dataset.lpAtom;
  return view && index !== undefined ? atomRuns.get(view)?.[Number(index)] : undefined;
}

function setAtomMarkdown(node, markdown) {
  atomRuns.get(node.closest(".lp-view"))[Number(node.dataset.lpAtom)] = markdown;
}

// 链接和图片 ] 之后的 (地址) 或 [引用]，返回结尾位置；没有返回 -1。
const referenceLabel = /\[[^\]\n]*\]/y;
function matchLinkTail(source, at) {
  if (source[at] === "(") return scanLinkDestination(source, at + 1);
  referenceLabel.lastIndex = at;
  return referenceLabel.test(source) ? referenceLabel.lastIndex : -1;
}

const imageHead = /!\[([^\]\n]*)\]/y;
function matchImage(source, offset) {
  imageHead.lastIndex = offset;
  const head = imageHead.exec(source);
  if (!head) return null;
  const end = matchLinkTail(source, imageHead.lastIndex);
  return end < 0 ? null : { alt: head[1], end };
}

const entities = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": "\u00a0" };
const lineBreakTag = /<br[ \t]*\/?>/iy;
const listMarkerPrefix = /^\s*(?:[-*+]|\d+\.)[ \t]+/;

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
  registerAtom(node, raw);
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
      setAtomMarkdown(node, dollars ? "$" + value + "$" : "\\(" + value + "\\)");
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
      const image = matchImage(source, offset);
      if (!image || image.alt !== node.alt) return -1;
      const atom = document.createElement("span");
      atom.contentEditable = "false";
      node.replaceWith(atom);
      atom.append(node);
      registerAtom(atom, source.slice(offset, image.end));
      offset = image.end;
      continue;
    }
    let mark = delimiters[name];
    if (name === "A") mark = "[";
    if (name === "BR") {
      lineBreakTag.lastIndex = offset;
      const tag = lineBreakTag.exec(source);
      if (tag) {
        originalRuns.set(node, { markdown: tag[0] });
        offset += tag[0].length;
        continue;
      }
    }
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
      if (source[offset] !== "]") return -1;
      const tailEnd = matchLinkTail(source, offset + 1);
      close = tailEnd < 0 ? "]" : source.slice(offset, tailEnd);
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
    if (name === "BUTTON") continue; // 界面控件，不是正文
    const atom = atomMarkdown(node);
    if (atom !== undefined) {
      result += atom;
      continue;
    }
    // 图片和公式找不到原文就不能猜：返回 null，由调用方放弃这次回写。
    if (name === "IMG" || node.matches(".math-inline")) return null;
    const original = originalRuns.get(node);
    if (original?.markdown !== undefined) {
      result += original.markdown;
      continue;
    }
    const inner = inlineMarkdown(node, paragraphBreak);
    if (inner === null) return null;
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
    else if (name === "BR" && node.nextSibling) result += paragraphBreak === "<br>" ? "<br>" : "  \n";
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

const isBlank = character => character === " " || character === "\t";

// 反引号包起来的代码里的竖线不分列；没有闭合的反引号按普通字符处理。
function skipCodeSpan(source, index, end) {
  let open = index;
  while (open < end && source[open] === "`") open++;
  for (let j = open; j < end;) {
    if (source[j] !== "`") { j++; continue; }
    let close = j;
    while (close < end && source[close] === "`") close++;
    if (close - j === open - index) return close;
    j = close;
  }
  return open;
}

// 一行表格原文按未转义的竖线切成单元格，返回各单元格在原文中的范围（不含竖线）。
function splitTableRow(source, lineStart, lineEnd) {
  let from = lineStart;
  while (from < lineEnd && isBlank(source[from])) from++;
  if (source[from] === "|") from++;
  let end = lineEnd;
  while (end > from && isBlank(source[end - 1])) end--;
  if (end > from && source[end - 1] === "|" && source[end - 2] !== "\\") end--;
  const segments = [];
  let segmentFrom = from;
  for (let i = from; i < end; i++) {
    if (source[i] === "\\") i++;
    else if (source[i] === "`") i = skipCodeSpan(source, i, end) - 1;
    else if (source[i] === "|") {
      segments.push({ from: segmentFrom, to: i });
      segmentFrom = i + 1;
    }
  }
  segments.push({ from: segmentFrom, to: end });
  return segments;
}

// 单元格内容与原文逐字符对得上才开放编辑；对不上（含缺失的单元格）的整格保持原样、不可编辑。
function mapTableCell(cell, source, segment) {
  if (!segment) return null;
  let from = segment.from, end = segment.to;
  while (from < end && isBlank(source[from])) from++;
  while (end > from && isBlank(source[end - 1])) end--;
  const to = matchInline(cell, source, from);
  return to === end ? { field: cell, from, to, value: source.slice(from, to) } : null;
}

// 表格按行对应原文：表头在第一行，第二行是分隔行，之后每行对应一个正文行。
function mapTable(table, source, offset) {
  const rows = [...table.rows];
  const blanks = /\s*/y;
  blanks.lastIndex = offset;
  blanks.exec(source);
  const lines = [];
  for (let start = blanks.lastIndex; lines.length <= rows.length && start <= source.length;) {
    const end = source.indexOf("\n", start);
    lines.push({ start, end: end === -1 ? source.length : end });
    start = lines[lines.length - 1].end + 1;
  }
  if (lines.length <= rows.length) return null;
  const cells = [];
  rows.forEach((row, index) => {
    const line = lines[index ? index + 1 : 0];
    const segments = splitTableRow(source, line.start, line.end);
    [...row.cells].forEach((cell, column) => {
      const entry = mapTableCell(cell, source, segments[column]);
      if (entry) cells.push(entry);
    });
  });
  return { cells, end: lines[rows.length].end };
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
  const mappedTables = new Set();
  for (const field of fields) {
    if (field.matches("td,th")) {
      const table = field.closest("table");
      if (mappedTables.has(table)) continue;
      mappedTables.add(table);
      const result = mapTable(table, source, offset);
      if (!result) return [];
      mapped.push(...result.cells);
      offset = result.end;
      continue;
    }
    let prefix;
    if (/^H[1-6]$/.test(field.tagName)) prefix = /^\s*(?:#{1,6}[ \t]+)?/;
    else if (field.matches("li,.lp-list-label,li > p:first-child")) prefix = listMarkerPrefix;
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

// onRevert：字段内容无法可靠地写回 Markdown 时调用，由调用方把这一块恢复成上一次已知的源码。
export function prepareVisualBlock(view, source, onChange, onRevert) {
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
      // 浏览器重建图片、公式节点后可能丢掉不可编辑标记
      for (const atom of field.querySelectorAll("[data-lp-atom]")) {
        if (atom.getAttribute("contenteditable") !== "false") atom.contentEditable = "false";
      }
      let paragraphBreak = "\n\n";
      if (field.matches("td,th")) paragraphBreak = "<br>";
      else if (field.matches("li,.lp-list-label,li > p")) {
        const lineStart = source.lastIndexOf("\n", entry.from - 1) + 1;
        paragraphBreak = (field.tagName === "P" ? "\n\n" : "\n") + source.slice(lineStart, entry.from);
      } else if (field.closest("blockquote")) paragraphBreak = "\n> ";
      const markdown = entry.serialize ? entry.serialize() : inlineMarkdown(field, paragraphBreak);
      if (markdown === null) {
        onRevert();
        return;
      }
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
