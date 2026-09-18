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

export function splitMarkdownBlocks(text) {
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

export function joinMarkdownBlocks(blocks) {
  if (!blocks || !blocks.length) return "";
  return blocks.map(function (b) { return b.text || ""; }).join("\n\n");
}

const LP_MARKER_PREFIX = "<!--inkwell-lp-block:";
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
