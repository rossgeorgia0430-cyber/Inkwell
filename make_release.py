#!/usr/bin/env python3
"""
组装可分发的安装包：把 dist\\Inkwell（onedir 载荷）+ 安装脚本打包成
release\\Inkwell-Setup\\，并压缩为 release\\Inkwell-Setup.zip。

分发包结构：
  Inkwell-Setup\\
    Inkwell\\               <- 程序本体（Inkwell.exe + _internal）
    install.ps1            <- 安装脚本（完整注册 + WebView2 补装）
    uninstall.ps1          <- 卸载脚本
    cleanup_legacy.ps1     <- 旧版清理（被 install.ps1 调用）
    Install-Inkwell.bat    <- 双击安装（无需管理员）
    Uninstall-Inkwell.bat  <- 双击卸载
    README.txt

用法：python make_release.py
"""
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "Inkwell")
SCRIPTS = os.path.join(ROOT, "scripts")
RELEASE = os.path.join(ROOT, "release")
OUT_DIR = os.path.join(RELEASE, "Inkwell-Setup")
ZIP_PATH = os.path.join(RELEASE, "Inkwell-Setup.zip")

SCRIPT_FILES = [
    "install.ps1", "uninstall.ps1", "cleanup_legacy.ps1",
    "Install-Inkwell.bat", "Uninstall-Inkwell.bat",
]

README = """Inkwell - 本地 Markdown 阅读器  安装说明
============================================

【安装】
  双击  Install-Inkwell.bat
  - 自动检测并补装 WebView2 运行时（需联网；多数 Win10/11 已自带）
  - 清理旧版 MarkdownReader/MDReader 残留
  - 安装到  %LOCALAPPDATA%\\Programs\\Inkwell （无需管理员）
  - 完整注册 .md/.markdown 关联（让“始终使用此应用打开”可用）
  - 创建开始菜单 + 桌面快捷方式
  - 安装末尾会打开“默认应用”设置，便于把 .md 指给 Inkwell

【把 Inkwell 设为 .md 默认程序（永久）】
  Windows 安全机制要求你手动确认一次（之后永久生效）：
    方式一：双击任意 .md -> 选择 Inkwell -> 勾选/点击“始终”
    方式二：设置 > 应用 > 默认应用 > 找到 Inkwell -> 把 .md / .markdown 指给它
  托管（域/Entra/MDM）机器可“右键以管理员身份运行”安装，自动写组策略免点击。

【卸载】
  双击  Uninstall-Inkwell.bat

【依赖】
  - Microsoft Edge WebView2 运行时（安装器会自动补装）
  - 不需要单独安装 Python；程序已自包含。
"""


def main():
    if not os.path.isfile(os.path.join(DIST, "Inkwell.exe")):
        raise SystemExit(f"[X] 未找到载荷 {DIST}\\Inkwell.exe，请先运行 python build.py")

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) 拷贝程序载荷
    print(f"[*] 拷贝载荷 {DIST} -> {OUT_DIR}\\Inkwell")
    shutil.copytree(DIST, os.path.join(OUT_DIR, "Inkwell"))

    # 2) 拷贝脚本
    for name in SCRIPT_FILES:
        src = os.path.join(SCRIPTS, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUT_DIR, name))
            print(f"[*] 脚本 {name}")
        else:
            print(f"[!] 缺少脚本 {name}（跳过）")

    # 3) README（UTF-8 BOM，便于记事本中文显示）
    with open(os.path.join(OUT_DIR, "README.txt"), "w", encoding="utf-8-sig") as f:
        f.write(README)

    # 4) 压缩
    print(f"[*] 压缩 -> {ZIP_PATH}")
    if os.path.isfile(ZIP_PATH):
        os.remove(ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(OUT_DIR):
            for fn in files:
                full = os.path.join(base, fn)
                arc = os.path.relpath(full, RELEASE)
                zf.write(full, arc)

    size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
    print(f"[DONE] 分发包就绪：")
    print(f"       目录：{OUT_DIR}")
    print(f"       压缩：{ZIP_PATH}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
