import copy
from unittest.mock import patch

from PIL import Image

from color_tool.data import Style
from color_tool.gemini import GeminiClient
from color_tool.pairing import normalize_batch
from suite.cache import ResultCache


def item(filename, label, **kwargs):
    return dict(
        filename=filename,
        color_label=label,
        main_color=label,
        confidence=kwargs.get("confidence", 0.98),
        needs_review=kwargs.get("needs_review", False),
        reason="对照总览服装主体与印刷颜色文字",
    )


def reply(rows):
    return {
        "colors": [
            {"label": "紫色", "description": "紫色裤子"},
            {"label": "黑色", "description": "黑色裤子"},
        ],
        "images": rows,
        "alternatives": [],
    }


def product(tmp_path):
    for name in ["颜色.png", "紫图.jpg", "黑图.jpg"]:
        Image.new("RGB", (80, 80), "purple" if name.startswith("紫") else "black").save(
            tmp_path / name
        )
    return Style(
        "JYF-2780",
        colors={"紫色": ["sku-purple"], "黑色": ["sku-black"]},
        chart=tmp_path / "颜色.png",
        images=[tmp_path / "紫图.jpg", tmp_path / "黑图.jpg"],
    )


def test_filename_path_color_suffix_percentage_and_boolean_are_normalized():
    data = reply(
        [item(r"D:\商品\紫图.JPG", " 紫 ", confidence="98%", needs_review="false")]
    )
    before = copy.deepcopy(data)
    labels, matches, _, _ = normalize_batch(data, ["紫图.jpg"], ["紫色"])
    assert labels == ["紫色", "黑色"]
    assert matches["紫图.jpg"]["color_label"] == "紫色"
    assert matches["紫图.jpg"]["confidence"] == 0.98
    assert matches["紫图.jpg"]["needs_review"] is False
    assert data == before


def test_front_back_duplicate_is_one_photo_and_unknown_extra_does_not_break_matches():
    data = reply(
        [
            item("紫图.jpg", "紫色"),
            item("紫图.jpg", "紫", confidence=0.95),
            item("颜色总览.jpg", "黑色"),
        ]
    )
    _, matches, _, notes = normalize_batch(data, ["紫图.jpg"], ["紫色"])
    assert list(matches) == ["紫图.jpg"]
    assert matches["紫图.jpg"]["confidence"] == 0.95
    assert any("未知文件" in note for note in notes)


def test_conflicting_duplicate_is_re_read_without_resending_good_photo(tmp_path):
    style = product(tmp_path)
    client = GeminiClient("test-key", "models/gemini-test")
    data = reply(
        [item("紫图.jpg", "紫色"), item("黑图.jpg", "黑色"), item("黑图.jpg", "紫色")]
    )
    logs = []
    with patch.object(
        client, "generate", side_effect=[data, reply([item("黑图.jpg", "黑色")])]
    ) as request:
        client.analyze_style(
            style, logs.append, cache=ResultCache("test", tmp_path / "cache")
        )
    assert style.selections == {"紫色": "紫图.jpg", "黑色": "黑图.jpg"}
    assert request.call_count == 2
    parts, schema = request.call_args_list[1].args
    assert schema["properties"]["images"]["items"]["properties"]["filename"][
        "enum"
    ] == ["黑图.jpg"]
    assert len([p for p in parts if "inlineData" in p]) == 2
    assert any("仅复读" in line for line in logs)


def test_invalid_one_photo_does_not_erase_other_pair_or_get_cached(tmp_path):
    style = product(tmp_path)
    client = GeminiClient("test-key", "models/gemini-test")
    cache = ResultCache("test", tmp_path / "cache")
    invalid = reply([item("黑图.jpg", "黑色", confidence="not-a-number")])
    with patch.object(
        client,
        "generate",
        side_effect=[reply([item("紫图.jpg", "紫色")]), invalid, invalid],
    ) as request:
        client.analyze_style(style, cache=cache)
    assert style.selections == {"紫色": "紫图.jpg"}
    assert style.matches["黑图.jpg"].needs_review
    assert style.matches["黑图.jpg"].confidence == 0
    assert request.call_count == 3
    with patch.object(
        client,
        "generate",
        return_value=reply([item("紫图.jpg", "紫色"), item("黑图.jpg", "黑色")]),
    ) as retry:
        client.analyze_style(style, cache=cache)
    assert retry.call_count == 1
    assert style.selections == {"紫色": "紫图.jpg", "黑色": "黑图.jpg"}


def test_different_named_blues_are_not_normalized_into_each_other():
    data = {
        "colors": [{"label": "月光蓝"}],
        "images": [item("1.jpg", "天池蓝")],
        "alternatives": [],
    }
    _, matches, _, notes = normalize_batch(data, ["1.jpg"], ["月光蓝"])
    assert not matches and notes
