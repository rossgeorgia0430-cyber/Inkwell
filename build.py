#!/usr/bin/env python3
"""
Inkwell 打包脚本：调用 PyInstaller 按 Inkwell.spec 生成 onedir 产物（dist/Inkwell/）。
构建后校验关键 WebView2 / pythonnet DLL 是否在产物中。
"""
import os
import sys
import shutil
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "Inkwell")


def regen_assets():
    """重新生成图标与 Pygments 主题，确保资源最新。"""
    for script in ("gen_icon.py", "gen_pygments.py"):
        p = os.path.join(ROOT, script)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"缺少资源生成脚本：{p}")
        print(f"[build] 运行 {script}")
        # 资源生成失败时立即停止，不能继续打出使用旧资源或缺失资源的安装包。
        subprocess.run([sys.executable, p], cwd=ROOT, check=True)


def clean():
    for d in ("build", "dist"):
        path = os.path.join(ROOT, d)
        if os.path.isdir(path):
            try:
                shutil.rmtree(path)
            except OSError as exc:
                print(f"[build] 清理 {path} 失败 [X]：{exc}")
                print("[build] 常见原因：Inkwell.exe 仍在运行占用了产物目录，请先关闭后重试。")
                sys.exit(1)


def build():
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            os.path.join(ROOT, "Inkwell.spec")]
    print("[build] " + " ".join(args))
    r = subprocess.run(args, cwd=ROOT)
    return r.returncode == 0


def verify():
    ok = True
    exe = os.path.join(DIST, "Inkwell.exe")
    if not os.path.isfile(exe):
        print("[verify] 缺少 Inkwell.exe [X]")
        return False
    print(f"[verify] Inkwell.exe 存在 [OK] ({os.path.getsize(exe)//1024} KB)")

    # 关键 DLL（pywebview hook 应已收集，这里校验）
    needles = {
        "WebView2Loader.dll": False,
        "Microsoft.Web.WebView2.Core.dll": False,
        "Python.Runtime.dll": False,
    }
    for root, _dirs, files in os.walk(DIST):
        for f in files:
            if f in needles:
                needles[f] = True
    for name, found in needles.items():
        print(f"[verify] {name}: {'[OK]' if found else '[X] 缺失'}")
        if not found:
            ok = False

    # 资源：以源码 inkwell/assets 目录树为准，逐个核对打包产物下同名文件存在且大小一致，
    # 这样前端资源拆分/改名后清单会自动跟上，不需要在这里同步维护一份硬编码列表。
    src_assets = os.path.join(ROOT, "inkwell", "assets")
    dist_assets = os.path.join(DIST, "_internal", "inkwell", "assets")
    for root, _dirs, files in os.walk(src_assets):
        for name in files:
            src_path = os.path.join(root, name)
            rel = os.path.relpath(src_path, src_assets)
            dist_path = os.path.join(dist_assets, rel)
            found = os.path.isfile(dist_path) and os.path.getsize(dist_path) == os.path.getsize(src_path)
            print(f"[verify] assets/{rel.replace(os.sep, '/')}: {'[OK]' if found else '[X]'}")
            if not found:
                ok = False
    return ok


def main():
    try:
        regen_assets()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[build] 资源生成失败 [X]：{exc}")
        sys.exit(1)
    clean()
    if not build():
        print("[build] PyInstaller 失败 [X]")
        sys.exit(1)
    print("\n[build] 校验产物...")
    if verify():
        print(f"\n[DONE] 打包成功：{DIST}")
        print("   下一步：powershell -ExecutionPolicy Bypass -File scripts\\install.ps1")
    else:
        print("\n[!] 打包完成但校验有缺失，请检查上面的 [X] 项。")
        sys.exit(2)


if __name__ == "__main__":
    main()
