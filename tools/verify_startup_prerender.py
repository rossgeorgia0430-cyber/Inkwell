#!/usr/bin/env python3
"""启动预渲染：pull_initial 不重复渲染，路径变化或有待打开文件时按新路径渲染。"""

import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inkwell.api import Api  # noqa: E402


def make_api(init_path):
    api = Api()
    api.calls = []
    release = threading.Event()

    def fake_render(path):
        release.wait(5)
        api.calls.append(path)
        return {"ok": True, "path": path}

    api._render_startup_payload = fake_render
    api._init_path = init_path
    return api, release


def main():
    # 预渲染结果被 pull_initial 直接取用，同一路径只渲染一次（预渲染尚未完成时也要等它）。
    api, release = make_api("a.md")
    api.start_prerender()
    release.set()
    assert api.pull_initial() == {"ok": True, "path": "a.md"}
    assert api.calls == ["a.md"], api.calls
    assert api._initial_delivered and api._prerendered is None

    # 没有路径时预渲染欢迎页，同样只渲染一次。
    api, release = make_api(None)
    api.start_prerender()
    release.set()
    assert api.pull_initial() == {"ok": True, "path": None}
    assert api.calls == [None], api.calls

    # 待打开路径优先于预渲染的路径：新路径渲染一次，预渲染结果作废。
    api, release = make_api(None)
    api.start_prerender()
    release.set()
    api._accept_finder_path("b.md")
    assert api.pull_initial() == {"ok": True, "path": "b.md"}
    assert api.calls.count("b.md") == 1 and api.calls.count(None) <= 1, api.calls
    assert api._init_path == "b.md" and api._pending_open_path is None

    # 未经预渲染（如重复调用）时仍按当前 _init_path 渲染。
    api, release = make_api("a.md")
    release.set()
    assert api.pull_initial() == {"ok": True, "path": "a.md"}
    assert api.pull_initial() == {"ok": True, "path": "a.md"}
    assert api.calls == ["a.md", "a.md"], api.calls

    # 渲染期间又到达新文件：本轮结果作废，循环重建到没有待打开文件为止。
    api, release = make_api("a.md")
    original = api._render_startup_payload

    def render_then_receive(path):
        payload = original(path)
        if path == "a.md":
            api._accept_finder_path("c.md")
        return payload

    api._render_startup_payload = render_then_receive
    api.start_prerender()
    release.set()
    assert api.pull_initial() == {"ok": True, "path": "c.md"}
    assert api.calls == ["a.md", "c.md"], api.calls

    # 预渲染失败时 pull_initial 拿到同样的错误 payload，而不是挂住。
    api = Api()
    api._init_path = str(ROOT / "no_such_dir" / "missing.md")
    api.start_prerender()
    payload = api.pull_initial()
    assert payload["ok"] is False and payload["title"] == "错误", payload

    print("STARTUP PRERENDER PASS")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
