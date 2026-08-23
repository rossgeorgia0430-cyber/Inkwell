#!/usr/bin/env python3
"""Remove the local macOS app bundle, Launchpad symlink, and CLI shim."""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "Inkwell.app"
HOME_APP = Path.home() / "Applications" / "Inkwell.app"
SHIM = Path.home() / ".local" / "bin" / "inkwell"


def rm(path: Path):
    if path.is_symlink() or path.is_file():
        path.unlink()
        print("[uninstall] 删除", path)
    elif path.is_dir():
        shutil.rmtree(path)
        print("[uninstall] 删除", path)


def main():
    if sys.platform != "darwin":
        raise SystemExit("本卸载脚本仅用于 macOS")
    rm(HOME_APP)
    rm(APP)
    rm(SHIM)
    print("[DONE] 已移除 Inkwell.app 与命令行入口。")
    print("       源码与虚拟环境仍在", ROOT, "（如需一并删除请手动 rm -rf）")


if __name__ == "__main__":
    main()
