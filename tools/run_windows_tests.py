"""运行 Windows 回归测试并检查 WebView2 返回的结果。"""

import json
import os
import subprocess
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_REPORT_PROBES = {
    "copy", "edit_live", "image_copy", "multimonitor_maximize", "native",
    "nav", "resize", "theme", "ui_fixes", "viewer",
}


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("本测试入口需要 Windows 和 WebView2")
    failures = []
    for probe in sorted(_TOOLS.glob("verify_*.py")):
        name = probe.stem.removeprefix("verify_")
        if name == "mac_ui_thread":
            continue
        report = _TOOLS / f"_{name}_result.json"
        previous = report.stat().st_mtime_ns if report.exists() else None
        try:
            process = subprocess.run(
                [sys.executable, str(probe)], cwd=_TOOLS.parent, timeout=180,
                env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True,
                encoding="utf-8", errors="replace",
            )
            ok = process.returncode == 0
            if name in _REPORT_PROBES:
                result = json.loads(report.read_text(encoding="utf-8"))
                ok = ok and report.stat().st_mtime_ns != previous and result.get("all_pass", result.get("ok"))
                if ok and result.get("stage") == "skipped":
                    print(f"{name} 跳过：{result.get('reason')}", flush=True)
                    continue
                if not ok:
                    print(result.get("trace") or result.get("error") or result)
            if not ok:
                print(process.stdout[-2000:], process.stderr[-2000:])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(exc)
            ok = False
        print(f"{name} {'通过' if ok else '失败'}", flush=True)
        if not ok:
            failures.append(name)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
