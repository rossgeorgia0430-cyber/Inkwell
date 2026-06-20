#!/usr/bin/env python3
"""验证复制净化：表格结构/TSV、公式还原为 LaTeX，代码块去除着色 UI。"""
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
  var inlineMath=document.querySelector('#content .math-inline');
  var blockMath=document.querySelector('#content .math-block');
  var codeBlock=document.querySelector('#content .code-block-wrapper');
  var inlineOut=run(function(r){ r.selectNode(inlineMath); });
  var blockOut=run(function(r){ r.selectNode(blockMath); });
  var codeOut=run(function(r){ r.selectNode(codeBlock); });
  return {
    whole:   run(function(r){ r.selectNode(table); }),
    fragment:run(function(r){ r.selectNodeContents(tbody); }),
    cellText:run(function(r){ r.selectNodeContents(td); }),
    inlineMath:inlineOut,
    blockMath:blockOut,
    code:codeOut,
    details:{
      inlinePlain:inlineOut.plainHead,
      blockPlain:blockOut.plainHead,
      codePlain:codeOut.plainHead,
      codeHasUi:/code-copy|code-block-header|copy-action/i.test(codeOut.html),
      codeHasPygments:/class=|<span/i.test(codeOut.html),
      inlineHandlers:document.querySelectorAll('#content [onclick]').length,
      copyActions:document.querySelectorAll('#content [data-copy-action]').length,
      leakedGlobals:(typeof window.copyCode !== 'undefined' || typeof window.copyLatex !== 'undefined')
    }
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
            "inline_math_to_latex": "$E = mc^2$" in out["details"]["inlinePlain"],
            "block_math_to_latex": "$$\\int_" in out["details"]["blockPlain"],
            "code_keeps_text": "USTRUCT(BlueprintType)" in out["details"]["codePlain"],
            "code_strips_ui": out["details"]["codeHasUi"] == False,
            "code_strips_pygments": out["details"]["codeHasPygments"] == False,
            "copy_uses_delegation": (out["details"]["inlineHandlers"] == 0
                                     and out["details"]["copyActions"] >= 2
                                     and out["details"]["leakedGlobals"] == False),
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
