#!/usr/bin/env python3
"""验证图片复制前端：图片本地化、PNG 栅格化、焦点支持及原生回退输入校验。"""
import base64
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview
from PIL import Image, ImageDraw, ImageGrab

from inkwell import app as A
from inkwell import render as R
from inkwell import server as S
from inkwell.page import build_page


HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_image_copy_result.json")
BUTTON_SHOT = os.path.join(HERE, "_image_copy_button.png")
VIEWER_SHOT = os.path.join(HERE, "_image_viewer.png")
# 由 Pillow 生成预览测试图；经渲染管线本地化后可覆盖 data URI 图片路径。
_png_buffer = io.BytesIO()
_fixture = Image.new("RGBA", (1400, 800), (42, 41, 38, 255))
_draw = ImageDraw.Draw(_fixture)
_draw.rounded_rectangle((90, 90, 1310, 710), radius=34, fill=(216, 183, 126, 255))
_draw.rectangle((140, 145, 1260, 655), fill=(238, 235, 225, 255))
_draw.text((180, 190), "INKWELL IMAGE PREVIEW", fill=(52, 50, 46, 255))
_fixture.save(_png_buffer, format="PNG")
PNG = base64.b64encode(_png_buffer.getvalue()).decode("ascii")
MD = f"# 图片复制验证\n\n![透明像素](data:image/png;base64,{PNG})\n"


def job(win):
    res = {"stage": "start"}
    try:
        for _ in range(50):
            ready = win.evaluate_js("document.readyState")
            loaded = win.evaluate_js("(function(){var i=document.querySelector('#content img');return !!(i&&i.complete&&i.naturalWidth);})()")
            if ready == "complete" and loaded:
                break
            time.sleep(0.15)

        res["dom"] = win.evaluate_js(r"""
(function(){
  var img=document.querySelector('#content img');
  if (!img || !window.__ink || !window.__ink.image) return {ok:false};
  var block=img.closest('.image-block');
  var button=block&&block.querySelector('.image-copy-btn');
  img.focus();
  var buttonStyle=button&&getComputedStyle(button);
  window.__image_copy_probe={pending:true};
  window.__ink.image.toPng(img).then(function(blob){
    window.__image_copy_probe={ok:true,type:blob.type,size:blob.size,
      tabindex:img.tabIndex,button:!!button,buttonInBlock:!!(block&&block.contains(button)),
      buttonPosition:buttonStyle&&buttonStyle.position,buttonTop:buttonStyle&&buttonStyle.top,
      buttonRight:buttonStyle&&buttonStyle.right,buttonOpacity:buttonStyle&&buttonStyle.opacity,
      copyAction:button&&button.getAttribute('data-copy-action')};
  }).catch(function(err){ window.__image_copy_probe={ok:false,error:String(err)}; });
  return {ok:true,currentSrc:img.currentSrc,localized:img.getAttribute('src')};
})()
""")
        for _ in range(50):
            image_result = win.evaluate_js("window.__image_copy_probe")
            if image_result and not image_result.get("pending"):
                break
            time.sleep(0.1)
        res["image"] = image_result
        time.sleep(0.25)
        res["image"]["buttonOpacity"] = win.evaluate_js(
            "getComputedStyle(document.querySelector('.image-copy-btn')).opacity"
        )
        ImageGrab.grab().save(BUTTON_SHOT)
        res["button_shot"] = BUTTON_SHOT
        win.evaluate_js("window.__ink.image.open(document.querySelector('#content img'))")
        time.sleep(0.3)
        viewer = win.evaluate_js(r"""
(function(){var v=document.querySelector('.image-viewer'),i=v&&v.querySelector('.image-viewer-image');
return {open:!!(v&&v.classList.contains('open')),source:i&&i.getAttribute('src'),
copy:!!(v&&v.querySelector('.image-viewer-copy')),close:!!(v&&v.querySelector('.image-viewer-close'))};})()
""")
        res["viewer"] = viewer
        ImageGrab.grab().save(VIEWER_SHOT)
        res["viewer_shot"] = VIEWER_SHOT
        win.evaluate_js("window.__ink.image.close()")
        source = res["dom"].get("currentSrc", "") if res.get("dom") else ""
        asset = A._image_asset_path_for_copy(source)
        dib = A._image_asset_to_dib(asset) if asset else b""
        res["native"] = {
            "asset_found": bool(asset and asset.is_file()),
            "dib_bytes": len(dib),
            "rejects_remote": A._image_asset_path_for_copy("https://example.com/__img__/test.png") is None,
            "rejects_traversal": A._image_asset_path_for_copy("/__img__/../secret.png") is None,
        }
        res["all_pass"] = bool(
            res["dom"].get("ok")
            and res["dom"].get("localized", "").startswith("/__img__/")
            and res["image"].get("ok")
            and res["image"].get("type") == "image/png"
            and res["image"].get("size", 0) > 0
            and res["image"].get("tabindex") == 0
            and res["image"].get("button")
            and res["image"].get("buttonInBlock")
            and res["image"].get("buttonPosition") == "absolute"
            and res["image"].get("buttonTop") == "8px"
            and res["image"].get("buttonRight") == "8px"
            and res["image"].get("buttonOpacity") == "1"
            and res["image"].get("copyAction") == "image"
            and res["viewer"].get("open")
            and bool(res["viewer"].get("source"))
            and res["viewer"].get("copy")
            and res["viewer"].get("close")
            and all(res["native"].values())
        )
        res["stage"] = "ok"
    except Exception as exc:
        res["stage"] = "error"
        res["error"] = repr(exc)
    finally:
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        try:
            win.destroy()
        except Exception:
            pass


content, toc = R.render_markdown(MD, base_dir=HERE)
S.set_page(build_page(content, toc, "image-copy-verify"))
httpd, url = S.start_server()
win = webview.create_window(title="image-copy-verify", url=url, js_api=None,
                            width=900, height=640, frameless=True, text_select=True,
                            on_top=True)
webview.start(job, win, gui="edgechromium", private_mode=True)
print("IMAGE COPY VERIFY DONE")
