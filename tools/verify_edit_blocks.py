#!/usr/bin/env python3
"""不弹窗的编辑模式纯文本检查：分块规则、往返保真、标题判断、引用定义收集。

blocks.js 不引用 DOM，直接用 node 加载；列表分块规则再与后端渲染结果对照。
"""

import json
import os
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inkwell.render import render_markdown

NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const blocks = await import(%s);
const cases = JSON.parse(readFileSync(0, "utf8"));
const out = {
  split: cases.split.map(text => blocks.splitMarkdownBlocks(text).map(block => block.text)),
  roundtrip: cases.split.map(text => blocks.joinMarkdownBlocks(blocks.splitMarkdownBlocks(text))),
  kinds: cases.kinds.map(text => blocks.classifyBlockKind(text)),
  headings: cases.kinds.map(text => blocks.isHeadingBlock(text)),
  references: cases.references.map(item =>
    blocks.withReferenceDefinitions(item.text, item.blocks.map(text => ({ text })), item.own)),
};
process.stdout.write(JSON.stringify(out));
"""

# (原文, 期望分出的块)
SPLIT_CASES = [
    ("- a\n\n- b", ["- a\n\n- b"]),
    ("- a\n\n\n- b", ["- a\n\n\n- b"]),
    ("1. a\n\n2. b\n\n3. c", ["1. a\n\n2. b\n\n3. c"]),
    ("- a\n\n* b\n\n+ c", ["- a\n\n* b\n\n+ c"]),
    ("- a\n\n - b", ["- a\n\n - b"]),
    ("- [ ] a\n\n- [x] b", ["- [ ] a\n\n- [x] b"]),
    ("- a\n- b\n\n- c", ["- a\n- b\n\n- c"]),
    ("- a\n\n    - x\n\n    - y\n\n- b", ["- a\n\n    - x\n\n    - y\n\n- b"]),
    ("- a\n\n    more\n\n- b", ["- a\n\n    more\n\n- b"]),
    ("- a\n\n- b\n\npara", ["- a\n\n- b", "para"]),
    ("text\n\n- a\n\n- b", ["text", "- a\n\n- b"]),
    ("- a\n\n1. b", ["- a", "1. b"]),
    ("1. a\n\n- b", ["1. a", "- b"]),
    ("- a\n\npara\n\n- b", ["- a", "para", "- b"]),
    ("- a\n\n* * *\n\n- b", ["- a", "* * *", "- b"]),
    ("- a\n\n- - -\n\n- b", ["- a", "- - -", "- b"]),
    ("- a\n\n---\n\n- b", ["- a", "---", "- b"]),
    ("- a\n\n# h\n\n- b", ["- a", "# h", "- b"]),
    ("- a\n\n```\nx\n```\n\n- b", ["- a", "```\nx\n```", "- b"]),
    ("1. a\n\n1) b", ["1. a", "1) b"]),
    ("- a\n\n- b\n", ["- a\n\n- b"]),
    ("- a\r\n\r\n- b\r\n\r\npara\r\n", ["- a\n\n- b", "para"]),
]

# 不在上表里、只检查往返保真的写法
ROUNDTRIP_ONLY = [
    "",
    "\n\n- a\n\n- b\n\n\n",
    "- a\r\n\r\n- b",
    "# T\r\n\r\n1. a\r\n\r\n2. b\r\n\r\ntext\r\n",
    "- a\n\n- b\r\n\r\n- c\n",
]

KIND_CASES = [
    ("Title\n=====", "setext:1"),
    ("Title\n---", "setext:2"),
    ("Title\n===  ", "setext:1"),
    ("# Title", "heading:1"),
    ("## Title", "heading:2"),
    ("a | b\n--|--\nc | d", "table"),
    ("| a | b |\n| - | - |\n| c | d |", "table"),
    ("- a\n- b", "list"),
    ("text\n---x", "text"),
    ("plain text", "text"),
]

REFERENCE_CASES = [
    # (本块, 全部块, 本块序号, 期望追加的行)
    ("see [x][Ref]", ["see [x][Ref]", "[ref]: https://example.org"], 0, ["[ref]: https://example.org"]),
    ("see [ref]", ["see [ref]", "[REF]: https://example.org"], 0, ["[REF]: https://example.org"]),
    ("![img][pic]", ["![img][pic]", "[pic]: data:image/png;base64,AAAA", "[other]: https://unused"], 0,
     ["[pic]: data:image/png;base64,AAAA"]),
    ("see [x][ref]", ["see [x][ref]", "```\n[ref]: https://in-fence\n```"], 0, []),
    ("see [x][ref]", ["see [x][ref]", "$$\n[ref]: a\n$$"], 0, []),
    ("see [x][ref]", ["see [x][ref]", "[ref]: https://a", "[ref]: https://b"], 0, ["[ref]: https://a"]),
    ("see [x][ref]\n\nown", ["x", "[ref]: https://own"], 0, ["[ref]: https://own"]),
    ("see [x][ref]", ["see [x][ref]\n[ref]: https://own"], 0, []),
    ("note[^1]", ["note[^1]", "[^1]: footnote"], 0, []),
    ("plain text", ["plain text", "[ref]: https://a"], 0, []),
    ("[x][ref]", ["[x][ref]", "[ref]: https://a\n    \"Title\""], 0, ["[ref]: https://a\n    \"Title\""]),
    ("[x][a] and [y][b]", ["[x][a] and [y][b]", "[a]: https://a\n[b]: https://b"], 0,
     ["[a]: https://a", "[b]: https://b"]),
    ("```\n[ref]\n```", ["```\n[ref]\n```", "[ref]: https://a"], 0, []),
]


class _ListCounter(HTMLParser):
    """统计最外层 ul/ol 的个数。"""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("ul", "ol"):
            if not self.depth:
                self.count += 1
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self.depth -= 1


def top_level_lists(markdown):
    counter = _ListCounter()
    counter.feed(render_markdown(markdown)[0])
    return counter.count


def run_node(cases):
    node = shutil.which("node")
    if not node:
        print("verify_edit_blocks: 本机没有 node，跳过")
        raise SystemExit(0)
    script = NODE_SCRIPT % json.dumps((ROOT / "inkwell/assets/js/edit/blocks.js").as_uri())
    done = subprocess.run(
        [node, "--input-type=module", "-e", script], input=json.dumps(cases),
        capture_output=True, encoding="utf-8", check=False,
    )
    if done.returncode:
        raise SystemExit(done.stderr)
    return json.loads(done.stdout)


IMAGE_SCRIPT = """
const blocks = await import(%s);
const big = "A".repeat(5000000);
const results = {
  nested: blocks.findImages("x ![a](u(1)(2).png) y").map(hit => hit.markdown),
  angle: blocks.findImages("![a](<u (x).png>)").map(hit => hit.markdown),
  title: blocks.findImages('![a](u.png "t (x")').map(hit => hit.markdown),
  escaped: blocks.findImages("![a](u\\\\)x.png) tail").map(hit => hit.markdown),
  apostrophe: blocks.findImages("![a](it's.png) 后文 'x'").map(hit => hit.markdown),
  linkApostrophe: blocks.scanLinkDestination("https://example.org/wiki/O'Brien) 之后", 0),
  quotedParen: blocks.findImages('![a](url "带 ) 的标题") 尾').map(hit => hit.markdown),
  singleQuoted: blocks.findImages("![a](url 'x') 尾").map(hit => hit.markdown),
  two: blocks.findImages("![a](1.png) mid ![b](2.png)").map(hit => hit.alt),
  unclosed: blocks.findImages("![a](u.png and more text").length,
  newline: blocks.findImages("![a](u.png\\nmore)").length,
  fenced: blocks.findImages("```\\n![a](u.png)\\n```\\n![b](v.png)").map(hit => hit.alt),
  huge: blocks.findImages("![a](data:image/png;base64," + big + ") 之后的说明").map(hit => hit.markdown.length),
  hugeUnclosed: blocks.findImages("![a](data:image/png;base64," + big).length,
  scanEnd: blocks.scanLinkDestination("(a(b)c) d", 1),
  removeInline: blocks.textWithoutImage("前 ![a](u.png) 后", blocks.findImageAt("前 ![a](u.png) 后", 3)),
  removeOwnLine: blocks.textWithoutImage("a\\n\\n![a](u.png)\\n\\nb", blocks.findImageAt("a\\n\\n![a](u.png)\\n\\nb", 4)),
};
process.stdout.write(JSON.stringify(results));
"""

EMBED_SCRIPT = """
const blocks = await import(%s);
const uri = data => "data:image/png;base64," + data;
const bigA = "A".repeat(2000), bigB = "B".repeat(3000);
const table = blocks.createEmbedTable();
const collapse = text => blocks.collapseEmbeddedData(text, table);
const restore = text => blocks.restoreEmbeddedData(text, table);
const markerOf = text => text.match(/…\\[内嵌数据 #\\d+ · [^\\]]*\\]/g) || [];

const two = "![a](" + uri(bigA) + ") 与 ![b](" + uri(bigB) + ")";
const twoCollapsed = collapse(two);
const same = "![a](" + uri(bigA) + ")\\n\\n![b](" + uri(bigA) + ")";
const sameCollapsed = collapse(same);
const marker = markerOf(twoCollapsed)[0];
const shortUri = "![s](" + uri("A".repeat(255)) + ")";
const edge = "![s](" + uri("A".repeat(256)) + ")";
const results = {
  twoCollapsed, twoRestored: restore(twoCollapsed),
  twoMarkers: markerOf(twoCollapsed).length,
  sameRestored: restore(sameCollapsed), sameMarkers: new Set(markerOf(sameCollapsed)).size,
  shortKept: collapse(shortUri) === shortUri,
  edgeCollapsed: collapse(edge) !== edge,
  // 记号被整个删掉：对应的数据不再还原
  deleted: restore(twoCollapsed.replace(marker, "")),
  // 记号被改坏：原样保留
  broken: restore(twoCollapsed.replace("#1", "#x")),
  unknownId: restore("![z](" + uri("…[内嵌数据 #99 · 1 KB]") + ")"),
  // 带记号的文本粘贴到别的块仍能还原
  cross: restore("看 ![z](data:image/png;base64," + marker.replace(/ · .*/, "") + " · 2 KB])"),
  plain: collapse("data:text/plain,hello 与 普通文字") === "data:text/plain,hello 与 普通文字",
  noMime: collapse(";base64," + bigA) === ";base64," + bigA,
  roundtripBig: (() => { const text = "前 ![a](" + uri("Z".repeat(4000000)) + ") 后"; const c = collapse(text);
    return { short: c.length < 200, back: restore(c) === text }; })(),
  // 不在 ;base64, 之后的记号文字是正文，原样保留
  markerInProse: restore("记号写成 " + marker + " 这样") === "记号写成 " + marker + " 这样",
  uncollapsedUntouched: restore("没有记号的文本") === "没有记号的文本",
};
results.twoMarkerSample = marker;
process.stdout.write(JSON.stringify(results));
"""


def run_node_script(script):
    node = shutil.which("node")
    done = subprocess.run(
        [node, "--input-type=module", "-e", script % json.dumps((ROOT / "inkwell/assets/js/edit/blocks.js").as_uri())],
        capture_output=True, encoding="utf-8", check=False,
    )
    if done.returncode:
        raise SystemExit(done.stderr)
    return json.loads(done.stdout)


def check_images_and_embeds():
    failures = []
    big_a, big_b = "A" * 2000, "B" * 3000

    def expect(name, got, want):
        if got != want:
            failures.append((name, want, got))

    images = run_node_script(IMAGE_SCRIPT)
    expect("image:nested", images["nested"], ["![a](u(1)(2).png)"])
    expect("image:angle", images["angle"], ["![a](<u (x).png>)"])
    expect("image:title", images["title"], ['![a](u.png "t (x")'])
    expect("image:escaped", images["escaped"], ["![a](u\\)x.png)"])
    expect("image:apostrophe", images["apostrophe"], ["![a](it's.png)"])
    expect("image:link-apostrophe", images["linkApostrophe"], len("https://example.org/wiki/O'Brien)"))
    expect("image:quoted-paren", images["quotedParen"], ['![a](url "带 ) 的标题")'])
    expect("image:single-quoted", images["singleQuoted"], ["![a](url 'x')"])
    expect("image:two", images["two"], ["a", "b"])
    expect("image:unclosed", images["unclosed"], 0)
    expect("image:newline", images["newline"], 0)
    expect("image:fenced", images["fenced"], ["b"])
    expect("image:huge", images["huge"], [len("![a](data:image/png;base64,") + 5000000 + 1])
    expect("image:huge-unclosed", images["hugeUnclosed"], 0)
    expect("image:scan-end", images["scanEnd"], 7)
    expect("image:remove-inline", images["removeInline"]["text"], "前  后")
    expect("image:remove-own-line", images["removeOwnLine"]["text"], "a\n\nb")

    embeds = run_node_script(EMBED_SCRIPT)
    expect("embed:two-restored", embeds["twoRestored"], "![a](data:image/png;base64,%s) 与 ![b](data:image/png;base64,%s)" % (big_a, big_b))
    expect("embed:two-collapsed-short", len(embeds["twoCollapsed"]) < 200, True)
    expect("embed:two-markers", embeds["twoMarkers"], 2)
    expect("embed:same-restored", embeds["sameRestored"], "![a](data:image/png;base64,%s)\n\n![b](data:image/png;base64,%s)" % (big_a, big_a))
    expect("embed:same-one-marker", embeds["sameMarkers"], 1)
    expect("embed:short-kept", embeds["shortKept"], True)
    expect("embed:edge-collapsed", embeds["edgeCollapsed"], True)
    expect("embed:deleted", embeds["deleted"], "![a](data:image/png;base64,) 与 ![b](data:image/png;base64,%s)" % big_b)
    broken = embeds["broken"]
    expect("embed:broken", ("#x" in broken, big_a in broken, big_b in broken), (True, False, True))
    expect("embed:unknown-id", embeds["unknownId"], "![z](data:image/png;base64,…[内嵌数据 #99 · 1 KB])")
    expect("embed:cross", embeds["cross"], "看 ![z](data:image/png;base64,%s)" % big_a)
    expect("embed:plain", embeds["plain"], True)
    expect("embed:no-mime", embeds["noMime"], True)
    expect("embed:big-roundtrip", embeds["roundtripBig"], {"short": True, "back": True})
    expect("embed:marker-in-prose", embeds["markerInProse"], True)
    expect("embed:no-marker",embeds["uncollapsedUntouched"], True)
    return failures


def main():
    split_texts = [text for text, _ in SPLIT_CASES] + ROUNDTRIP_ONLY
    result = run_node({
        "split": split_texts,
        "kinds": [text for text, _ in KIND_CASES],
        "references": [{"text": text, "blocks": blocks, "own": own}
                       for text, blocks, own, _ in REFERENCE_CASES],
    })
    failures = []
    for (text, expected), got in zip(SPLIT_CASES, result["split"]):
        if got != expected:
            failures.append(("split", text, expected, got))
    for text, got in zip(split_texts, result["roundtrip"]):
        if got != text:
            failures.append(("roundtrip", text, text, got))
    for (text, expected), got, heading in zip(KIND_CASES, result["kinds"], result["headings"]):
        if got != expected:
            failures.append(("kind", text, expected, got))
        if heading != expected.startswith(("heading:", "setext:")):
            failures.append(("heading", text, expected, heading))
    for (text, _, _, lines), got in zip(REFERENCE_CASES, result["references"]):
        expected = text + "\n\n" + "\n".join(lines) if lines else text
        if got != expected:
            failures.append(("references", text, expected, got))

    # 分块不能改变渲染出来的列表数量：合并的宽松列表仍是同一个列表，不同类型仍是两个。
    for text, _ in SPLIT_CASES:
        blocks = result["split"][split_texts.index(text)]
        whole = top_level_lists(text.replace("\r\n", "\n"))
        parts = sum(top_level_lists(block) for block in blocks)
        if whole != parts:
            failures.append(("backend-lists", text, whole, parts))

    # 追加的定义让链接渲染出来，且不产生可见文字
    text, blocks, own, _ = REFERENCE_CASES[0]
    html = render_markdown(result["references"][0])[0]
    if '<a href="https://example.org"' not in html or "[ref]" in html or "example.org</p>" in html:
        failures.append(("reference-render", text, "link without definition text", html))

    failures.extend(check_images_and_embeds())

    for failure in failures:
        print("FAIL", failure)
    if failures:
        raise SystemExit(1)
    print("verify_edit_blocks: PASS")


if __name__ == "__main__":
    main()
