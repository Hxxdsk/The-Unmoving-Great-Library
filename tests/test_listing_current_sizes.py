import threading
from PIL import Image

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.site import DouyinPage
from suite.listing.vision import (
    Analysis,
    prepare_color_sources,
    reference_color_matches,
)


def test_only_existing_size_region_notes_are_filled_and_unmatched_preserved(session):
    with session as s:
        s.check_login()
        s.page.evaluate(
            """editor();document.querySelector('#sizes .items').innerHTML='';
        for(const n of ['S','M','L','XL','2XL','3XL'])addSize(n);
        for(const e of document.querySelectorAll('#sizes input[placeholder=备注]'))e.value='原备注';
        document.querySelector('#sizes').insertAdjacentHTML('beforebegin','<aside><span>M</span><input placeholder="备注" value="侧栏不改"></aside>');
        document.body.insertAdjacentHTML('beforeend','<section id="unrelated"><label>其他选项</label><select><option>XL</option></select><input placeholder="备注" value="其他不改"></section>');"""
        )
        page = DouyinPage(s, threading.Event())
        page.control = lambda *_: (_ for _ in ()).throw(
            AssertionError("不得全页寻找尺码或新增")
        )
        page.set_sizes(
            [
                {"name": "M", "weight": "100-120斤"},
                {"name": "XL", "weight": "140-160斤"},
                {"name": "4XL", "weight": "200-220斤"},
            ]
        )
        _, records = page.specs("尺码大小")
        result = {r["name"]: s.page.locator(r["note"]).input_value() for r in records}
        assert result == {
            "S": "原备注",
            "M": "100-120斤",
            "L": "原备注",
            "XL": "140-160斤",
            "2XL": "原备注",
            "3XL": "原备注",
        }
        assert s.page.locator("aside input").input_value() == "侧栏不改"
        assert s.page.locator("#unrelated input").input_value() == "其他不改"


def test_live_three_column_color_rows_override_shifted_legacy_mapping(
    tmp_path, session
):
    names = ["黑色", "月光蓝", "浅绿", "卡其", "粉紫", "深灰", "天池蓝"]
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>.grid{display:grid;grid-template-columns:repeat(3,310px)}.line{display:flex;align-items:center;gap:5px;height:44px}input[readonly]{width:115px}input[placeholder]{width:85px}.pic,.pic img,.pic svg{width:28px;height:28px}.pic{position:relative}.pic button{position:absolute;right:0;top:0;width:12px;height:12px;font-size:8px;padding:0}</style><section id="colors"><label>颜色分类</label><div class="grid"></div></section><section><label>尺码大小</label></section><label>尺码表</label>"""
        )
        s.page.evaluate(
            """names=>{document.querySelector('.grid').innerHTML=names.map((n,i)=>`<div class="line" id="c${i}"><input readonly value="${n}"><input placeholder="备注"><div class="pic"><svg data-icon="picture"></svg><input type="file" style="display:none"></div><button aria-label="删除">删除</button></div>`).join('');for(const f of document.querySelectorAll('[type=file]'))f.onchange=()=>{const img=new Image();img.src=URL.createObjectURL(f.files[0]);img.dataset.file=f.files[0].name;f.parentElement.append(img)}}""",
            names,
        )
        page = DouyinPage(s, threading.Event(), log=print)
        _, rows = page.specs("颜色分类")
        wrong = [dict(r, name=names[(i + 1) % len(names)]) for i, r in enumerate(rows)]
        original = page.dom
        page.dom = lambda op, *args, **kwargs: (
            wrong if op == "specs" else original(op, *args, **kwargs)
        )
        _, rows = page.specs("颜色分类")
        assert [r["name"] for r in rows] == names
        path = tmp_path / "卡其.png"
        Image.new("RGB", (100, 100), "khaki").save(path)
        s.page.evaluate(
            """()=>{const tile=document.querySelector('#c3 .pic');tile.innerHTML='<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJX8AAAAASUVORK5CYII="><button aria-label="删除">删除</button>';
        tile.querySelector('button').onclick=()=>{const old=document.querySelector('#c3'),fresh=old.cloneNode(true);fresh.removeAttribute('data-gl-id');for(const e of fresh.querySelectorAll('[data-gl-id]'))e.removeAttribute('data-gl-id');const pic=fresh.querySelector('.pic');pic.innerHTML='<svg data-icon="picture"></svg><input type="file" style="display:none">';const f=pic.querySelector('input');f.onchange=()=>{const img=new Image();img.src=URL.createObjectURL(f.files[0]);img.dataset.file=f.files[0].name;pic.append(img)};old.replaceWith(fresh);window.remounted=true};}"""
        )
        page.set_color_photos(Analysis({"卡其": path}, [], []))
        assert s.page.locator("#c3 img").get_attribute("data-file") == "卡其.png"
        assert s.page.locator("#c1 img").count() == 0
        assert s.page.evaluate("window.remounted") is True


def test_content_size_chart_is_excluded_and_ai_invalids_do_not_drop_garments(
    tmp_path, session
):
    product = make_product(tmp_path)
    chart = product.path / "颜色" / "尺码.png"
    Image.new("RGB", (600, 800), "white").save(chart)
    from suite.listing.models import scan_products

    product = scan_products(tmp_path)[0]
    product.title = "用户商品标题"

    class Classifier(Client):
        def generate(self, parts, schema):
            assert "尺码表" in parts[0]["text"]
            return {
                "reference_ids": [],
                "size_chart_ids": ["2"],
                "invalid_ids": ["0", "1"],
            }

    prepare_color_sources(Classifier(), product, threading.Event())
    assert chart not in product.color_photos
    with session as s:
        page = DouyinPage(s, threading.Event())
        page.upload_color_images = True
        page.create(product)
        generation = next(
            e for e in s.page.evaluate("audit") if e["event"] == "generation"
        )
        assert chart.name in generation["details"]
        assert [p.name for p in product.color_photos] == ["1.png", "2.png"]


def test_visible_garment_color_corrects_khaki_photo_assigned_to_blue(tmp_path):
    product = make_product(tmp_path)
    Image.new("RGB", (100, 100), "khaki").save(product.images["颜色"][0])
    Image.new("RGB", (100, 100), "lightblue").save(product.images["颜色"][1])
    reference = product.path / "颜色" / "reference.png"
    image = Image.new("RGB", (400, 200), "lightblue")
    image.paste(Image.new("RGB", (200, 200), "khaki"), (200, 0))
    image.save(reference)
    product.color_references = (reference,)

    class Matcher(Client):
        def generate(self, parts, schema):
            if "slots" in schema["properties"]:
                return {
                    "slots": [
                        {"reference_id": "0", "color": n, "box": b, "confidence": 0.99}
                        for n, b in [
                            ("月光蓝", [0, 0, 500, 1000]),
                            ("卡其", [500, 0, 1000, 1000]),
                        ]
                    ]
                }
            return {
                "matches": [
                    {
                        "photo_id": str(i),
                        "slot_id": "slot_0",
                        "confidence": 0.99,
                        "ambiguous": False,
                        "reason": "首次错误都选蓝色",
                    }
                    for i in range(2)
                ]
            }

    selected, _ = reference_color_matches(
        Matcher(), product, ["月光蓝", "卡其"], threading.Event(), lambda _: None
    )
    assert selected["卡其"] == product.color_photos[0]
    assert selected["月光蓝"] == product.color_photos[1]
