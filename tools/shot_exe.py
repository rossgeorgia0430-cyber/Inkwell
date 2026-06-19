#!/usr/bin/env python3
"""启动已打包的 Inkwell.exe 打开一个文件，等待数秒后全屏截图，再结束进程。
用于验证打包产物不白屏、能正常渲染。
用法: python tools/shot_exe.py <exe_path> <md_path> <out_png> [wait_seconds]
"""
import sys, os, time, subprocess

def main():
    exe = sys.argv[1]
    md = sys.argv[2]
    out = sys.argv[3]
    wait = float(sys.argv[4]) if len(sys.argv) > 4 else 8.0

    proc = subprocess.Popen([exe, md])
    try:
        time.sleep(wait)
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(out)
        print("SHOT", out, "size", img.size)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(1)
        # 兜底强杀同名进程
        try:
            subprocess.run(["taskkill", "/F", "/IM", "Inkwell.exe"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

if __name__ == "__main__":
    main()
