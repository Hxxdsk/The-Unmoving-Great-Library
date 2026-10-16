import threading

import pytest
from PIL import Image

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.site import DouyinPage
from suite.listing.models import ListingError
from suite.listing.vision import analyze


def test_size_entry_unblocks_real_preview_sidebar_and_adds_all_sizes(session):
    with session as s:
        s.check_login()
        s.page.evaluate(
            """editor();
        const old=document.querySelector('[aria-label="请选择尺码"]');
        const entry=document.createElement('div');entry.role='combobox';entry.className='style_skuValueInput__fpVup style_forCreate__c19CW';entry.tabIndex=0;entry.textContent='请选择/输入尺码大小';entry.style='width:250px;height:35px;border:1px solid gray';
        entry.onclick=()=>{document.querySelector('#size-options')?.remove();const menu=document.createElement('div');menu.id='size-options';menu.style='position:fixed;top:10px;left:10px;z-index:20;background:white';
        for(const n of ['S','M','L','XL','2XL','3XL']){const option=document.createElement('button');option.role='option';option.textContent=n;option.onclick=()=>{addSize(n);menu.remove()};menu.append(option)}document.body.append(menu)};old.replaceWith(entry);
        document.body.insertAdjacentHTML('beforeend','<aside class="styles_sidePanel__KGyeH styles_sidePanelOverlay__EdKdj" style="position:fixed;inset:0;z-index:999;background:white"><div class="styles_previewBodyWrap__pcVBF" style="position:absolute;inset:30px">商品效果预览<img class="styles_galleryImage__vAG9C"></div><button aria-label="关闭消费者预览" style="position:absolute;right:0;top:0" onclick="window.closedPreview=true;this.parentElement.remove()">×</button></aside>');"""
        )
        logs = []
        page = DouyinPage(s, threading.Event(), logs.append)
        sizes = [
            {"name": n, "weight": "100-120斤"}
            for n in ["S", "M", "L", "XL", "2XL", "3XL"]
        ]
        page.set_sizes(sizes)
        assert s.page.evaluate("window.closedPreview") is True
        _, records = page.specs("尺码大小")
        assert {r["name"] for r in records} == {r["name"] for r in sizes}
        assert all(
            s.page.locator(r["note"]).input_value() == "100-120斤" for r in records
        )
        assert any("商品预览侧栏" in line for line in logs)


def test_unknown_overlay_is_not_clicked_through(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            '<button id="target" onclick="window.bad=true">目标</button><div style="position:fixed;inset:0;z-index:999">未知弹层</div>'
        )
        page = DouyinPage(s, threading.Event())
        with pytest.raises(ListingError, match="遮挡"):
            page.click_field(s.page.locator("#target"))
        assert s.page.evaluate("window.bad||false") is False


class BlueReferenceClient(Client):
    def __init__(self, duplicate=False, invalid=False):
        super().__init__()
        self.duplicate, self.invalid = duplicate, invalid

    def generate(self, parts, schema):
        properties = schema.get("properties", {})
        if "slots" in properties:
            if self.invalid:
                return {"slots": []}
            return {
                "slots": [
                    {
                        "reference_id": "0",
                        "color": "月光蓝",
                        "box": [0, 0, 500, 1000],
                        "confidence": 0.99,
                    },
                    {
                        "reference_id": "0",
                        "color": "天池蓝",
                        "box": [500, 0, 1000, 1000],
                        "confidence": 0.99,
                    },
                ]
            }
        if "matches" in properties:
            assert any("原始单色主图 photo_id=0" in p.get("text", "") for p in parts)
            assert any("图上真实标注：月光蓝" in p.get("text", "") for p in parts)
            return {
                "matches": [
                    {
                        "photo_id": "0",
                        "slot_id": "slot_0",
                        "confidence": 0.99,
                        "ambiguous": False,
                        "reason": "浅蓝色、黄色拉链，对应月光蓝格",
                    },
                    {
                        "photo_id": "0" if self.duplicate else "1",
                        "slot_id": "slot_1",
                        "confidence": 0.99,
                        "ambiguous": False,
                        "reason": "深蓝色，对应天池蓝格",
                    },
                ]
            }
        result = super().generate(parts, schema)
        # Reproduce the initial AI mistake: shallow blue is assigned to Tianchi.
        result["colors"] = [
            dict(r, color=n) for r, n in zip(result["colors"], ["天池蓝", "月光蓝"])
        ]
        return result


def blue_product(tmp_path):
    product = make_product(tmp_path)
    ref = product.path / "颜色" / "对照.png"
    Image.new("RGB", (1000, 800), "lightblue").save(ref)
    product.color_references = (ref,)
    product.color_uploads = product.images["颜色"]
    return product


def test_reference_tiles_correct_swapped_moonlight_and_tianchi_blue(tmp_path):
    product = blue_product(tmp_path)
    result = analyze(
        BlueReferenceClient(), product, ["月光蓝", "天池蓝"], threading.Event()
    )
    assert result.colors["月光蓝"] == product.color_photos[0]
    assert result.colors["天池蓝"] == product.color_photos[1]
    assert not result.skipped_colors
    assert result.sizes


@pytest.mark.parametrize("duplicate,invalid", [(True, False), (False, True)])
def test_reference_failure_or_duplicate_photo_does_not_reuse_initial_wrong_mapping(
    tmp_path, duplicate, invalid
):
    product = blue_product(tmp_path)
    result = analyze(
        BlueReferenceClient(duplicate, invalid),
        product,
        ["月光蓝", "天池蓝"],
        threading.Event(),
    )
    assert result.colors == {}
    assert result.skipped_colors == ["月光蓝", "天池蓝"]
    assert result.sizes
