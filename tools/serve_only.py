#!/usr/bin/env python3
"""仅启动 Inkwell 内置服务器（不开原生窗口），用于浏览器内验证前端。
用法: python tools/serve_only.py [file.md] [--port N]
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = 8799
    for a in sys.argv[1:]:
        if a.startswith("--port"):
            port = int(a.split("=")[-1]) if "=" in a else 8799
    path = args[0] if args else os.path.join(os.path.dirname(__file__), "..", "tests", "sample.md")
    path = os.path.abspath(path)
    md = open(path, encoding="utf-8").read()
    content, toc = R.render_markdown(md, base_dir=os.path.dirname(path))
    S.set_page(build_page(content, toc, os.path.basename(path)))
    httpd, url = S.start_server(port=port)
    print("SERVING", url, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
