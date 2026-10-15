import threading

import pytest
from PIL import Image, ImageDraw

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.models import scan_products, ListingError, Journal
from suite.listing.vision import analyze, normalize_weight, size_chart_parts
from suite.listing.runner import BatchRunner
from suite.listing.browser import BrowserConfig

WEIGHTS = {
    "S": "85-100斤",
    "M": "100-120斤",
    "L": "120-140斤",
    "XL": "140-160斤",
    "2XL": "160-175斤",
    "3XL": "175-190斤",
}


@pytest.mark.parametrize("photo_count", [0, 1])
def test_missing_color_images_still_save_and_size_weights_match_each_row(
    tmp_path, session, photo_count
):
    product = make_product(tmp_path)
    for path in product.images["颜色"][photo_count:]:
        path.unlink()
    product = scan_products(tmp_path)[0]

    class TableClient(Client):
        def generate(self, parts, schema):
            prompt = parts[0]["text"]
            assert "体重(斤)" in prompt and "衣长" in prompt and "模特" in prompt
            assert schema["properties"]["colors"]["items"]["properties"]["photo_id"][
                "enum"
            ] == (["0", "skip"] if photo_count else ["skip"])
            result = super().generate(parts, schema)
            result["colors"] = result["colors"][:photo_count]
            result["sizes"] = [
                {
                    "name": name,
                    "weight": weight,
                    "confidence": 0.99,
                    "needs_review": False,
                    "reason": "尺码推荐表体重(斤)列",
                }
                for name, weight in WEIGHTS.items()
            ]
            return result

    results = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        TableClient(),
        threading.Event(),
        session_factory=lambda *_: session,
    ).run()
    assert results == [(product.sku, "已保存")]
    saved = next(e for e in session.audit if e["event"] == "save")
    assert {row["name"]: row["weight"] for row in saved["sizes"]} == WEIGHTS
    uploads = [e for e in session.audit if e["event"] == "specphoto"]
    assert [e["color"] for e in uploads] == (["灰色"] if photo_count else [])
    assert Journal(tmp_path).get(product)["skipped_colors"] == (
        ["黑色"] if photo_count else ["灰色", "黑色"]
    )
    assert len(saved["price"]) == 12
    assert set(map(int, saved["stock"])) == {1000}


def test_weight_ranges_normalize_to_jin_without_confusing_measurements():
    expected = {
        "85 ～ 100斤": "85-100斤",
        "50-60 KG": "100-120斤",
        "42.5至50公斤": "85-100斤",
        "建议体重：100—120斤": "100-120斤",
        "120斤-140斤": "120-140斤",
    }
    for source, target in expected.items():
        assert normalize_weight(source) == target
    assert normalize_weight("") == ""
    for source in ("118cm", "85-100", "64kg", "100-85斤"):
        with pytest.raises(ListingError):
            normalize_weight(source)


def test_uncertain_color_and_duplicate_size_views_do_not_block_reliable_data(tmp_path):
    product = make_product(tmp_path)
    client = Client()
    result = client.generate([], {})
    result["colors"][0]["needs_review"] = True
    result["sizes"].append(dict(result["sizes"][0]))
    client.generate = lambda *_: result
    analysis = analyze(client, product, ["灰色", "黑色", "浅绿"], threading.Event())
    assert set(analysis.colors) == {"黑色"}
    assert analysis.skipped_colors == ["灰色", "浅绿"]
    assert len(analysis.sizes) == 5


def test_wide_size_chart_includes_labeled_lower_table_details(tmp_path):
    path = tmp_path / "尺码.png"
    picture = Image.new("RGB", (1132, 617), "white")
    draw = ImageDraw.Draw(picture)
    draw.text(
        (25, 420),
        "SIZE / HEIGHT / WEIGHT (jin): S 85-100 M 100-120 L 120-140",
        fill="black",
    )
    draw.text((600, 420), "SIZE / LENGTH / CHEST (cm)", fill="black")
    picture.save(path)
    parts = size_chart_parts(path)
    assert len([p for p in parts if "inlineData" in p]) == 3
    assert sum("同一尺码图下半部" in p.get("text", "") for p in parts) == 2
