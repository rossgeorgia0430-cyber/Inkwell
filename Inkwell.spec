# -*- mode: python ; coding: utf-8 -*-
"""Inkwell PyInstaller 打包配置（onedir，pywebview/WebView2 + pythonnet + 离线资源）。"""
import os
import sys
from PyInstaller.utils.hooks import collect_all

ROOT = SPECPATH  # noqa: F821  (PyInstaller 注入)

datas = [
    (os.path.join("inkwell", "assets"), os.path.join("inkwell", "assets")),
]
binaries = []
hiddenimports = [
    "webview", "webview.platforms.edgechromium", "webview.platforms.winforms",
    "clr", "clr_loader", "clr_loader.netfx", "pythonnet",
    "bottle", "proxy_tools", "typing_extensions",
    "markdown", "markdown.extensions.fenced_code", "markdown.extensions.codehilite",
    "markdown.extensions.tables", "markdown.extensions.toc",
    "markdown.extensions.sane_lists", "markdown.extensions.md_in_html",
    "markdown.extensions.attr_list",
    "pygments", "pygments.lexers", "pygments.formatters",
    "pygments.formatters.html", "pygments.styles",
]

# 收集 pywebview（含 lib/ 下的 WebView2 DLL 与注入 js）、pygments、markdown 的全部资源
for pkg in ("webview", "pygments", "markdown"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Conda 环境修复：Anaconda 的部分 .pyd（pyexpat/_ctypes/_bz2/_lzma）依赖
# Library/bin 下的 DLL，PyInstaller 不会自动收集，缺失会导致冻结程序启动即崩。
_conda_libbin = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
for _dll in ("libexpat.dll", "ffi.dll", "libbz2.dll", "liblzma.dll"):
    _p = os.path.join(_conda_libbin, _dll)
    if os.path.isfile(_p):
        binaries.append((_p, "."))

ICON = os.path.join("inkwell", "assets", "icon.ico")

a = Analysis(
    ["run_inkwell.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide6", "matplotlib", "numpy",
              "scipy", "pandas", "IPython", "notebook", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Inkwell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.exists(ICON) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Inkwell",
)
