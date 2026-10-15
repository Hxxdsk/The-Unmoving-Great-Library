import threading

from test_listing import session, short_fixture_waits
from suite.listing.site import DouyinPage


def test_wash_upload_waits_for_body_portal_and_keeps_other_fields(tmp_path, session):
    image = tmp_path / "wash.png"
    from PIL import Image

    Image.new("RGB", (80, 80), "white").save(image)
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>.upload-tile{width:90px;height:90px;border:1px solid;padding:10px}img{width:65px;height:65px}</style>
        <section id="main"><label>主图</label><img id="keep"></section>
        <div id="group"><div id="wash"><label>* 水洗标/吊牌图 <small>上传图片智能识别并预填材质信息</small></label>
        <div class="photos"></div><div class="upload-tile" onmouseenter="showLocal()">+<span>上传图片</span></div></div>
        <div><label>面料材质</label><input id="material" value="聚酯纤维"></div>
        <aside class="index-module_assistantContent__abc"><img id="helper"></aside></div>
        <div style="display:none"><button>本地上传</button></div>"""
        )
        s.page.evaluate("""()=>{
          window.showLocal=()=>{if(document.querySelector('#local'))return;setTimeout(()=>{
            const m=document.createElement('div');m.id='local';m.setAttribute('role','menu');m.style='position:fixed;left:300px;top:50px;background:white';
            m.innerHTML='<button onclick="picker.click()">本地上传</button><input id="picker" type="file" style="display:none">';document.body.append(m);
            m.querySelector('input').onchange=e=>{picture(e.target.files[0].name,document.querySelector('#wash .photos'));m.remove()};
          },200)};
        }""")
        page = DouyinPage(s, threading.Event())
        selector = page.section("水洗标/吊牌图")
        assert s.page.locator(selector).get_attribute("id") == "wash"
        assert page.photo_count(selector) == 0
        page.replace_images(("水洗标/吊牌图",), [image])
        assert s.page.locator("#wash img").count() == 1
        assert s.page.locator("#main img").count() == 1
        assert s.page.locator("#helper").count() == 1
        assert s.page.locator("#material").input_value() == "聚酯纤维"


def test_clear_hover_uses_thumbnail_wrapper_when_overlay_covers_image(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>
          #wash{width:350px;height:150px}.thumb{position:relative;width:90px;height:90px}
          img{width:80px;height:80px}.index-module_assistantContent__mask{position:absolute;inset:0;background:#eee8}
          .delete{display:none;position:absolute;top:0;right:0}.thumb:hover .delete{display:block}
        </style><section id="wash"><label>水洗标/吊牌图</label><div class="thumb"><img>
        <div class="index-module_assistantContent__mask">预览</div><button class="delete" aria-label="删除" onclick="this.parentElement.remove()">删除</button>
        </div><span>上传图片</span></section><section><label>主图</label><img id="keep"></section>"""
        )
        page = DouyinPage(s, threading.Event())
        selector = page.section("水洗标/吊牌图")
        page.clear(selector)
        assert s.page.locator("#wash img").count() == 0
        assert s.page.locator("#keep").count() == 1


def test_external_assistant_is_closed_before_hover_without_forcing_clicks(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>#wash{width:350px;height:150px}img{width:80px;height:80px}.thumb{position:relative;width:90px;height:90px}
        .delete{display:none;position:absolute;top:0;right:0}.thumb:hover .delete{display:block}</style>
        <section id="wash"><label>水洗标/吊牌图</label><div class="thumb"><img><button class="delete" aria-label="删除" onclick="this.parentElement.remove()">删除</button></div><span>上传图片</span></section>
        <div id="assistant" class="index-module_assistantContent__block" style="position:fixed;z-index:999;background:white"><button aria-label="关闭" onclick="this.parentElement.remove()">关闭</button></div>"""
        )
        s.page.evaluate(
            "()=>{const r=document.querySelector('img').getBoundingClientRect();Object.assign(document.querySelector('#assistant').style,{left:r.x+'px',top:r.y+'px',width:'150px',height:'100px'})}"
        )
        logs = []
        page = DouyinPage(s, threading.Event(), logs.append)
        page.clear(page.section("水洗标/吊牌图"))
        assert s.page.locator("#assistant").count() == 0
        assert s.page.locator("#wash img").count() == 0
        assert any("收起" in log for log in logs)
