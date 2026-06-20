#!/usr/bin/env python3
"""验证两处修复：
  1) open_dialog 的 file_types 描述能通过 pywebview parse_file_type（不再因 '/' 抛错）。
  2) 宽屏态收起目录后侧栏彻底移出布局：display:none、几何宽 0，正文左缘贴住容器左缘
     （无残留发丝边）。展开后侧栏恢复占位。
不弹真实文件选择框（需真实点击），只校验过滤器字符串与折叠几何。"""
import sys, os, time, json, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from webview.util import parse_file_type
from inkwell.app import Api
from inkwell import render as R, server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_ui_fixes_result.json")
res = {}

# ---- 1) 文件过滤器字符串（取自 app.py 实际使用值）----
FILTER = "Markdown 与文本 (*.md;*.markdown;*.mdown;*.mkd;*.txt)"
try:
    desc, exts = parse_file_type(FILTER)
    res["filter_parses"] = True
    res["filter_desc"] = desc
    res["filter_exts"] = exts
except Exception as e:
    res["filter_parses"] = False
    res["filter_error"] = repr(e)

api = Api()
content, toc = R.render_markdown(
    "# 折叠验证\n\n## 小节一\n\n## 小节二\n\n正文内容。", base_dir=HERE)
S.set_page(build_page(content, toc, "ui-fixes-verify"))
httpd, url = S.start_server()

MEASURE = r"""
(function(){
  var app = document.getElementById('app');
  var sb  = document.getElementById('sidebar');
  var mn  = document.getElementById('main');
  var ar = app.getBoundingClientRect(), sr = sb.getBoundingClientRect(), mr = mn.getBoundingClientRect();
  return JSON.stringify({
    inner_w: window.innerWidth, dpr: window.devicePixelRatio,
    drawer: app.classList.contains('drawer'),
    hidden: app.classList.contains('sidebar-hidden'),
    sb_display: getComputedStyle(sb).display,
    sb_w: Math.round(sr.width), sb_left: Math.round(sr.left),
    app_left: Math.round(ar.left),
    main_left: Math.round(mr.left),
    main_w: Math.round(mr.width)
  });
})()
"""


def job(win):
    try:
        time.sleep(1.0)
        # 强制宽屏态：需保证 CSS 内宽 >=760 才是非抽屉网格布局。高 DPI（如 150%）下
        # CSS 内宽 = 设备宽/缩放，故设备宽给足（1600）以越过 760 断点。
        win.resize(1600, 980)
        time.sleep(0.4)
        win.evaluate_js("window.dispatchEvent(new Event('resize'))")
        time.sleep(0.3)

        expanded = json.loads(win.evaluate_js(MEASURE))
        res["expanded"] = expanded

        # 点击「目录」按钮收起
        win.evaluate_js("document.getElementById('sidebarToggle').click()")
        time.sleep(0.35)
        collapsed = json.loads(win.evaluate_js(MEASURE))
        res["collapsed"] = collapsed

        # 再次点击展开
        win.evaluate_js("document.getElementById('sidebarToggle').click()")
        time.sleep(0.35)
        reexpanded = json.loads(win.evaluate_js(MEASURE))
        res["reexpanded"] = reexpanded

        res["collapse_no_sliver"] = (
            collapsed["hidden"]
            and collapsed["sb_display"] == "none"
            and collapsed["sb_w"] == 0
            # 正文左缘 == 容器左缘（容差 1px），即左侧不留任何侧栏发丝边
            and abs(collapsed["main_left"] - collapsed["app_left"]) <= 1
        )
        res["expand_restores"] = (
            not reexpanded["hidden"]
            and reexpanded["sb_display"] != "none"
            and reexpanded["sb_w"] > 100
            # 展开后正文左缘明显右移（让出侧栏宽度）
            and reexpanded["main_left"] - collapsed["main_left"] > 100
        )
        res["all_pass"] = bool(
            res.get("filter_parses") and res["collapse_no_sliver"] and res["expand_restores"])
        res["stage"] = "ok"
    except Exception as e:
        res["stage"] = "error"; res["error"] = repr(e); res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try: win.destroy()
        except Exception: pass


win = webview.create_window(title="ui-fixes-verify", url=url, js_api=api,
                            width=1600, height=980, min_size=(520, 360),
                            frameless=True, easy_drag=False, text_select=True,
                            background_color="#FFFFFF", zoomable=False)
api._window = win
webview.start(job, win, gui="edgechromium", private_mode=True)
print(json.dumps(res, ensure_ascii=False))
print("UI FIXES VERIFY DONE")
