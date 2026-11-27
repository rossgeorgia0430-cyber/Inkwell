#!/usr/bin/env python3
"""真实 WebView2 回归：Obsidian 风格 Live Preview 编辑模式的安全网。

覆盖：进入/退出编辑、块数与激活、就地修改并经 getText() 校验、插入代码块/
公式、删图、保存落盘、外部改写文件后的保存冲突路径（拒绝覆盖 / 强制覆盖）；
另外驱动真实浏览器里的 window.__ink.edit.splitBlocks / joinBlocks /
findImageAt / findProtectedRanges，覆盖 Live Preview 分块与图片边界识别的
纯逻辑样例，避免另起脚本重抄一遍 JS 逻辑。
"""
import base64
import json
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview

from inkwell import server as S
from inkwell.api import Api
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_edit_live_result.json")

PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

DOC = """
# Edit Live Test


一段正文用于测试 **Live Preview**。

- 清单项目
- _强调_

| 列名 | 数值 |
| --- | ---: |
| 内容 | -12 |

![pixel](images/pixel.png)

```python
print(1)
```

$$
E = mc^2
$$

~~~mermaid
flowchart LR
A --> B
~~~

<div><b>HTML text</b></div>

Setext title
------------

1. Parent
    - Child

Mixed __style__, $a+b$ and ![inline](images/pixel.png) end.

[visible][ref]

[ref]: https://example.org "Title"
"""


# 表格与宽松列表的各种写法：不改动时回写全文必须与原文相同；改某个可编辑字段时只多出一处插入，
# 且落在该字段所在的块里。名字 -> (原文, 可直接编辑的单元格数, 单元格总数)
FIDELITY_CASES = {
    "对齐空格": ("| 名称   | 类型  |\n| ------ | ----- |\n| id     | int   |\n| name   | str   |", 6, 6),
    "无首尾竖线": ("名称 | 类型\n--- | ---\nid | int", 4, 4),
    "换行标签": ("| 键 | 值 |\n| --- | --- |\n| 多行 | 一<br>二 |", 4, 4),
    "自闭合换行": ("| 键 | 值 |\n| --- | --- |\n| 甲 | 一<br/>二 |\n| 乙 | 三<BR />四 |", 6, 6),
    "行内标签": ("| 键 | 值 |\n| --- | --- |\n| 颜色 | <span>红</span> 与 <kbd>Ctrl</kbd> |", 3, 4),
    "实体": ("| 键 | 值 |\n| --- | --- |\n| 空格 | a&nbsp;b &lt;x&gt; |", 4, 4),
    "转义竖线与代码竖线": ("| 键 | 值 |\n| --- | --- |\n| 竖线 | a \\| b |\n| 代码 | `a\\|b` 与 `x|y` |", 6, 6),
    "自动链接": ("| 键 | 值 |\n| --- | --- |\n| 链接 | <https://example.org> |", 3, 4),
    "尖括号文本": ("| 键 | 值 |\n| --- | --- |\n| 泛型 | Map<K, V> 与 a < b |", 3, 4),
    "单元格不齐": ("| 甲 | 乙 | 丙 |\n| --- | --- | --- |\n| 1 | 2 |\n| 3 |", 6, 9),
    "空单元格": ("| 甲 | 乙 |\n| --- | --- |\n|  | 2 |\n| 3 |  |", 6, 6),
    "缩进与不规则空格": ("  | a  |   b |\n  |---|:-:|\n  | c |  d  |\n  | e | f", 6, 6),
    "公式与货币": ("| 键 | 值 |\n| --- | --- |\n| 范数 | $a+b$ 与 $5 到 $10 |", 4, 4),
    "混排": ("| 键 | 值 |\n| --- | --- |\n| 代码 | **`T<K>`** 与 ~~旧~~ [文档](https://e.com \"标题\") |", 4, 4),
    "图片单元格": ("| 键 | 值 |\n| --- | --- |\n| 图 | ![icon](images/pixel.png) |", 4, 4),
}
FIDELITY_LISTS = {
    "宽松无序": "- a\n\n- b\n\n- c",
    "宽松有序": "1. a\n\n2. b\n\n3. c",
    "混用无序符号": "- a\n\n* b\n\n+ c",
    "宽松嵌套": "- a\n\n    - x\n\n    - y\n\n- b",
    "宽松多段": "- a\n\n    more\n\n- b",
}


def fidelity_doc():
    parts = []
    for name, (source, _, _) in FIDELITY_CASES.items():
        parts += ["### " + name, source]
    for name, source in FIDELITY_LISTS.items():
        parts += ["### " + name, source]
    return "\n\n".join(parts) + "\n"


FIDELITY_JS = r"""
((doc) => {
  const edit = window.__ink.edit;
  const root = document.querySelector('#editorLive');
  const flat = text => text.replace(/\r\n/g, '\n');
  const original = edit.getText();
  const blocks = edit.splitBlocks(original).map(block => block.text);
  const problems = [];
  if (original !== doc) problems.push('不改动时回写全文与原文不同');
  const editableSelector = '[contenteditable="true"],[contenteditable="plaintext-only"]';
  const fields = [...root.querySelectorAll(editableSelector)].filter(el => !el.parentElement.closest(editableSelector));
  const source = flat(original);
  for (const el of fields) {
    const index = Number(el.closest('.lp-block').dataset.idx);
    const start = source.indexOf(blocks[index]);
    const node = document.createTextNode('Z');
    el.appendChild(node);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    const edited = flat(edit.getText());
    node.remove();
    el.dispatchEvent(new Event('input', {bubbles: true}));
    let at = 0;
    while (at < source.length && source[at] === edited[at]) at++;
    const label = blocks[index].slice(0, 30) + ' / ' + el.tagName;
    if (edited.length !== source.length + 1 || edited[at] !== 'Z' || edited.slice(0, at) + edited.slice(at + 1) !== source) {
      problems.push('改一个字段后不只多出一处插入：' + label);
    } else if (at < start || at > start + blocks[index].length) {
      problems.push('插入落到了块外：' + label);
    }
    if (edit.getText() !== original) problems.push('撤销改动后全文与原文不同：' + label);
  }
  const tables = [...root.querySelectorAll('table')].map(table => {
    const cells = [...table.querySelectorAll('th,td')];
    const block = table.closest('.lp-block');
    return {
      name: blocks[Number(block.dataset.idx) - 1].replace('### ', ''),
      cells: cells.length,
      editable: cells.filter(cell => cell.isContentEditable).length,
      leaked: cells.filter(cell => !cell.isContentEditable && cell.hasAttribute('contenteditable')).length,
    };
  });
  const lists = [...root.querySelectorAll('.lp-block')].filter(b => b.querySelector('ul,ol')).map(block => ({
    name: blocks[Number(block.dataset.idx) - 1].replace('### ', ''),
    paragraphs: block.querySelectorAll('li > p').length,
    editable: block.querySelectorAll('li > p[contenteditable]').length,
  }));
  return {problems, tables, lists};
})(__DOC__)
"""


def _noisy_png_uri(chars, color):
    """约 chars 个字符的 PNG data URI：附加不可压缩的辅助块把体积撑够。"""
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes(color) * 8
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
           + chunk(b"prVt", os.urandom(chars * 3 // 4)) + chunk(b"IDAT", zlib.compress(row * 8)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def select_image(window, alt):
    """单击图片：只选中并给出选中框，不打开灯箱。"""
    js(window, """(() => {
        const img = document.querySelector('#editorLive img[alt=%s]');
        for (const type of ['mousedown', 'mouseup', 'click'])
            img.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true}));
    })()""" % alt)
    assert js(window, "document.querySelector('#editorLive img[alt=%s]').classList.contains('is-selected')" % alt), "单击后没有选中框"
    assert not js(window, "window.__ink.image.state().open"), "编辑态单击不该打开灯箱"


def check_embedded_images(window, res):
    """含大 data URI 的块：可直接编辑、不改动时全文不变、删除选中的独立图片、源码框折叠、回车退格不丢图。"""
    inline = _noisy_png_uri(1_000_000, (200, 40, 40))
    solo = _noisy_png_uri(300_000, (40, 200, 40))
    tiny = _noisy_png_uri(0, (10, 10, 10))
    blocks = [
        "# 图片阶段",
        "前文 ![inl](%s) 后文说明" % inline,
        "![solo](%s)" % solo,
        "甲 ![a](%s) 乙 ![b](%s)" % (tiny, tiny),
        "尾段",
    ]
    doc = "\n\n".join(blocks) + "\n"
    js(window, "document.querySelectorAll('#editorLive img').forEach(i => i.dataset.stale = '1')")
    js(window, "window.__ink.edit.setText(%s)" % json.dumps(doc))
    assert wait_js(window, "document.querySelectorAll('#editorLive img[alt=inl]:not([data-stale])').length === 1", timeout=60)
    assert js(window, "window.__ink.edit.getText()") == doc, "不改动时回写全文与原文不同"
    editable = "#editorLive .lp-block[data-idx=\"%d\"] [contenteditable=\"true\"]"
    assert js(window, "!!document.querySelector(%s)" % json.dumps(editable % 1)), "含大图片的段落不能直接编辑"

    # 回车再退格：图片语法留在源码里，界面控件文字不能混进正文
    assert js(window, r"""
(() => {
  const field = document.querySelector(%s);
  field.focus();
  const range = document.createRange();
  range.setStart(field.firstChild, 1); range.collapse(true);
  const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  document.execCommand('insertParagraph');
  document.execCommand('delete');
  const text = window.__ink.edit.getText();
  return text.includes('![inl](' + %s + ')') && !text.includes('复制');
})()
""" % (json.dumps(editable % 1), json.dumps(inline)))

    # 删除选中的独立图片：只去掉那一块
    js(window, "document.querySelectorAll('#editorLive img').forEach(i => i.dataset.stale = '1')")
    js(window, "window.__ink.edit.setText(%s)" % json.dumps(doc))
    assert wait_js(window, "document.querySelectorAll('#editorLive img[alt=solo]:not([data-stale])').length === 1", timeout=60)
    select_image(window, "solo")
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true, cancelable: true}))")
    js(window, "window.__expected = %s" % json.dumps(doc.replace(blocks[2] + "\n\n", "")))
    assert wait_js(window, "window.__ink.edit.getText() === window.__expected", timeout=30), "删除选中的独立图片后全文不对: " + js(window, "JSON.stringify([document.activeElement.tagName, document.activeElement.isContentEditable, !!window.__ink.image.selected(), document.getElementById('editorStatus').textContent, window.__ink.edit.getText().length])")

    # 段落里的行内图片：单击选中后 Delete，只去掉那一处
    js(window, "document.querySelectorAll('#editorLive img').forEach(i => i.dataset.stale = '1')")
    js(window, "window.__ink.edit.setText(%s)" % json.dumps(doc))
    assert wait_js(window, "document.querySelectorAll('#editorLive img[alt=inl]:not([data-stale])').length === 1", timeout=60)
    select_image(window, "inl")
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true, cancelable: true}))")
    js(window, "window.__expected = %s" % json.dumps(doc.replace("![inl](%s)" % inline, "")))
    assert wait_js(window, "window.__ink.edit.getText() === window.__expected", timeout=30), "删除选中的行内图片后全文不对"

    # 粘贴：同时带文字和图片时按文字处理
    assert js(window, r"""
(() => {
  const dt = new DataTransfer();
  dt.setData('text/plain', '文字内容');
  dt.items.add(new File([new Uint8Array(8)], 'x.png', {type: 'image/png'}));
  const event = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
  const target = document.querySelector('#editorLive [contenteditable="true"]');
  target.focus();
  target.dispatchEvent(event);
  return !!target.isConnected;
})()
""")
    assert not js(window, "window.__ink.edit.getText().includes('粘贴的图片')"), "文字加图片的粘贴被当成了图片"

    # 源码框折叠：进出一次全文不变，改说明后其余原样
    js(window, "document.querySelectorAll('#editorLive img').forEach(i => i.dataset.stale = '1')")
    js(window, "window.__ink.edit.setText(%s)" % json.dumps(doc))
    assert wait_js(window, "document.querySelectorAll('#editorLive img[alt=inl]:not([data-stale])').length === 1", timeout=60)
    js(window, "window.__ink.edit.activate(1)")
    assert wait_js(window, "window.__ink.edit.state().active === 1")
    shown = js(window, "document.querySelector('.lp-block.is-active textarea.lp-source').value")
    assert len(shown) < 500 and "内嵌数据 #" in shown, shown[:200]
    js(window, "window.__ink.edit.deactivate()")
    assert wait_js(window, "window.__ink.edit.state().active === -1")
    assert js(window, "window.__ink.edit.getText()") == doc, "进源码再退出后全文变了"
    js(window, "window.__ink.edit.activate(1)")
    assert wait_js(window, "window.__ink.edit.state().active === 1")
    js(window, """(() => { const ta = document.querySelector('.lp-block.is-active textarea.lp-source');
        ta.value = ta.value.replace('前文', '改动'); ta.dispatchEvent(new Event('input', {bubbles: true})); })()""")
    js(window, "window.__ink.edit.deactivate()")
    js(window, "window.__expected = %s" % json.dumps(doc.replace("前文", "改动", 1)))
    assert wait_js(window, "window.__ink.edit.getText() === window.__expected", timeout=30)
    res["embedded_images"] = {"inline_chars": len(inline), "textarea_chars": len(shown)}


def check_edit_fidelity(window, res):
    doc = fidelity_doc()
    expected_tables = len(FIDELITY_CASES)
    for label, text in (("lf", doc), ("crlf", doc.replace("\n", "\r\n"))):
        js(window, "document.querySelectorAll('#editorLive table').forEach(t => t.dataset.stale = '1')")
        js(window, "window.__ink.edit.setText(%s)" % json.dumps(text))
        assert wait_js(window, "document.querySelectorAll('#editorLive table:not([data-stale])').length === %d"
                       % expected_tables), label
        report = json_js(window, FIDELITY_JS.replace("__DOC__", json.dumps(text)))
        res["fidelity_" + label] = report
        assert not report["problems"], report["problems"]
        found = {t["name"]: (t["editable"], t["cells"]) for t in report["tables"]}
        for name, (_, editable, cells) in FIDELITY_CASES.items():
            assert found.get(name) == (editable, cells), (name, found.get(name))
        assert all(t["leaked"] == 0 for t in report["tables"]), report["tables"]
        # 宽松列表在编辑态是同一个列表块，列表项都能直接编辑
        for name, source in FIDELITY_LISTS.items():
            items = [l for l in report["lists"] if l["name"] == name]
            assert len(items) == 1, (name, report["lists"])
            assert items[0]["editable"] == items[0]["paragraphs"] > 0, items[0]


def wait_js(window, expression, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if window.evaluate_js(expression):
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def js(window, expression):
    return window.evaluate_js(expression)


def json_js(window, expression):
    return json.loads(window.evaluate_js("JSON.stringify(" + expression + ")"))


def wait_until(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def wait_file_contains(path, needle, timeout=10):
    end = time.monotonic() + timeout
    text = ""
    while time.monotonic() < end:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if needle in text:
            return True, text
        time.sleep(0.15)
    return False, text


# —— 纯逻辑样例：分块（splitBlocks/joinBlocks）与图片边界识别（findImageAt/
#    findProtectedRanges），驱动真实浏览器里的 window.__ink.edit.* ——
BLOCK_SPLIT_SAMPLES = [
    ("", [""]),
    ("hello", ["hello"]),
    ("<div>\n\n<div>nested</div>\n\n</div>\n\np", ["<div>\n\n<div>nested</div>\n\n</div>", "p"]),
    ("- first\n\n    more", ["- first\n\n    more"]),
    ("\\[\na\n\nb\n\\]", ["\\[\na\n\nb\n\\]"]),
    ("# Title\n\npara one\n\npara two", ["# Title", "para one", "para two"]),
    (
        "before\n\n```python\nprint(1)\n\nprint(2)\n```\n\nafter",
        ["before", "```python\nprint(1)\n\nprint(2)\n```", "after"],
    ),
    ("p\n\n$$\nE=mc^2\n$$\n\nq", ["p", "$$\nE=mc^2\n$$", "q"]),
    ("$$x^2$$\n\nnext", ["$$x^2$$", "next"]),
    (
        "```mermaid\nflowchart LR\n  A-->B\n```\n\ntext",
        ["```mermaid\nflowchart LR\n  A-->B\n```", "text"],
    ),
    ("- a\n- b\n\npara", ["- a\n- b", "para"]),
    ("```\na\n\nb\n\nc\n```", ["```\na\n\nb\n\nc\n```"]),
    ("before\n```\nx\n```", ["before", "```\nx\n```"]),
    ("   ~~~\na\n   ~~~", ["   ~~~\na\n   ~~~"]),
    ("```\nnope", ["```\nnope"]),
    ("# A\r\n\r\nB", ["# A", "B"]),
    ("$$\nE=mc^2", ["$$\nE=mc^2"]),
]

IMAGE_BOUNDS_SAMPLES = [
    # (text, pos, expected dest or None)
    ("hello\n\n![cat](images/a.png)\n\nend\n", None, "images/a.png"),
    ("```\n![nope](x.png)\n```\n\n![yes](y.png)\n", "![nope]", None),
    ("```\n![nope](x.png)\n```\n\n![yes](y.png)\n", "![yes]", "y.png"),
    ("use `![a](b.png)` then ![c](d.png)", "![a]", None),
    ("use `![a](b.png)` then ![c](d.png)", "![c]", "d.png"),
]


def check_block_logic(window, res):
    checks = []
    for src, expected in BLOCK_SPLIT_SAMPLES:
        got = json_js(window, "window.__ink.edit.splitBlocks(%s).map(function(b){return b.text;})" % json.dumps(src))
        checks.append({"name": "split:" + repr(src)[:40], "ok": got == expected, "got": got, "want": expected})
    doc = "# A\n\nHello **world**\n\n```js\n1\n\n2\n```\n\n$$\n1+1\n$$\n"
    blocks = json_js(window, "window.__ink.edit.splitBlocks(%s).map(function(b){return b.text;})" % json.dumps(doc))
    checks.append({"name": "doc-block-count", "ok": len(blocks) == 4, "got": len(blocks)})
    rejoined = js(window, "window.__ink.edit.joinBlocks(%s.map(function(t){return {text:t};}))" % json.dumps(blocks))
    reblocks = json_js(window, "window.__ink.edit.splitBlocks(%s).map(function(b){return b.text;})" % json.dumps(rejoined))
    checks.append({"name": "roundtrip-stable", "ok": reblocks == blocks})

    for text, needle, expected_dest in IMAGE_BOUNDS_SAMPLES:
        pos = text.index(needle) if needle else text.index("![")
        hit = js(window, "JSON.stringify(window.__ink.edit.findImageAt(%s, %d))" % (json.dumps(text), pos))
        hit = json.loads(hit)
        if expected_dest is None:
            ok = hit is None
        else:
            ok = bool(hit) and hit.get("dest") == expected_dest
        checks.append({"name": "findImageAt:" + repr(text)[:30], "ok": ok, "got": hit})

    # data URI 目标（含 base64 中的 +/=，dest 里没有裸括号）：不是简单相等，
    # 改成前缀 + markdown 收尾校验。
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    data_uri_text = "x\n\n![shot](data:image/png;base64,%s)\n\ny\n" % b64
    hit = json_js(window, "window.__ink.edit.findImageAt(%s, %d)" % (
        json.dumps(data_uri_text), data_uri_text.index("data:")))
    ok = bool(hit) and hit.get("dest", "").startswith("data:image/png;base64,") and hit.get("markdown", "").endswith(")")
    checks.append({"name": "findImageAt:data-uri", "ok": ok, "got": hit})

    # title 引号：dest 里带着 `"..."`，同样不做整串相等，只要求子串命中。
    title_quote_text = '![t](foo.png "my title")'
    hit = json_js(window, "window.__ink.edit.findImageAt(%s, 0)" % json.dumps(title_quote_text))
    ok = bool(hit) and 'foo.png "my title"' in hit.get("dest", "")
    checks.append({"name": "findImageAt:title-quote", "ok": ok, "got": hit})

    # 删除后源码干净：图片语法独占一行时，连同该行一起删除不留空行。
    # 找边界仍走真实 JS，只有删除产生的切片校验留在 Python 侧。
    clean_delete_text = "a\n\n![x](images/x.png)\n\nb\n"
    hit = json_js(window, "window.__ink.edit.findImageAt(%s, %d)" % (
        json.dumps(clean_delete_text), clean_delete_text.index("![")))
    ok = False
    if hit:
        line_start = clean_delete_text.rfind("\n", 0, hit["start"]) + 1
        line_end = clean_delete_text.find("\n", hit["end"])
        line = clean_delete_text[line_start:line_end]
        out = clean_delete_text[:line_start] + clean_delete_text[line_end + 1:]
        ok = (line.strip() == hit["markdown"] and "![x]" not in out
              and "a\n" in out and "b\n" in out)
    checks.append({"name": "findImageAt:clean-delete", "ok": ok, "got": hit})

    protected = js(window, "JSON.stringify(window.__ink.edit.findProtectedRanges(%s))" % json.dumps("`a` and ```\nb\n```"))
    protected = json.loads(protected)
    checks.append({"name": "findProtectedRanges-count", "ok": len(protected) == 2, "got": protected})

    res["block_logic_checks"] = checks
    return all(c["ok"] for c in checks)


def check_structured_edits(window, doc_path):
    from pathlib import Path
    path = Path(doc_path)
    expected = path.read_bytes()
    for selector, old, new in (
        (".codehilite pre[contenteditable]", "print(1)", "print(2)"),
        ("b [contenteditable]", "HTML text", "HTML 修改"),
        ("h2[contenteditable]", "Setext title", "新标题"),
        (".lp-list-label[contenteditable]", "Parent", "父项"),
        ("a", "visible", "引用"),
    ):
        script = """
        (() => {
          const el = [...document.querySelectorAll('#editorLive ' + %s)]
            .find(node => node.textContent === %s && node.isContentEditable);
          if (!el) return false;
          el.textContent = %s;
          el.dispatchEvent(new Event('input', {bubbles:true}));
          return true;
        })()
        """ % (json.dumps(selector), json.dumps(old), json.dumps(new))
        assert js(window, script), selector
        expected = expected.replace(old.encode(), new.encode(), 1)
    assert js(window, r"""
(() => {
  const atom = document.querySelector('#editorLive .math-inline');
  atom.click();
  const input = document.querySelector('.lp-inline-formula input');
  if (!input) return false;
  input.value = 'c+d';
  input.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
})()
""")
    expected = expected.replace(b"$a+b$", b"$c+d$")
    for label, old, new in (
        ("编辑公式", "E = mc^2", "E = mc^3"),
        ("编辑流程图", "A --> B", "A --> C"),
        ("编辑图片说明", "pixel", "像素"),
    ):
        js(window, "Array.from(document.querySelectorAll('#editorLive summary')).find(s => s.textContent === %s).click()" % json.dumps(label))
        field = '#editorLive textarea[aria-label="%s"]' % label
        assert wait_js(window, "!!document.querySelector(%s)" % json.dumps(field))
        js(window, """(() => {const el = document.querySelector(%s);
          el.value = el.value.replace(%s, %s);
          el.dispatchEvent(new Event('input', {bubbles:true}));})()""" % (
              json.dumps(field), json.dumps(old), json.dumps(new)))
        expected = expected.replace(old.encode(), new.encode(), 1)
    assert wait_js(window, "!!document.querySelector('#editorLive .math-block[data-latex=\\\"E = mc^3\\\"]') && !!document.querySelector('#editorLive img[alt=像素]')")
    js(window, "window.__ink.edit.save()")
    assert wait_js(window, "!window.__ink.edit.state().dirty")
    actual = path.read_bytes()
    assert actual == expected, (actual, expected)


def job(window, tmp_dir, doc_path):
    res = {"stage": "start"}
    try:
        assert window.events.loaded.wait(90), "WebView2 未完成页面加载"
        assert wait_js(window, "!!(window.__ink && window.__ink.edit)", timeout=40), js(window, "JSON.stringify(window.__errors)")
        res["block_logic_ok"] = check_block_logic(window, res)

        # ---- 进入编辑 ----
        res["stage"] = "enter"
        js(window, "window.__ink.edit.enter()")
        assert wait_js(window, "window.__ink.edit.state().mode === true")
        st = json_js(window, "window.__ink.edit.state()")
        res["enter_state"] = st
        assert st["blocks"] >= 5, st

        assert wait_js(window, "!!document.querySelector('#editorLive strong')")
        with open(doc_path, "rb") as source_file:
            original = source_file.read()
        assert js(window, "window.__ink.edit.getText()").encode() == original
        res["stage"] = "visual-edit"
        assert js(window, r"""
(() => {
  const strong = document.querySelector('#editorLive strong');
  const host = strong.closest('[contenteditable=true]');
  host.focus();
  strong.click();
  if (document.querySelector('.lp-source')) return false;
  const range = document.createRange();
  range.selectNodeContents(strong);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  if (!document.execCommand('insertText', false, '直接编辑')) return false;
  window.__savedField = host;
  window.__savedOffset = selection.anchorOffset;
  return true;
})()
""")
        # 有未保存修改时宿主窗口要开启关闭确认（Alt+F4 等绕过页面的关闭靠它拦截），保存后关掉。
        res["confirm_close_on_dirty"] = wait_until(lambda: window.confirm_close is True)
        assert res["confirm_close_on_dirty"], window.confirm_close
        js(window, "window.__ink.edit.save()")
        assert wait_js(window, "!window.__ink.edit.state().dirty")
        res["confirm_close_off_after_save"] = wait_until(lambda: window.confirm_close is False)
        assert res["confirm_close_off_after_save"], window.confirm_close
        assert js(window, "document.activeElement === window.__savedField && window.getSelection().anchorOffset === window.__savedOffset")
        with open(doc_path, "rb") as source_file:
            assert source_file.read() == original.replace(b"Live Preview**", "直接编辑**".encode())

        res["stage"] = "structured-roundtrip"
        check_structured_edits(window, doc_path)

        assert js(window, r"""
(() => {
  const root = document.querySelector('#editorLive');
  const item = root.querySelector('li em');
  const cell = root.querySelector('tbody td:last-child');
  for (const target of [item, cell]) {
    if (!target?.isContentEditable) return false;
    target.firstChild.appendData('改');
    target.dispatchEvent(new Event('input', {bubbles:true}));
  }
  const text = window.__ink.edit.getText();
  return text.includes('_强调改_') && text.includes('| 内容 | -12改 |');
})()
""")

        # ---- 激活一个块并就地修改 ----
        res["stage"] = "activate-and-edit"
        para_idx = 1  # 「一段正文…」所在块
        js(window, "document.querySelectorAll('.lp-edit-source')[1].click()")
        assert wait_js(window, "window.__ink.edit.state().active === %d" % para_idx)
        ok_set_value = js(window, r"""
(function(){
  var ta = document.querySelector('.lp-block.is-active textarea.lp-source');
  if (!ta) return false;
  ta.value = '一段正文用于测试 Live Preview——已修改。';
  ta.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
})()
""")
        assert ok_set_value
        js(window, "window.__ink.edit.deactivate()")
        assert wait_js(window, "window.__ink.edit.state().active === -1")
        text_after_edit = js(window, "window.__ink.edit.getText()")
        res["text_after_edit_contains"] = "已修改" in text_after_edit
        assert res["text_after_edit_contains"], text_after_edit

        # ---- 插入代码块 / 公式（工具栏按钮）----
        res["stage"] = "insert-code-and-math"
        js(window, "document.getElementById('editInsertCodeBtn').click()")
        assert wait_js(window, "window.__ink.edit.getText().replace(/\\r/g, '').indexOf('```\\ncode\\n```') >= 0", timeout=10)
        js(window, "window.__ink.edit.deactivate()")
        text_after_code = js(window, "window.__ink.edit.getText()")
        res["insert_code_ok"] = "```\ncode\n```" in text_after_code.replace("\r", "")

        js(window, "document.getElementById('editInsertMathBtn').click()")
        assert wait_js(window, "window.__ink.edit.getText().replace(/\\r/g, '').indexOf('E = mc^2') >= 0", timeout=10)
        js(window, "window.__ink.edit.deactivate()")
        text_after_math = js(window, "window.__ink.edit.getText()")
        res["insert_math_ok"] = "E = mc^2" in text_after_math.replace("\r", "")
        assert res["insert_code_ok"] and res["insert_math_ok"], (text_after_code, text_after_math)

        # ---- 删图：激活含图片的块，光标落在图片语法内，点删图 ----
        res["stage"] = "delete-image"
        blocks_now = json_js(window, "window.__ink.edit.splitBlocks(window.__ink.edit.getText()).map(function(b){return b.text;})")
        image_idx = next(i for i, t in enumerate(blocks_now) if "![像素]" in t)
        js(window, "window.__ink.edit.activate(%d)" % image_idx)
        assert wait_js(window, "window.__ink.edit.state().active === %d" % image_idx)
        js(window, r"""
(function(){
  var ta = document.querySelector('.lp-block.is-active textarea.lp-source');
  var pos = ta.value.indexOf('![像素]');
  ta.selectionStart = ta.selectionEnd = pos + 2;
})()
""")
        js(window, "document.getElementById('editDeleteImageBtn').click()")
        js(window, "window.__ink.edit.deactivate()")
        text_after_delete = js(window, "window.__ink.edit.getText()")
        res["delete_image_ok"] = "![像素]" not in text_after_delete
        assert res["delete_image_ok"], text_after_delete

        # ---- 保存 ----
        res["stage"] = "save"
        # save() 返回 Promise；用轮询读磁盘代替直接等待 Promise 结果。
        js(window, "window.__ink.edit.save()")
        assert wait_js(window, "window.__ink.edit.state().dirty === false", timeout=10)
        found, saved_disk_text = wait_file_contains(doc_path, "已修改", timeout=10)
        res["save_matches_disk"] = found and "![像素]" not in saved_disk_text
        assert res["save_matches_disk"], saved_disk_text[:200]

        # ---- 冲突路径：外部改写文件后再保存 ----
        res["stage"] = "conflict-reject"
        time.sleep(0.05)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("# externally changed\n")
        js(window, "window.__ink.edit.activate(0)")
        wait_js(window, "window.__ink.edit.state().active === 0")
        js(window, r"""
(function(){
  var ta = document.querySelector('.lp-block.is-active textarea.lp-source');
  ta.value = ta.value + ' 冲突前追加';
  ta.dispatchEvent(new Event('input', {bubbles:true}));
})()
""")
        js(window, "window.__ink.edit.deactivate()")
        js(window, "window.confirm = function(){ return false; }")
        js(window, "window.__ink.edit.save()")
        assert wait_js(window, "(document.getElementById('editorStatus').textContent||'').indexOf('已取消') >= 0", timeout=10)
        with open(doc_path, "r", encoding="utf-8") as f:
            after_reject = f.read()
        res["conflict_reject_ok"] = after_reject.startswith("# externally changed")
        assert res["conflict_reject_ok"], after_reject[:200]

        res["stage"] = "conflict-force"
        js(window, "window.confirm = function(){ return true; }")
        js(window, "window.__ink.edit.save()")
        found, after_force = wait_file_contains(doc_path, "冲突前追加", timeout=10)
        res["conflict_force_ok"] = found
        assert res["conflict_force_ok"], after_force[:200]

        # ---- 表格与宽松列表：回写保真 ----
        res["stage"] = "edit-fidelity"
        check_edit_fidelity(window, res)
        res["edit_fidelity_ok"] = True

        # ---- 内嵌图片：大 data URI ----
        res["stage"] = "embedded-images"
        check_embedded_images(window, res)
        res["embedded_images_ok"] = True

        # ---- 退出编辑：正文重新渲染 ----
        res["stage"] = "exit"
        js(window, "window.__ink.edit.exit()")
        assert wait_js(window, "window.__ink.edit.state().mode === false")
        res["exit_body_class_ok"] = not js(window, "document.body.classList.contains('edit-mode')")
        res["exit_content_visible_ok"] = js(window, "getComputedStyle(document.getElementById('content')).display !== 'none'")
        assert res["exit_body_class_ok"] and res["exit_content_visible_ok"]

        res["errors"] = json_js(window, "(window.__errors || [])")
        res["no_js_errors"] = res["errors"] == []

        res["all_pass"] = bool(
            res["block_logic_ok"]
            and res["text_after_edit_contains"]
            and res["insert_code_ok"] and res["insert_math_ok"]
            and res["delete_image_ok"]
            and res["save_matches_disk"]
            and res["confirm_close_on_dirty"] and res["confirm_close_off_after_save"]
            and res["conflict_reject_ok"] and res["conflict_force_ok"]
            and res["edit_fidelity_ok"] and res["embedded_images_ok"]
            and res["exit_body_class_ok"] and res["exit_content_visible_ok"]
            and res["no_js_errors"]
        )
        res["stage"] = "done"
    except Exception as exc:
        res["all_pass"] = False
        res["error"] = repr(exc)
        res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(json.dumps({"stage": res.get("stage"), "all_pass": res.get("all_pass")}, ensure_ascii=False))
        window.confirm_close = False  # 断言失败时仍处于有未保存修改状态，destroy 不能被确认框卡住
        try:
            window.destroy()
        except Exception:
            pass


def main():
    tmp_dir = tempfile.mkdtemp(prefix="inkwell_editlive_")
    doc_path = os.path.join(tmp_dir, "edit_live.md")
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(DOC)
    images_dir = os.path.join(tmp_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    with open(os.path.join(images_dir, "pixel.png"), "wb") as f:
        f.write(PIXEL_PNG)

    api = Api()
    payload = api._render_payload(doc_path)
    api.activate_path(doc_path)
    S.set_page(build_page(payload["content"], payload["toc"], payload["title"], doc_path,
                          preferences=api.preferences))
    httpd, url = S.start_server()

    window = webview.create_window(
        title="edit-live-verify", url=url, js_api=api,
        width=1180, height=860, frameless=True, easy_drag=False,
        text_select=True, background_color="#FFFFFF", zoomable=False,
    )
    api._window = window
    try:
        webview.start(job, (window, tmp_dir, doc_path), gui="edgechromium", private_mode=True)
    finally:
        httpd.shutdown()
    with open(RESULT, encoding="utf-8") as report:
        if not json.load(report)["all_pass"]:
            raise SystemExit(1)
    print("EDIT LIVE VERIFY PASS")


if __name__ == "__main__":
    main()
