import copy
import threading

import pytest
from PIL import Image, ImageDraw

from suite.cache import ResultCache
from suite.listing.models import scan_products, filename_key, ListingError
from suite.listing.vision import (
    prepare_color_sources,
    analyze,
    direct_reference_matches,
)
from suite.listing.site import DouyinPage
from test_listing import make_product, session, short_fixture_waits


class SourceClient:
    model = "fixture-model"

    def __init__(self, ids, refs=()):
        self.ids, self.refs, self.calls = list(ids), list(refs), 0

    def generate(self, parts, schema):
        self.calls += 1
        assert "size_chart_ids" in schema["properties"]
        assert "不根据文件名" in parts[0]["text"]
        assert "90-300斤" in parts[0]["text"]
        return dict(size_chart_ids=self.ids, reference_ids=self.refs, invalid_ids=[])


def test_unnamed_size_table_is_content_classified_without_renaming(tmp_path):
    product = make_product(tmp_path)
    chart = product.path / "颜色" / "尺码.png"
    renamed = chart.with_name("20261021_175944.png")
    chart.rename(renamed)
    product = scan_products(tmp_path)[0]
    assert not product.issues and product.images["尺码"] == ()
    index = product.images["颜色"].index(renamed)
    prepare_color_sources(SourceClient([str(index)]), product, threading.Event())
    assert product.images["尺码"] == (renamed,)
    assert renamed not in product.color_photos
    assert len(product.color_photos) == 2
    assert renamed.exists() and not chart.exists()


def test_filename_is_not_evidence_of_size_table(tmp_path):
    product = make_product(tmp_path)
    with pytest.raises(ListingError, match="未识别到可靠尺码表"):
        prepare_color_sources(SourceClient([]), product, threading.Event())


@pytest.mark.parametrize("ids,refs", [(["-1"], []), (["99"], []), (["2"], ["2"])])
def test_bad_or_overlapping_source_ids_are_rejected(tmp_path, ids, refs):
    product = make_product(tmp_path)
    with pytest.raises(ListingError, match="编号无效|类别冲突"):
        prepare_color_sources(SourceClient(ids, refs), product, threading.Event())
    assert not product.sources_prepared


def test_source_cache_reuses_content_but_invalidates_changed_image(tmp_path):
    product = make_product(tmp_path)
    cache = ResultCache("source", tmp_path / "_cache")
    client = SourceClient(["2"])
    prepare_color_sources(client, product, threading.Event(), cache=cache)
    prepare_color_sources(client, product, threading.Event(), cache=cache)
    assert client.calls == 1
    Image.new("RGB", (60, 80), "red").save(product.images["颜色"][0])
    prepare_color_sources(client, product, threading.Event(), cache=cache)
    assert client.calls == 2
    client.model = "other-model"
    prepare_color_sources(client, product, threading.Event(), cache=cache)
    assert client.calls == 3


def _shade_product(tmp_path):
    product = make_product(tmp_path)
    folder = product.path / "颜色"
    for path in folder.iterdir():
        path.unlink()
    chart = folder / "phone_123.png"
    Image.new("RGB", (800, 400), "white").save(chart)
    shades = ["#16161a", "#bababe", "#4a4a4e"]
    # Like the supplied posters: a common beige advertising area on the left,
    # with the garment on the right. Whole-image medians would see beige.
    for number, shade in enumerate(shades):
        image = Image.new("RGB", (1000, 1000), "#c5b89f")
        ImageDraw.Draw(image).rectangle((620, 100, 960, 920), fill=shade)
        image.save(folder / f"{number}.png")
    reference = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(reference)
    for number, shade in enumerate(shades):
        draw.rectangle((number * 300 + 15, 50, number * 300 + 285, 550), fill=shade)
    reference.save(folder / "guide.png")
    return scan_products(tmp_path)[0]


class ShadeClient:
    model = "fixture-model"

    def __init__(self):
        self.calls = 0
        self.rows = []

    def generate(self, parts, schema):
        self.calls += 1
        if "size_chart_ids" in schema["properties"]:
            return dict(reference_ids=["3"], size_chart_ids=["4"], invalid_ids=[])
        assert "reference_box" in schema["properties"]["colors"]["items"]["required"]
        assert "photo_box" in schema["properties"]["colors"]["items"]["required"]
        assert (
            "左侧宣传区" in parts[0]["text"] and "黑色、深灰、浅灰" in parts[0]["text"]
        )
        assert (
            len([p for p in parts if "inlineData" in p]) == 5
        )  # three originals + guide + one chart
        self.rows = [
            dict(
                color=name,
                photo_id=str(i),
                reference_id="0",
                reference_label=name,
                view="正面",
                confidence=0.99,
                needs_review=False,
                reason="对应印刷名称与服装主体",
                reference_box=dict(
                    x_min=i * 333 + 25, y_min=100, x_max=i * 333 + 300, y_max=900
                ),
                photo_box=dict(x_min=620, y_min=100, x_max=960, y_max=920),
            )
            for i, name in enumerate(["黑色", "浅灰", "深灰"])
        ]
        return dict(
            colors=self.rows,
            sizes=[
                dict(
                    name="L",
                    weight="100-120斤",
                    confidence=0.99,
                    needs_review=False,
                    reason="尺码表推荐列",
                )
            ],
        )


def test_shades_with_same_advertising_background_use_two_calls_and_cache(tmp_path):
    product = _shade_product(tmp_path)
    client, cancel = ShadeClient(), threading.Event()
    cache = ResultCache("vision", tmp_path / "_cache")
    prepare_color_sources(client, product, cancel, cache=cache)
    result = analyze(client, product, ["黑色", "浅灰", "深灰"], cancel, cache=cache)
    assert {name: path.name for name, path in result.colors.items()} == {
        "黑色": "0.png",
        "浅灰": "1.png",
        "深灰": "2.png",
    }
    assert result.sizes == [dict(name="L", weight="100-120斤")]
    assert client.calls == 2
    prepare_color_sources(client, product, cancel, cache=cache)
    second = analyze(client, product, ["黑色", "浅灰", "深灰"], cancel, cache=cache)
    assert second.colors == result.colors and client.calls == 2
    assert not any(
        "inlineData" in p.read_text() for p in cache.directory.glob("*.json")
    )
    analyze(client, product, ["深灰", "浅灰", "黑色"], cancel, cache=cache)
    assert client.calls == 3  # Page candidate changes must invalidate the old mapping.


def test_reference_matching_rejects_wrong_caption_or_duplicate_photo(tmp_path):
    product = _shade_product(tmp_path)
    client, cancel = ShadeClient(), threading.Event()
    prepare_color_sources(client, product, cancel)
    analyze(client, product, ["黑色", "浅灰", "深灰"], cancel)
    rows = copy.deepcopy(client.rows)
    rows[0]["reference_label"] = "深灰"
    result = direct_reference_matches(
        product, ["黑色", "浅灰", "深灰"], rows, cancel, lambda _: None
    )
    assert "黑色" not in result
    rows = copy.deepcopy(client.rows)
    rows[0]["photo_id"] = "1"
    result = direct_reference_matches(
        product, ["黑色", "浅灰", "深灰"], rows, cancel, lambda _: None
    )
    assert "黑色" not in result


def test_black_and_charcoal_swap_is_rejected_even_below_gross_difference_threshold(
    tmp_path,
):
    product = _shade_product(tmp_path)
    client, cancel = ShadeClient(), threading.Event()
    prepare_color_sources(client, product, cancel)
    analyze(client, product, ["黑色", "浅灰", "深灰"], cancel)
    rows = copy.deepcopy(client.rows)
    rows[0]["photo_id"], rows[2]["photo_id"] = "2", "0"
    selected = direct_reference_matches(
        product, ["黑色", "浅灰", "深灰"], rows, cancel, lambda _: None
    )
    assert set(selected) == {"浅灰"}


def test_explorer_punctuation_numeric_and_native_comparator(tmp_path, monkeypatch):
    import suite.listing.models as models

    names = ["10.jpg", "a10.jpg", "2.jpg", "!0.jpg", "1.jpg", "a2.jpg"]
    expected = ["!0.jpg", "1.jpg", "2.jpg", "10.jpg", "a2.jpg", "a10.jpg"]
    assert sorted(names, key=filename_key) == expected
    seen = []

    def compare(left, right):
        seen.append((left, right))
        return expected.index(left) - expected.index(right)

    monkeypatch.setattr(models, "_logical_compare", compare)
    assert sorted(names, key=models.filename_key) == expected and seen


def test_detail_upload_keeps_bang_zero_first_despite_slow_completion(tmp_path, session):
    make_product(tmp_path)
    folder = tmp_path / "货号A" / "详情"
    for path in folder.iterdir():
        path.unlink()
    for name in ["10.png", "!0.png", "2.png", "1.png"]:
        Image.new("RGB", (50, 50), "blue").save(folder / name)
    product = scan_products(tmp_path)[0]
    assert [p.name for p in product.images["详情"]] == [
        "!0.png",
        "1.png",
        "2.png",
        "10.png",
    ]
    with session as s:
        s.check_login()
        s.page.evaluate("""editor();window.fileSelections=[];
        document.querySelector('#detail input[type=file]').onchange=e=>{
          const files=[...e.target.files];fileSelections.push(files.map(f=>f.name));
          for(const file of files)setTimeout(()=>picture(file.name,document.querySelector('#detail .photos')),file.name.startsWith('!')?180:5);
        };void 0;""")
        page = DouyinPage(s, threading.Event())
        page.replace_images(("商品详情",), product.images["详情"])
        assert s.page.locator("#detail img").evaluate_all(
            "xs=>xs.map(x=>x.dataset.filename)"
        ) == ["!0.png", "1.png", "2.png", "10.png"]
        assert s.page.evaluate("fileSelections") == [
            ["!0.png"],
            ["1.png"],
            ["2.png"],
            ["10.png"],
        ]
