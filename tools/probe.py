#!/usr/bin/env python3
"""原生 pywebview 窗口自动化探针：加载 sample.md，校验渲染/公式/复制净化，截屏后自动关闭。"""
import sys, os, json, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview
from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(HERE, "_probe_shot.png")
RESULT = os.path.join(HERE, "_probe_result.json")

METRICS_JS = r"""
(function(){
  function q(s){return document.querySelectorAll(s).length;}
  return {
    ready: document.readyState,
    errors: (window.__errors||[]).slice(0,20),
    katex: q('.katex'),
    katexErr: q('.katex-error'),
    codeblocks: q('.codehilite'),
    pyspans: q('.codehilite .k, .codehilite .nf, .codehilite .kn, .codehilite .s2, .codehilite .nb, .codehilite .mi'),
    tocLinks: q('#toc a'),
    headings: q('#content h1[id],#content h2[id],#content h3[id]'),
    copyFloat: q('.code-copy-float'),
    wrapper: q('.code-block-wrapper'),
    mathBlocks: q('.math-block'),
    mathInline: q('.math-inline'),
    title: (document.getElementById('docTitle')||{}).textContent,
    theme: document.documentElement.getAttribute('data-theme')
  };
})()
"""

SANITIZE_JS = r"""
(function(){
  var c=document.getElementById('content');
  if(!c||!window.__ink) return {err:'no content/ink'};
  var r=document.createRange(); r.selectNodeContents(c);
  var h=document.createElement('div'); h.appendChild(r.cloneContents());
  window.__ink.sanitize(h);
  var html=h.innerHTML;
  var colors=(html.match(/color:\s*[^;"']+/gi)||[]);
  var nonBlack=colors.filter(function(c){return !/transparent|rgb\(0,\s*0,\s*0\)|#000(000)?/i.test(c);});
  var bgs=(html.match(/background[^:]*:\s*[^;"']+/gi)||[]).filter(function(c){return !/transparent|none/i.test(c);});
  return {
    len: html.length,
    hasClass: /class=/i.test(html),
    hasKatexSpan: /class="katex/i.test(html),
    nonBlackColors: nonBlack.slice(0,8),
    bgColors: bgs.slice(0,8),
    dollarCount: (html.match(/\$/g)||[]).length,
    hasH2: /<h2[ >]/i.test(html),
    hasStrong: /<strong[ >]/i.test(html),
    hasTable: /<table[ >]/i.test(html),
    sample: html.slice(0, 220)
  };
})()
"""

DBLCLICK_JS = r"""
(function(){
  // 模拟双击高亮：直接调用内部不易，转而验证 highlightToken 的可达性——
  // 这里手动在第一个代码块包裹一个 token 验证 CSS 类生效
  var box=document.querySelector('.codehilite');
  if(!box) return {err:'no code'};
  return {ok:true};
})()
"""


def probe(window):
    res = {"stage": "start"}
    try:
        # 等待加载与 KaTeX 渲染
        for _ in range(60):
            try:
                ready = window.evaluate_js("document.readyState")
                kx = window.evaluate_js("document.querySelectorAll('.katex').length")
            except Exception:
                ready, kx = None, 0
            if ready == "complete" and kx and kx > 0:
                break
            time.sleep(0.25)

        res["metrics"] = window.evaluate_js(METRICS_JS)
        res["sanitize"] = window.evaluate_js(SANITIZE_JS)
        res["highlight"] = window.evaluate_js(r"""
(function(){
  var box=document.querySelector('.codehilite');
  if(!box||!window.__ink) return {err:'no'};
  window.__ink.clearHL();
  window.__ink.highlight(box,'values');
  var n=box.querySelectorAll('.var-highlight').length;
  var cur=box.querySelectorAll('.var-highlight.current').length;
  window.__ink.clearHL();
  var after=box.querySelectorAll('.var-highlight').length;
  return {count:n, current:cur, afterClear:after};
})()
""")
        res["search"] = window.evaluate_js(r"""
(function(){
  if(!window.__ink) return {err:'no'};
  window.__ink.runSearch('标题');
  var n=document.querySelectorAll('mark.search-hit').length;
  var cur=document.querySelectorAll('mark.search-hit.current').length;
  return {hits:n, current:cur};
})()
""")

        # 滚动到代码块，便于查看 Claude 风格代码头部
        try:
            window.evaluate_js("var w=document.querySelector('.code-block-wrapper'); if(w) w.scrollIntoView({block:'start'});")
            time.sleep(0.4)
        except Exception:
            pass

        # 截屏（全屏，窗口居中可见）
        try:
            from PIL import ImageGrab
            time.sleep(0.4)
            img = ImageGrab.grab()
            img.save(SHOT)
            res["shot"] = SHOT
        except Exception as e:
            res["shot_err"] = repr(e)

        # 暗色主题截屏
        try:
            window.evaluate_js("window.__ink && document.getElementById('themeBtn').click()")
            time.sleep(0.6)
            from PIL import ImageGrab
            img2 = ImageGrab.grab()
            shot_dark = os.path.join(HERE, "_probe_shot_dark.png")
            img2.save(shot_dark)
            res["shot_dark"] = shot_dark
        except Exception as e:
            res["shot_dark_err"] = repr(e)

        res["stage"] = "ok"
    except Exception as e:
        import traceback
        res["stage"] = "error"
        res["error"] = repr(e)
        res["trace"] = traceback.format_exc()
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try:
            window.destroy()
        except Exception:
            pass


def main():
    path = os.path.join(HERE, "..", "tests", "sample.md")
    path = os.path.abspath(path)
    md = open(path, encoding="utf-8").read()
    content, toc = R.render_markdown(md, base_dir=os.path.dirname(path))
    S.set_page(build_page(content, toc, os.path.basename(path)))
    httpd, url = S.start_server()

    win = webview.create_window(
        title="Inkwell-probe", url=url, js_api=None,
        width=1180, height=820, frameless=True, easy_drag=False,
        text_select=True, background_color="#FAF9F5",
    )
    webview.start(probe, win, gui="edgechromium", private_mode=True)
    print("PROBE DONE")


if __name__ == "__main__":
    main()
