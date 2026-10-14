import base64
import io
import json
import threading
import urllib.error
from unittest.mock import patch

import pytest
from PIL import Image

from color_tool.data import Style
from color_tool.gemini import GeminiClient, GeminiError, image_part, parse_response, validate_matches
from suite.cache import ResultCache


def response(data):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(data)}]}}]}


def row(filename="2.jpg", label="黑色", **kwargs):
    result = {"filename": filename, "color_label": label, "main_color": "黑色", "confidence": .98,
              "needs_review": False, "reason": "衣服主体颜色与总览图一致"}
    result.update(kwargs)
    return result


def test_response_and_thought_filter():
    payload = response({"ok": True})
    payload["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "text": "private thought"})
    assert parse_response(payload) == {"ok": True}


@pytest.mark.parametrize("data", [{}, {"candidates": [{"finishReason": "MAX_TOKENS"}]},
                                   {"candidates": [{"content": {"parts": [{"text": "invalid"}]}}]}])
def test_bad_or_truncated_response(data):
    with pytest.raises(GeminiError):
        parse_response(data)


@pytest.mark.parametrize("rows", [[], [row(filename="wrong.jpg")], [row(), row()], [row(label="幻觉颜色")],
                                   [row(confidence=float('nan'))], [row(confidence=True)], [row(needs_review="false")]])
def test_invalid_match_response(rows):
    with pytest.raises(GeminiError):
        validate_matches({"images": rows}, ["2.jpg"], ["黑色"])


def test_model_pages_filters_and_generation():
    client = GeminiClient("test-key")
    replies = [{"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                            {"name": "models/gemini-tts", "supportedGenerationMethods": ["generateContent"]}], "nextPageToken": "abc"},
               {"models": [{"name": "models/gemini-pro", "displayName": "Pro", "supportedGenerationMethods": ["generateContent"]},
                            {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]}]
    with patch.object(client, "_request", side_effect=replies) as request:
        models = client.list_models()
    assert {m['name'] for m in models} == {"models/gemini-2.5-flash", "models/gemini-pro"}
    assert "pageToken=abc" in request.call_args_list[1].args[1]


def test_https_key_in_header_only_and_errors_redacted():
    client = GeminiClient("secret-test-key")
    error = urllib.error.HTTPError("https://example", 403, "Forbidden", {},
                                  io.BytesIO(json.dumps({"error": {"message": "secret-test-key denied"}}).encode()))
    with patch("urllib.request.urlopen", side_effect=error) as open_url:
        with pytest.raises(GeminiError) as exc:
            client.list_models()
    request = open_url.call_args.args[0]
    assert request.full_url.startswith("https://generativelanguage.googleapis.com/")
    assert "secret-test-key" not in request.full_url
    assert request.get_header("X-goog-api-key") == "secret-test-key"
    assert "secret-test-key" not in str(exc.value)


def test_malformed_key_does_not_leak_through_header_errors():
    key = "secret-test-key\naccidental-line"
    with pytest.raises(GeminiError) as exc:
        GeminiClient(key)
    assert "secret-test-key" not in str(exc.value)


def test_transient_retry_and_cancel():
    client = GeminiClient("test-key")
    error = urllib.error.HTTPError("https://example", 503, "Busy", {}, io.BytesIO(b"{}"))
    final = io.BytesIO(b'{"models": []}')
    with patch("urllib.request.urlopen", side_effect=[error, final]) as request, patch.object(client.cancel, "wait", return_value=False):
        assert client._request("GET", "/models") == {"models": []}
        assert request.call_count == 2
    client.cancel.set()
    with patch("urllib.request.urlopen") as request:
        with pytest.raises(GeminiError, match="取消"):
            client._request("GET", "/models")
        request.assert_not_called()


def test_image_conversion_does_not_modify_original(tmp_path):
    path = tmp_path / "透明.png"
    Image.new("RGBA", (3000, 1800), (12, 20, 50, 0)).save(path)
    original = path.read_bytes()
    part = image_part(path)
    with Image.open(io.BytesIO(base64.b64decode(part['inlineData']['data']))) as image:
        assert max(image.size) <= 1536
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)
    assert path.read_bytes() == original


def test_multimodal_pairing_ignores_filename_order_and_commits_atomically(tmp_path):
    for name in ("颜色.png", "1.jpg", "2.jpg"):
        Image.new("RGB", (50, 50)).save(tmp_path / name)
    style = Style("A", colors={"黑色": ["sku-black"], "月光蓝": ["sku-blue"]}, folder=tmp_path,
                  chart=tmp_path / "颜色.png", images=[tmp_path / "1.jpg", tmp_path / "2.jpg"])
    client = GeminiClient("test-key", "models/gemini-test")
    reply = {"colors": [{"label": "黑色", "description": "黑"}, {"label": "月光蓝", "description": "浅蓝"}],
             "images": [row("1.jpg", "月光蓝"), row("2.jpg", "黑色")], "alternatives": []}
    cache = ResultCache("test", tmp_path / "cache")
    with patch.object(client, "generate", return_value=reply) as generate:
        client.analyze_style(style, cache=cache)
        assert generate.call_count == 1
    assert style.selections == {"黑色": "2.jpg", "月光蓝": "1.jpg"}
    parts = generate.call_args_list[0].args[0]
    assert sum("inlineData" in part for part in parts) == 3
    assert "sku-black" not in json.dumps(parts)
    with patch.object(client, "generate", return_value={"colors": reply["colors"], "images": [], "alternatives": []}):
        with pytest.raises(GeminiError):
            client.analyze_style(style, force=True, cache=cache)
    assert style.selections == {"黑色": "2.jpg", "月光蓝": "1.jpg"}
