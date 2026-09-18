#!/usr/bin/env python3
"""真实 WebView2 回归：Obsidian 风格 Live Preview 编辑模式的安全网。

覆盖：进入/退出编辑、块数与激活、就地修改并经 getText() 校验、插入代码块/
公式、删图、保存落盘、外部改写文件后的保存冲突路径（拒绝覆盖 / 强制覆盖）。
另外把 verify_live_blocks.py / verify_edit_image_bounds.py 里的纯逻辑样例
原样搬来，改为调用真实 JS 的 window.__ink.edit.splitBlocks / joinBlocks /
findImageAt / findProtectedRanges，取代那两个脚本里重抄一遍 JS 逻辑的方式。
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

DOC = """# Edit Live Test

一段正文用于测试 Live Preview。

![pixel](images/pixel.png)

```python
print(1)
```

$$
E = mc^2
$$
"""


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


# —— 纯逻辑样例：原样从 verify_live_blocks.py / verify_edit_image_bounds.py 搬来，
#    但改为驱动真实浏览器里的 window.__ink.edit.* ——
BLOCK_SPLIT_SAMPLES = [
    ("", [""]),
    ("hello", ["hello"]),
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

    protected = js(window, "JSON.stringify(window.__ink.edit.findProtectedRanges(%s))" % json.dumps("`a` and ```\nb\n```"))
    protected = json.loads(protected)
    checks.append({"name": "findProtectedRanges-count", "ok": len(protected) == 2, "got": protected})

    res["block_logic_checks"] = checks
    return all(c["ok"] for c in checks)


def job(window, tmp_dir, doc_path):
    res = {"stage": "start"}
    try:
        assert wait_js(window, "!!(window.__ink && window.__ink.edit)")
        res["block_logic_ok"] = check_block_logic(window, res)

        # ---- 进入编辑 ----
        res["stage"] = "enter"
        js(window, "window.__ink.edit.enter()")
        assert wait_js(window, "window.__ink.edit.state().mode === true")
        st = json_js(window, "window.__ink.edit.state()")
        res["enter_state"] = st
        assert st["blocks"] >= 5, st

        # ---- 激活一个块并就地修改 ----
        res["stage"] = "activate-and-edit"
        para_idx = 1  # 「一段正文…」所在块
        js(window, "window.__ink.edit.activate(%d)" % para_idx)
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
        assert wait_js(window, "window.__ink.edit.getText().indexOf('```\\ncode\\n```') >= 0", timeout=10)
        js(window, "window.__ink.edit.deactivate()")
        text_after_code = js(window, "window.__ink.edit.getText()")
        res["insert_code_ok"] = "```\ncode\n```" in text_after_code

        js(window, "document.getElementById('editInsertMathBtn').click()")
        assert wait_js(window, "window.__ink.edit.getText().indexOf('E = mc^2') >= 0", timeout=10)
        js(window, "window.__ink.edit.deactivate()")
        text_after_math = js(window, "window.__ink.edit.getText()")
        res["insert_math_ok"] = "E = mc^2" in text_after_math
        assert res["insert_code_ok"] and res["insert_math_ok"], (text_after_code, text_after_math)

        # ---- 删图：激活含图片的块，光标落在图片语法内，点删图 ----
        res["stage"] = "delete-image"
        blocks_now = json_js(window, "window.__ink.edit.splitBlocks(window.__ink.edit.getText()).map(function(b){return b.text;})")
        image_idx = next(i for i, t in enumerate(blocks_now) if "![pixel]" in t)
        js(window, "window.__ink.edit.activate(%d)" % image_idx)
        assert wait_js(window, "window.__ink.edit.state().active === %d" % image_idx)
        js(window, r"""
(function(){
  var ta = document.querySelector('.lp-block.is-active textarea.lp-source');
  var pos = ta.value.indexOf('![pixel]');
  ta.selectionStart = ta.selectionEnd = pos + 2;
})()
""")
        js(window, "document.getElementById('editDeleteImageBtn').click()")
        js(window, "window.__ink.edit.deactivate()")
        text_after_delete = js(window, "window.__ink.edit.getText()")
        res["delete_image_ok"] = "![pixel]" not in text_after_delete
        assert res["delete_image_ok"], text_after_delete

        # ---- 保存 ----
        res["stage"] = "save"
        # save() 返回 Promise；用轮询读磁盘代替直接等待 Promise 结果。
        js(window, "window.__ink.edit.save()")
        assert wait_js(window, "window.__ink.edit.state().dirty === false", timeout=10)
        found, saved_disk_text = wait_file_contains(doc_path, "已修改", timeout=10)
        res["save_matches_disk"] = found and "![pixel]" not in saved_disk_text
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
            and res["conflict_reject_ok"] and res["conflict_force_ok"]
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
    print("EDIT LIVE VERIFY DONE")


if __name__ == "__main__":
    main()
