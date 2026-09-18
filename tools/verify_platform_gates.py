#!/usr/bin/env python3
"""Static gates: Mac-only paths must not run on Windows, and vice versa."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = (ROOT / "inkwell" / "app.py").read_text(encoding="utf-8")
    host = (ROOT / "inkwell" / "host.py").read_text(encoding="utf-8")
    spec = (ROOT / "Inkwell.spec").read_text(encoding="utf-8")

    assert "from .host_win import" not in app
    assert "from .host_mac import" in app
    assert "if IS_MACOS:" in app
    assert "window.run_js(js)" in app
    assert "if IS_WINDOWS:" in host
    assert "if IS_MACOS:" in host
    assert "from .host_win import Win32Backend" in host
    assert "from .host_mac import CocoaBackend" in host
    assert 'webview_gui' in host
    assert '"edgechromium"' in host
    assert '"cocoa"' in host
    assert "inkwell.host_win" in spec
    assert "webview.platforms.edgechromium" in spec
    assert "pythonnet" in spec

    host_win = (ROOT / "inkwell" / "host_win.py").read_text(encoding="utf-8")
    assert "ctypes.windll.user32" in host_win
    assert "BeginInvoke" in host_win

    print("PLATFORM GATES PASS")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
