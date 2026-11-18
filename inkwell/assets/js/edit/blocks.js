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

export function findImageAt(text, pos) {
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
    let j = text.indexOf("\n", start);
    while (j !== -1) {
      const k = j + 1;
      if (k >= n) break;
      if (text[k] === "\n") {
        let next = k;
        while (text[next] === "\n") next++;
        if (!canContinue || !(text.startsWith("    ", next) || text[next] === "\t")) break;
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
