import threading

import pytest
from PIL import Image

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.models import scan_products, ListingError
from suite.listing.site import DouyinPage
from suite.listing.vision import Analysis, analyze


def test_colors_are_required_and_ai_uses_only_color_folder(tmp_path):
    product = make_product(tmp_path)
    result = analyze(Client(), product, ["灰色", "黑色"], threading.Event())
    assert all(p.parent.name == "颜色" for p in result.colors.values())
    for image in product.images["颜色"]:
        image.unlink()
    product = scan_products(tmp_path)[0]
    assert "颜色 文件夹缺少图片" in product.issues
    with pytest.raises(ListingError, match="颜色文件夹缺少"):
        analyze(Client(), product, ["灰色", "黑色"], threading.Event())


def test_details_replaced_before_generation_and_main_retained(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        page = DouyinPage(s, threading.Event())
        page.create(product)
        audit = s.page.evaluate("window.audit")
        generation = next(e for e in audit if e["event"] == "generation")
        assert generation["details"] == ["1.png", "2.png"]
        details = next(e for e in audit if e.get("field") == "wizard-detail")
        assert details["files"] == ["1.png", "2.png"]
        assert audit.index(details) < audit.index(generation)
        assert len(s.page.locator("#main img").all()) == 5
        assert page.details_prepared


def test_initial_empty_size_list_can_be_filled(session):
    with session as s:
        s.check_login()
        s.page.evaluate("editor();document.querySelector('#sizes .items').innerHTML=''")
        page = DouyinPage(s, threading.Event())
        page.set_sizes(
            [
                {"name": "M", "weight": "80-100斤"},
                {"name": "3XL", "weight": "160-180斤"},
            ]
        )
        _, records = page.specs("尺码大小")
        assert {r["name"] for r in records} == {"M", "3XL"}
        assert s.page.locator(records[0]["note"]).input_value() == "80-100斤"


def test_six_real_style_color_rows_upload_through_right_picture_boxes(
    tmp_path, session
):
    colors = ["米白色", "浅紫色", "浅绿色", "银色", "黑色", "浅蓝色"]
    paths = {}
    for i, color in enumerate(colors):
        path = tmp_path / f"color-{i}.png"
        Image.new("RGB", (80, 80), (i * 35, 80, 100)).save(path)
        paths[color] = path
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>.row{display:flex;gap:6px;align-items:center;width:320px;height:42px}.semi-select{width:90px}input{width:100px}.photoBox{width:28px;height:28px;border:1px dashed}svg{width:20px;height:20px}.row img{width:28px;height:28px}</style>
        <section id="colors"><header><label>颜色分类</label><input type="checkbox" checked>添加规格图</header><div id="items"></div></section>
        <section><label>尺码大小</label><select><option>请选择尺码大小</option></select></section>
        <div style="display:none"><button>本地上传</button></div>"""
        )
        s.page.evaluate(
            """colors=>{
          document.querySelector('#items').innerHTML=colors.map((c,i)=>`<div class="row" id="c${i}"><div class="semi-select" role="combobox"><span class="semi-select-selection-text">${c}</span><span>▾</span></div><input placeholder="备注"><div class="photoBox" onmouseenter="openPicker('c${i}')"><svg data-icon="picture"><rect width="18" height="18"/></svg></div><button aria-label="删除" onclick="window.deleted=true">删除</button></div>`).join('');
          window.openPicker=id=>{if(document.querySelector('#portal'))return;setTimeout(()=>{
            const p=document.createElement('div'),r=document.querySelector('#'+id+' .photoBox').getBoundingClientRect();p.id='portal';p.setAttribute('role','menu');p.style=`position:fixed;left:${r.x}px;top:${r.y-35}px;background:white;z-index:99`;
            p.innerHTML='<button onclick="picker.click()">本地上传</button><button>图库选择</button><input type="file" id="picker" style="display:none">';document.body.append(p);
            p.querySelector('input').onchange=e=>{const f=e.target.files[0];document.querySelector('#'+id+' .photoBox').innerHTML='<img data-name="'+f.name+'" src="'+URL.createObjectURL(f)+'">';p.remove()};
          },120)};
        }""",
            colors,
        )
        page = DouyinPage(s, threading.Event())
        selector, records = page.specs("颜色分类")
        assert s.page.locator(selector).get_attribute("id") == "colors"
        assert [r["name"] for r in records] == colors
        page.set_color_photos(Analysis(paths, [], []))
        _, records = page.specs("颜色分类")
        assert all(r["photos"] == 1 for r in records)
        for i in range(6):
            assert (
                s.page.locator(f"#c{i} img").get_attribute("data-name")
                == f"color-{i}.png"
            )
        assert not s.page.evaluate("window.deleted===true")
