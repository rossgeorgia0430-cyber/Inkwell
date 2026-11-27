// Live Preview 的纯文本解析：围栏/公式块识别、Markdown 分块、块标记穿插与还原。
// 不引用 DOM，方便脱离浏览器环境单独测试（回归覆盖见 tools/verify_edit_live.py）。

// —— 围栏 / 行内代码保护：找图片、插片段时不能破坏代码块边界 ——
export function findProtectedRanges(text) {
  var ranges = [];
  var i = 0, n = text.length;
  while (i < n) {
    var fenceEnd = _matchFenceEnd(text, i);
    if (fenceEnd != null) {
      ranges.push({ start: i, end: fenceEnd, kind: "fence" });
      i = fenceEnd;
      continue;
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

// 从 "(" 之后扫到与它配对的 ")"，返回 ")" 的下一位；没有配对返回 -1。
// 逐字符线性扫描：内嵌图片的地址动辄几 MB，用正则回溯会在 WebKit 里超限静默失败、在 V8 里抛 RangeError。
export function scanLinkDestination(text, from) {
  var depth = 1;
  var inAngle = false;
  var quote = null;
  for (var p = from; p < text.length; p++) {
    var c = text.charAt(p);
    if (c === "\\") { p++; continue; }
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if (inAngle) {
      if (c === ">") inAngle = false;
      continue;
    }
    if (c === "<") inAngle = true;
    // 标题和地址之间必须有空白，地址里的撇号不是引号
    else if ((c === '"' || c === "'") && p > from && /\s/.test(text.charAt(p - 1))) quote = c;
    else if (c === "(") depth++;
    else if (c === ")" && --depth === 0) return p + 1;
    else if (c === "\n" && depth === 1) return -1;
  }
  return -1;
}

// 文本里所有行内图片 ![alt](地址)，按出现顺序；围栏和行内代码里的不算。
export function findImages(text) {
  var ranges = findProtectedRanges(text);
  var re = /!\[([^\]]*)\]\(/g;
  var hits = [];
  var m;
  while ((m = re.exec(text)) !== null) {
    if (inProtectedRange(ranges, m.index)) continue;
    var end = scanLinkDestination(text, re.lastIndex);
    if (end < 0) continue;
    hits.push({
      start: m.index,
      end: end,
      alt: m[1],
      dest: text.slice(re.lastIndex, end - 1),
      markdown: text.slice(m.index, end)
    });
    re.lastIndex = end;
  }
  return hits;
}

export function findImageAt(text, pos) {
  return findImages(text).find(function (hit) { return pos >= hit.start && pos <= hit.end; }) || null;
}

// 删掉一处图片：图片独占一行时连同该行删掉，并避免留下多余的空行。返回新文本和删除处的位置。
export function textWithoutImage(text, hit) {
  var lineStart = text.lastIndexOf("\n", hit.start - 1) + 1;
  var lineEnd = text.indexOf("\n", hit.end);
  if (lineEnd < 0) lineEnd = text.length;
  var onlyImage = text.slice(lineStart, lineEnd).trim() === hit.markdown;
  var delStart = onlyImage ? lineStart : hit.start;
  var delEnd = onlyImage ? (lineEnd < text.length ? lineEnd + 1 : lineEnd) : hit.end;
  if (onlyImage && delStart >= 2 && text.slice(delStart - 2, delStart) === "\n\n") delStart -= 1;
  return { text: text.slice(0, delStart) + text.slice(delEnd), caret: delStart };
}

// —— 源码框里折叠内嵌图片数据：textarea 塞几 MB 的 base64 会卡很久，也没法读 ——
// table 是编辑会话级的：记号在整个会话里唯一，带记号的文本剪切粘贴到别的块也能还原。
const EMBED_MIN_CHARS = 256;
const EMBED_MARKER_HEAD = "…[内嵌数据 #";
// 折叠产生的记号一定紧跟在 ;base64, 之后；别处出现的同样文字是正文，不还原。
const EMBED_MARKER = /(;base64,)…\[内嵌数据 #(\d+) · [^\]\n]*\]/g;
const DATA_URI_HEAD = /^data:[\w.+-]+\/[\w.+-]+(?:;[\w.=+-]+)*$/;

export function createEmbedTable() {
  return { byId: new Map(), idOf: new Map() };
}

function isBase64Char(code) {
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122) || (code >= 48 && code <= 57)
    || code === 43 || code === 47 || code === 61;
}

function formatDataSize(chars) {
  var bytes = Math.floor(chars * 3 / 4);
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return Math.round(bytes / 1024) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function embedMarker(data, table) {
  var id = table.idOf.get(data);
  if (id === undefined) {
    id = table.byId.size + 1;
    table.byId.set(id, data);
    table.idOf.set(data, id);
  }
  return EMBED_MARKER_HEAD + id + " · " + formatDataSize(data.length) + "]";
}

export function collapseEmbeddedData(text, table) {
  var out = "";
  var copied = 0;
  var searchFrom = 0;
  var at;
  while ((at = text.indexOf(";base64,", searchFrom)) !== -1) {
    var dataStart = at + 8;
    var dataEnd = dataStart;
    while (dataEnd < text.length && isBase64Char(text.charCodeAt(dataEnd))) dataEnd++;
    searchFrom = dataEnd;
    var head = text.lastIndexOf("data:", at);
    if (dataEnd - dataStart < EMBED_MIN_CHARS || head < 0 || at - head > 100
        || !DATA_URI_HEAD.test(text.slice(head, at))) continue;
    out += text.slice(copied, dataStart) + embedMarker(text.slice(dataStart, dataEnd), table);
    copied = dataEnd;
  }
  return copied ? out + text.slice(copied) : text;
}

// 记号被删掉或改坏就不再还原，对应的数据随之丢弃。
export function restoreEmbeddedData(text, table) {
  if (text.indexOf(EMBED_MARKER_HEAD) === -1) return text;
  return text.replace(EMBED_MARKER, function (marker, head, id) {
    var data = table.byId.get(Number(id));
    return data === undefined ? marker : head + data;
  });
}

// —— 块切分：空行分隔，围栏 / $$ 公式视为原子块 ——
function _atLineStart(text, pos) {
  return pos === 0 || text.charAt(pos - 1) === "\n";
}

// 围栏起止扫描：findProtectedRanges（找保护区间）与 splitMarkdownBlocks（找块边界）
// 都需要同一套“从某行起点判断是否为围栏、并找到其闭合位置”的逻辑，这里只留一份。
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
  const opener = text.slice(j, j + 2);
  if (opener !== "$$" && opener !== "\\[") return null;
  const closer = opener === "$$" ? "$$" : "\\]";
  j += 2;
  var lineEnd = j;
  while (lineEnd < n && text.charAt(lineEnd) !== "\n") lineEnd++;
  var after = text.slice(j, lineEnd).replace(/\s+$/, "");
  if (after.length >= 2 && after.slice(-2) === closer) {
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
    if (line === closer) {
      if (j < n && text.charAt(j) === "\n") j++;
      return j;
    }
    if (j < n && text.charAt(j) === "\n") j++;
  }
  return n;
}

export function classifyBlockKind(raw) {
  var t = (raw || "").replace(/^\s+/, "");
  if (/^(`{3,}|~{3,})/.test(t)) {
    if (/^(`{3,}|~{3,})\s*mermaid\b/i.test(t)) return "fence-mermaid";
    return "fence";
  }
  if (/^\$\$/.test(t)) return "math";
  // ATX 标题
  var hm = /^(#{1,6})\s+\S/.exec(t);
  if (hm) return "heading:" + hm[1].length;
  // Setext 标题：首行文字，下一行只有 = 或 -。源码框保持正文样式，所以不与 ATX 标题共用 heading 类别。
  var sm = /^[^\n]+\n(=+|-+)[ \t]*(?:\n|$)/.exec(t);
  if (sm) return "setext:" + (sm[1][0] === "=" ? 1 : 2);
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

export function isHeadingBlock(text) {
  return /^(?:heading|setext):/.test(classifyBlockKind(text));
}

const THEMATIC_BREAK = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;

// 后端（sane_lists）只在无序与有序之间分列表；同一种列表里不同的无序符号仍是同一个列表。
function listKindAt(text, pos) {
  const end = text.indexOf("\n", pos);
  const line = text.slice(pos, end === -1 ? text.length : end);
  if (THEMATIC_BREAK.test(line)) return null;
  if (/^ {0,3}[-*+][ \t]/.test(line)) return "bullet";
  if (/^ {0,3}\d+\.[ \t]/.test(line)) return "ordered";
  return null;
}

function htmlBlockEnd(text, start) {
  if (text.startsWith("<!--", start)) {
    const end = text.indexOf("-->", start + 4);
    return end === -1 ? text.length : end + 3;
  }
  const opening = / {0,3}<(div|section|details|table|blockquote|pre|ul|ol|article|figure|aside)(?=[\s>])/iy;
  opening.lastIndex = start;
  const tag = opening.exec(text)?.[1];
  if (!tag) return null;
  const tags = new RegExp("<(/?)" + tag + "(?=[\\s>])(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", "ig");
  tags.lastIndex = start;
  let depth = 0, match;
  while ((match = tags.exec(text))) {
    depth += match[1] ? -1 : 1;
    if (!depth) return tags.lastIndex;
  }
  return text.length;
}

export function splitMarkdownBlocks(text) {
  const source = String(text ?? "");
  text = source.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  var n = text.length;
  var blocks = [];
  const endings = source.includes("\r") ? source.match(/\r\n|\r|\n/g) || [] : null;
  let newlineIndex = 0;
  let previousEnd = 0;
  function original(slice) {
    return endings ? slice.replace(/\n/g, () => endings[newlineIndex++]) : slice;
  }
  function addBlock(start, end) {
    const body = text.slice(start, end).replace(/\n+$/, "");
    const before = original(text.slice(previousEnd, start));
    const raw = original(body);
    blocks.push({ text: body, initialText: body, raw, before });
    previousEnd = start + body.length;
  }
  var i = 0;
  while (i < n) {
    while (i < n && text.charAt(i) === "\n") i++;
    if (i >= n) break;
    var start = i;
    var fenceEnd = _matchFenceEnd(text, i);
    if (fenceEnd != null) {
      addBlock(start, fenceEnd);
      i = fenceEnd;
      continue;
    }
    var mathEnd = _matchMathEnd(text, i);
    if (mathEnd != null) {
      addBlock(start, mathEnd);
      i = mathEnd;
      continue;
    }
    const htmlEnd = htmlBlockEnd(text, start);
    if (htmlEnd !== null && (htmlEnd === n || text[htmlEnd] === "\n")) {
      addBlock(start, htmlEnd);
      i = htmlEnd;
      continue;
    }
    const container = /(?: {0,3}(?:[-*+]|\d+\.)[ \t]| {4}|\t)/y;
    container.lastIndex = start;
    const canContinue = container.test(text);
    const listKind = listKindAt(text, start);
    let j = text.indexOf("\n", start);
    while (j !== -1) {
      const k = j + 1;
      if (k >= n) break;
      if (text[k] === "\n") {
        let next = k;
        while (text[next] === "\n") next++;
        const indented = canContinue && (text.startsWith("    ", next) || text[next] === "\t");
        // 空行隔开的同类列表项在渲染里仍是同一个（宽松）列表，不能拆成两块。
        if (!indented && !(listKind && listKindAt(text, next) === listKind)) break;
        j = text.indexOf("\n", next);
        continue;
      }
      if (_matchFenceEnd(text, k) != null || _matchMathEnd(text, k) != null) break;
      j = text.indexOf("\n", k);
    }
    if (j === -1) j = n;
    addBlock(start, j);
    i = j;
    while (i < n && text.charAt(i) === "\n") i++;
  }
  if (!blocks.length) addBlock(0, 0);
  blocks[0].newline = endings?.[0] || "\n";
  blocks[blocks.length - 1].after = original(text.slice(previousEnd));
  return blocks;
}

export function joinMarkdownBlocks(blocks) {
  if (!blocks || !blocks.length) return "";
  const newline = blocks[0].newline || "\n";
  return blocks.map((block, index) => {
    const before = block.before ?? (index ? newline + newline : "");
    const raw = block.raw ?? "";
    const body = block.text === block.initialText
      ? raw : block.text.replace(/\n/g, (raw.match(/\r\n|\r|\n/) || [newline])[0]);
    return before + body + (block.after || "");
  }).join("");
}

function isCodeOrMathBlock(text) {
  return /^\s*(?:`{3,}|~{3,}|\$\$|\\\[)/.test(text);
}

function normalizeReferenceLabel(label) {
  return label.replace(/\s+/g, " ").trim().toLowerCase();
}

// 单块单独渲染时，引用式链接/图片的定义在别的块里。把本块用到的那几条定义行追加到末尾
// （定义本身不渲染出可见内容）；定义可能带体积很大的 data URI，所以只取用到的。
export function withReferenceDefinitions(text, blocks, ownIndex) {
  if (isCodeOrMathBlock(text)) return text;
  const wanted = new Set();
  for (const match of text.matchAll(/\[([^\[\]\n]+)\]/g)) wanted.add(normalizeReferenceLabel(match[1]));
  if (!wanted.size) return text;
  const found = new Map();
  const definition = /^ {0,3}\[([^\[\]\n]+)\]:[ \t]*(?=\S)/gm;
  blocks.forEach(function (block, index) {
    const body = block.text || "";
    if (index === ownIndex || body.indexOf("]:") === -1 || isCodeOrMathBlock(body)) return;
    definition.lastIndex = 0;
    let match;
    while ((match = definition.exec(body))) {
      const label = normalizeReferenceLabel(match[1]);
      if (label[0] === "^" || !wanted.has(label) || found.has(label)) continue;
      let end = body.indexOf("\n", definition.lastIndex);
      if (end === -1) end = body.length;
      // 标题可以单独写在下一行
      const title = /^\n[ \t]+["'(][^\n]*/.exec(body.slice(end, end + 1000));
      if (title) end += title[0].length;
      found.set(label, body.slice(match.index, end));
    }
  });
  return found.size ? text + "\n\n" + [...found.values()].join("\n") : text;
}

// 区分文档自带的注释与当前编辑会话的分隔标记。
const markerId = crypto.getRandomValues(new Uint32Array(1))[0].toString() + "0";
const LP_MARKER_PREFIX = "<!--inkwell-lp-block:" + markerId;
const LP_MARKER_SUFFIX = "-->";

export function bufferWithMarkers(blocks) {
  var parts = [];
  for (var i = 0; i < blocks.length; i++) {
    if (i > 0) parts.push("\n\n" + LP_MARKER_PREFIX + i + LP_MARKER_SUFFIX + "\n\n");
    parts.push(blocks[i].text || "");
  }
  return parts.join("");
}

export function splitPreviewHtml(html, count) {
  html = html || "";
  var re = new RegExp(LP_MARKER_PREFIX + "\\d+-->", "g");
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
