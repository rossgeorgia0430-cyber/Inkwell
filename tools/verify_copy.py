#!/usr/bin/env python3
"""验证表格复制：整表选择 / 片段选择(空粘贴 bug) 都应得到完整 <table>+TSV；单元格内选文不应被放大成整表。"""
import sys, os, time, json, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from inkwell import render as R, server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_copy_result.json")
MD = open(os.path.join(HERE, "showcase.md"), encoding="utf-8").read()

TEST_JS = r"""
(function(){
  function run(setSel){
    var s=window.getSelection(); s.removeAllRanges();
    var r=document.createRange(); setSel(r); s.addRange(r);
    var p=window.__ink.copyPayload(s);
    s.removeAllRanges();
    return {hasTable:/<table/i.test(p.html), tabs:(p.plain.match(/\t/g)||[]).length,
            rows:(p.plain.match(/\n/g)||[]).length, plainHead:p.plain.replace(/\n/g,'\\n').slice(0,60)};
  }
  var table=document.querySelector('#content table');
  var tbody=table.querySelector('tbody');
  var td=table.querySelector('tbody td');
  return {
    whole:   run(function(r){ r.selectNode(table); }),
    fragment:run(function(r){ r.selectNodeContents(tbody); }),
    cellText:run(function(r){ r.selectNodeContents(td); })
  };
})()
"""

def job(win):
    res={}
    try:
        for _ in range(40):
            if win.evaluate_js("document.readyState")=="complete" and win.evaluate_js("!!document.querySelector('#content table')"):
                break
            time.sleep(0.2)
        time.sleep(0.4)
        out=win.evaluate_js(TEST_JS)
        res["out"]=out
        res["checks"]={
            "whole_has_table":   out["whole"]["hasTable"],
            "whole_has_tabs":    out["whole"]["tabs"]>0,
            "fragment_has_table":out["fragment"]["hasTable"],     # 关键：片段也修复成整表
            "fragment_has_tabs": out["fragment"]["tabs"]>0,
            "celltext_no_table": out["cellText"]["hasTable"]==False,  # 关键：选单元格文字不放大
        }
        res["all_pass"]=all(res["checks"].values())
        res["stage"]="ok"
    except Exception as e:
        res["stage"]="error"; res["error"]=repr(e); res["trace"]=traceback.format_exc()
    finally:
        with open(RESULT,"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2)
        try: win.destroy()
        except Exception: pass

content,toc=R.render_markdown(MD, base_dir=HERE)
S.set_page(build_page(content,toc,"copy-verify"))
httpd,url=S.start_server()
win=webview.create_window(title="copy-verify", url=url, js_api=None,
    width=1000, height=720, frameless=True, text_select=True, background_color="#FFFFFF")
webview.start(job, win, gui="edgechromium", private_mode=True)
print("COPY VERIFY DONE")
