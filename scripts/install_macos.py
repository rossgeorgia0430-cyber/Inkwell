#!/usr/bin/env python3
"""在本机 macOS 上安装 Inkwell：venv + 依赖 + Inkwell.app，可选创建 Launchpad 快捷方式。"""

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
APP = ROOT / "Inkwell.app"
HOME_APPS = Path.home() / "Applications"
LAUNCH_SERVICES = (
    "/System/Library/Frameworks/CoreServices.framework/"
    "Frameworks/LaunchServices.framework/Support/lsregister"
)


def run(cmd, **kwargs):
    print("[install]", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kwargs)


def find_python():
    """按 环境变量 PYTHON -> PATH 里的 python3.12 -> PATH 里的 python3 -> 当前解释器 顺序查找。"""
    env = os.environ.get("PYTHON")
    if env:
        return env
    for name in ("python3.12", "python3"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


def create_venv(py):
    if VENV.is_dir() and (VENV / "bin" / "python").is_file():
        print("[install] 复用已有虚拟环境", VENV)
    else:
        run([py, "-m", "venv", str(VENV)])
    pip = str(VENV / "bin" / "pip")
    run([pip, "install", "--upgrade", "pip"])
    run([pip, "install", "-r", str(ROOT / "requirements.txt")])


def gen_icons():
    run([str(VENV / "bin" / "python"), str(ROOT / "gen_icon.py")], cwd=str(ROOT))


def _read_version():
    text = (ROOT / "inkwell" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("未能从 inkwell/__init__.py 读取版本号")
    return match.group(1)


def write_app_bundle():
    if APP.exists():
        shutil.rmtree(APP)
    macos = APP / "Contents" / "MacOS"
    resources = APP / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    # Info.plist 模板里的版本号是占位符，安装时换成 inkwell/__init__.py 里的真实版本，
    # 避免两处版本号各写一份、迟早对不上。
    plist_src = ROOT / "scripts" / "macos" / "Info.plist"
    plist_text = plist_src.read_text(encoding="utf-8").replace("__VERSION__", _read_version())
    (APP / "Contents" / "Info.plist").write_text(plist_text, encoding="utf-8")

    icns = ROOT / "inkwell" / "assets" / "icon.icns"
    png = ROOT / "inkwell" / "assets" / "icon.png"
    if icns.is_file():
        shutil.copy2(icns, resources / "AppIcon.icns")
    elif png.is_file():
        shutil.copy2(png, resources / "AppIcon.png")

    launcher = macos / "Inkwell"
    stub = ROOT / "scripts" / "macos" / "launcher.c"
    clang = shutil.which("clang")
    if clang and stub.is_file():
        run([clang, "-Os", "-o", str(launcher), str(stub)])
    else:
        # 没有 C 编译器时退化为 shell 启动脚本，效果等价。
        launcher.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "ROOT=%s\n"
            "cd \"$ROOT\"\n"
            "export PYTHONPATH=\"$ROOT${PYTHONPATH:+:$PYTHONPATH}\"\n"
            "export PATH=\"$ROOT/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$PATH\"\n"
            "exec \"$ROOT/.venv/bin/python\" -m inkwell \"$@\"\n" % (repr(str(ROOT)),),
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print("[install] 已写入", APP)


def link_applications():
    HOME_APPS.mkdir(parents=True, exist_ok=True)
    dest = HOME_APPS / "Inkwell.app"
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(APP)
    print("[install] 快捷方式", dest, "->", APP)


def register_launch_services():
    if os.path.isfile(LAUNCH_SERVICES):
        run([LAUNCH_SERVICES, "-f", str(APP)])
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    shim = local_bin / "inkwell"
    shim.write_text(
        "#!/bin/bash\n"
        "exec \"%s/.venv/bin/python\" -m inkwell \"$@\"\n" % ROOT,
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print("[install] 命令行入口", shim)


def smoke():
    run([
        str(VENV / "bin" / "python"), "-c",
        "import webview, markdown, pygments, PIL; from inkwell.app import Api; "
        "print('ok', getattr(webview, '__version__', 'imported'))",
    ], cwd=str(ROOT))


def main():
    if sys.platform != "darwin":
        raise SystemExit("本安装脚本仅用于 macOS")
    py = find_python()
    print("[install] Python:", py)
    create_venv(py)
    gen_icons()
    write_app_bundle()
    link_applications()
    register_launch_services()
    smoke()
    print()
    print("[DONE] Inkwell 已安装到", ROOT)
    print("  打开应用：open", APP)
    print("  或运行：  ", VENV / "bin" / "python", "-m", "inkwell", ROOT / "tests" / "sample.md")
    print("  命令行：  ~/.local/bin/inkwell")
    print("  把 .md 设为默认打开方式：在 Finder 里右键文件 → 显示简介 → 打开方式 → Inkwell → 全部更改")


if __name__ == "__main__":
    main()
