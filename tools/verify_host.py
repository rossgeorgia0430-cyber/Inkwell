#!/usr/bin/env python3
"""验证宿主层文件边界、活动文档切换和跨启动设置持久化。"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inkwell.app import Api, _MAX_DOCUMENT_BYTES, _initial_file


def main():
    old_localappdata = os.environ.get("LOCALAPPDATA")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["LOCALAPPDATA"] = str(root / "local")
            first = root / "first.md"
            second = root / "second.md"
            unsupported = root / "payload.bin"
            oversized = root / "oversized.md"
            first.write_text("# First", encoding="utf-8")
            second.write_text("# Second", encoding="utf-8")
            unsupported.write_bytes(b"not markdown")
            with oversized.open("wb") as file:
                file.seek(_MAX_DOCUMENT_BYTES)
                file.write(b"x")

            api = Api()
            first_payload = api._render_payload(first)
            assert first_payload["ok"] and api.current_file is None
            assert api.activate_path(first_payload["path"])["ok"]
            active = api.current_file

            # 仅渲染候选文档不能抢先切换 watcher/相对链接基准。
            second_payload = api._render_payload(second)
            assert second_payload["ok"] and api.current_file == active
            assert api.activate_path(second_payload["path"])["ok"]
            assert api.current_file == str(second.resolve())

            assert not api._render_payload(unsupported)["ok"]
            assert not api._render_payload(oversized)["ok"]
            assert _initial_file(["inkwell", str(unsupported)]) is None
            assert _initial_file(["inkwell", str(first)]) == str(first.resolve())

            assert api.set_preference("theme", "dark")["ok"]
            assert api.set_preference("font", "17.5")["ok"]
            restored = Api().preferences
            assert restored == {"theme": "dark", "font": 17.5}
            assert not api.set_preference("theme", "hostile")["ok"]
            assert not api.set_preference("unknown", "x")["ok"]
    finally:
        if old_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_localappdata

    print("HOST VERIFY PASS")


if __name__ == "__main__":
    main()
