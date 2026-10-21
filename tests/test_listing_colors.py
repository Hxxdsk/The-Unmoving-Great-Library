import threading

import pytest
from PIL import Image

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.models import scan_products, ListingError
from suite.listing.site import DouyinPage
from suite.listing.vision import Analysis, analyze


def test_missing_colors_are_skipped_and_ai_uses_only_color_folder(tmp_path):
    product = make_product(tmp_path)
    result = analyze(Client(), product, ["灰色", "黑色"], threading.Event())
    assert all(p.parent.name == "颜色" for p in result.colors.values())
    for image in product.color_photos:
        image.unlink()
    product = scan_products(tmp_path)[0]
    assert "颜色 文件夹缺少图片" not in product.issues
    result = analyze(Client(), product, ["灰色", "黑色"], threading.Event())
    assert result.colors == {}
    assert result.skipped_colors == ["灰色", "黑色"]


def test_details_replaced_before_generation_and_main_retained(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        page = DouyinPage(s, threading.Event())
        page.create(product)
        audit = s.page.evaluate("window.audit")
        generation = next(e for e in audit if e["event"] == "generation")
        assert generation["details"] == ["1.png", "2.png", "尺码.png"]
        details = [e for e in audit if e.get("field") == "wizard-detail"]
        assert [name for event in details for name in event["files"]] == [
            "1.png",
            "2.png",
            "尺码.png",
        ]
        assert all(audit.index(event) < audit.index(generation) for event in details)
        assert len(s.page.locator("#main img").all()) == 5
        assert not page.details_prepared
        assert generation["title"] == product.title
        assert not any(e["event"] == "title" for e in audit)


def test_initial_empty_size_list_is_left_unchanged(session):
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
        _, records = page.specs("尺码大小", allow_empty=True)
        assert records == []


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


def test_actual_seven_colors_readonly_inputs_and_header_selector(session, tmp_path):
    colors = ["黑色", "月光蓝", "浅绿", "卡其", "粉紫", "深灰", "天池蓝"]
    sizes = ["S", "M", "L", "XL", "2XL", "3XL"]
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>.line{display:flex;align-items:center;gap:6px;width:300px;height:42px}.line input:first-child{width:120px}.line input[placeholder]{width:88px}.pic{width:28px;height:28px}.pic svg,.pic img{width:28px;height:28px}</style>
        <section id="colors"><header><label>颜色分类</label><select><option>颜色分类</option></select></header><div id="color-items"></div></section>
        <section id="sizes"><header><label>尺码大小</label></header><div id="size-items"></div></section><label>尺码表</label>"""
        )
        s.page.evaluate(
            """({colors,sizes})=>{
          for(const [root,names] of [['color-items',colors],['size-items',sizes]]){
            document.getElementById(root).innerHTML=names.map((n,i)=>`<div class="line"><input readonly value="${n}"><input placeholder="备注">${root==='color-items'?`<div class="pic"><svg data-icon="picture"><rect width="25" height="25"/></svg><input type="file" style="display:none"></div>`:''}<button aria-label="删除">删除</button></div>`).join('');
          }
          for(const input of document.querySelectorAll('input[type=file]'))input.onchange=e=>{const img=document.createElement('img');img.src=URL.createObjectURL(e.target.files[0]);img.dataset.name=e.target.files[0].name;input.parentElement.querySelector('svg').remove();input.parentElement.append(img)};
        }""",
            {"colors": colors, "sizes": sizes},
        )
        page = DouyinPage(s, threading.Event())
        _, rows = page.specs("颜色分类")
        assert [r["name"] for r in rows] == colors
        _, size_rows = page.specs("尺码大小")
        assert [r["name"] for r in size_rows] == sizes
        paths = {}
        for i, color in enumerate(colors):
            paths[color] = tmp_path / f"{i}.png"
            Image.new("RGB", (60, 60), "black").save(paths[color])
        page.set_color_photos(Analysis(paths, [], []))
        _, rows = page.specs("颜色分类")
        assert all(r["photos"] == 1 for r in rows)
        for i, row in enumerate(rows):
            assert (
                s.page.locator(row["photo"]).locator("img").get_attribute("data-name")
                == f"{i}.png"
            )
        assert all(s.page.locator(row["note"]).input_value() == "" for row in size_rows)


def test_ai_reads_unknown_spec_widget_and_keeps_live_control_binding(session):
    class Reader:
        def generate(self, parts, schema):
            assert any("inlineData" in p for p in parts)
            return {"rows": [{"row_id": 0, "name": "黑色"}]}

    with session as s:
        s.check_login()
        s.page.set_content(
            """<section><label>颜色分类</label><div style="display:flex;gap:5px;align-items:center"><div style="width:120px;height:32px"><canvas width="120" height="32"></canvas></div><input placeholder="备注" style="width:88px;height:26px"><div style="width:28px;height:28px"><svg width="25" height="25"><rect width="20" height="20"/></svg></div></div></section><label>尺码大小</label>"""
        )
        s.page.evaluate(
            "()=>{const c=document.querySelector('canvas').getContext('2d');c.font='18px sans-serif';c.fillText('黑色',10,22)}"
        )
        page = DouyinPage(s, threading.Event(), client=Reader())
        _, rows = page.specs("颜色分类")
        assert [r["name"] for r in rows] == ["黑色"]
        assert s.page.locator(rows[0]["note"]).get_attribute("placeholder") == "备注"
        assert s.page.locator(rows[0]["photo"]).count() == 1
