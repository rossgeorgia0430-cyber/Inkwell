#!/usr/bin/env python3
"""验证编辑模式宿主 API：读写、预览、冲突检测、监视暂停、插图路径安全。"""

import base64
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inkwell.api import Api
from inkwell.documents import MAX_DOCUMENT_BYTES


class _FakeWindow:
    def __init__(self, pick_path=None, save_path=None):
        self.pick_path = pick_path
        self.save_path = save_path

    def create_file_dialog(self, dialog_type, **kwargs):
        # pywebview: OPEN_DIALOG=10, SAVE_DIALOG=30；也可为 FileDialog 枚举
        name = str(dialog_type).lower()
        value = int(dialog_type) if str(dialog_type).isdigit() or isinstance(dialog_type, int) else -1
        try:
            value = int(dialog_type)
        except Exception:
            value = -1
        if "save" in name or value == 30:
            return self.save_path
        return [self.pick_path] if self.pick_path else None


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        doc = root / "note.md"
        doc.write_text("# Hello\n\npara\n", encoding="utf-8")

        api = Api()
        assert api.activate_path(str(doc))["ok"]
        assert api.current_file == str(doc.resolve())

        # get_source
        src = api.get_source()
        assert src["ok"], src
        assert "# Hello" in src["text"]
        assert src["mtime_ns"] is not None
        # mtime 必须以字符串过桥，避免 JS Number 丢 ns 精度（Python json 本身保 int）
        assert isinstance(src["mtime_ns"], str), type(src["mtime_ns"])
        m0 = src["mtime_ns"]
        import json
        roundtrip = json.loads(json.dumps({"m": m0}))["m"]
        assert roundtrip == m0 and isinstance(roundtrip, str)
        # 模拟 JS IEEE754：超过 2^53-1 的整数以 Number 传递会丢精度
        n = int(m0)
        if n > (2 ** 53 - 1):
            js_like = float(n)  # IEEE754 double
            assert int(js_like) != n

        # preview without writing
        prev = api.preview_markdown("# Preview\n\n```python\nprint(1)\n```\n", str(doc))
        assert prev["ok"], prev
        assert "print" in prev["content"]
        assert doc.read_text(encoding="utf-8").startswith("# Hello")

        # save + re-render
        new_text = "# Saved\n\n$$E=mc^2$$\n\n```mermaid\nflowchart LR\n  A-->B\n```\n"
        saved = api.save_document(new_text, str(doc), m0)
        assert saved["ok"], saved
        assert saved.get("saved")
        assert "math-block" in saved["content"] or "data-latex" in saved["content"]
        assert "mermaid" in saved["content"]
        assert doc.read_text(encoding="utf-8") == new_text
        m1 = saved["mtime_ns"]
        assert isinstance(m1, str)
        assert m1 != m0

        # conflict detection
        doc.write_text("# external\n", encoding="utf-8")
        conflict = api.save_document("# mine\n", str(doc), m1)
        assert conflict.get("conflict"), conflict
        assert isinstance(conflict.get("mtime_ns"), str)
        # force save
        forced = api.save_document("# mine\n", str(doc), None)
        assert forced["ok"], forced
        assert doc.read_text(encoding="utf-8") == "# mine\n"

        # 不能保存到非当前活动文档
        other = root / "other.md"
        other.write_text("# other\n", encoding="utf-8")
        denied = api.save_document("# hack\n", str(other), None)
        assert not denied["ok"], denied

        # watch pause flag
        assert api.set_watch_paused(True)["paused"] is True
        assert api._watch_paused is True
        assert api.set_watch_paused(False)["paused"] is False

        # pick_image file mode: copy into images/
        png = root / "shot.png"
        # 1x1 PNG
        png.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        ))
        api._window = _FakeWindow(pick_path=str(png))
        api.activate_path(str(doc))
        picked = api.pick_image("file")
        assert picked["ok"], picked
        assert picked["mode"] == "file"
        assert picked["markdown"].startswith("![")
        assert "images/" in picked["relative"]
        assert (doc.parent / picked["relative"]).is_file()
        # 源文件中绝不应出现 /__img__/
        assert "/__img__/" not in picked["markdown"]

        # embed mode
        emb = api.pick_image("embed")
        assert emb["ok"], emb
        assert "data:image/png;base64," in emb["markdown"]
        assert "/__img__/" not in emb["markdown"]

        # oversized save rejected
        big = "x" * (MAX_DOCUMENT_BYTES + 10)
        bad = api.save_document(big, str(doc), None)
        assert not bad["ok"]

        # welcome/no path
        api2 = Api()
        assert not api2.get_source()["ok"]
        assert not api2.pick_image()["ok"]

    print("EDIT MODE VERIFY PASS")


if __name__ == "__main__":
    main()
