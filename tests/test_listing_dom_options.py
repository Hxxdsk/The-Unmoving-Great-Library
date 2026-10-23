import json
import threading
from contextlib import nullcontext

import pytest

from test_listing import Client, make_product, session, short_fixture_waits
from suite.cache import ResultCache
from suite.listing.browser import BrowserConfig
from suite.listing.models import ListingError
from suite.listing.page_ai import PageAI
from suite.listing.runner import BatchRunner
from suite.listing.site import DouyinPage
from suite.listing.vision import analyze, prepare_size_sources


def test_default_batch_never_matches_reads_or_uploads_colors(
    tmp_path, session, monkeypatch
):
    product = make_product(tmp_path)
    # Size discovery must still work without a special filename.
    chart = product.images["尺码"][0]
    renamed = chart.with_name("任意名称.png")
    chart.rename(renamed)
    product.images["颜色"] = tuple(
        renamed if p == chart else p for p in product.images["颜色"]
    )
    product.images["尺码"] = ()
    # Existing reference classifications from an earlier run must not trigger
    # colour matching when this run has opted out.
    product.color_references = product.color_photos[:1]

    class SizesOnly(Client):
        def generate(self, parts, schema):
            keys = set(schema["properties"])
            assert keys in ({"size_chart_ids"}, {"sizes"})
            if keys == {"size_chart_ids"}:
                return {"size_chart_ids": [str(product.images["颜色"].index(renamed))]}
            # Only the selected chart is supplied to size/weight reading.
            assert sum("inlineData" in p for p in parts) == 1
            assert not any(
                "photo_id=" in p.get("text", "") or "reference_id=" in p.get("text", "")
                for p in parts
            )
            return super().generate(parts, schema)

    original = DouyinPage.specs

    def specs(self, label, **kwargs):
        assert label != "颜色分类", "关闭颜色图时不得读取颜色候选或触发颜色OCR"
        return original(self, label, **kwargs)

    monkeypatch.setattr(DouyinPage, "specs", specs)
    monkeypatch.setattr(
        DouyinPage, "ensure_color_first", lambda *_: pytest.fail("不应移动颜色规格")
    )
    monkeypatch.setattr(
        DouyinPage, "set_color_photos", lambda *_: pytest.fail("不应上传颜色图")
    )
    with session as s:
        monkeypatch.setattr(
            s.page, "screenshot", lambda **_: pytest.fail("可读DOM流程不应截图")
        )
        result = BatchRunner(
            tmp_path,
            [product],
            BrowserConfig(),
            SizesOnly(),
            threading.Event(),
            session_factory=lambda *_: nullcontext(s),
        ).run()
        assert result == [(product.sku, "已保存")]
        audit = s.page.evaluate("window.audit")
        assert not any(e["event"] == "specphoto" for e in audit)
        generation = next(e for e in audit if e["event"] == "generation")
        assert generation["details"] == [p.name for p in product.images["详情"]]


def test_dom_scan_and_native_input_do_not_scroll_or_request_ai(session, monkeypatch):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<div style="height:1800px"></div>
          <section id="first"><label>白底图</label><input type="file"></section>
          <section id="second"><label>主图3:4</label><input type="file" multiple></section>
          <section><label>货号</label><input id="sku" oninput="window.saved=this.value"></section>
          <aside style="position:fixed;inset:0;background:white;z-index:999">遮挡层</aside>"""
        )
        monkeypatch.setattr(s.page, "screenshot", lambda **_: pytest.fail("不应截图"))
        page = DouyinPage(s, threading.Event())
        ordered = list(
            page.fields_in_page_order(
                [(("主图3:4",), ["portrait.png"]), (("白底图",), ["white.png"])]
            )
        )
        assert [names for names, _ in ordered] == [("白底图",), ("主图3:4",)]
        assert s.page.evaluate("scrollY") == 0
        page.fill_sku("CURRENT")
        assert s.page.evaluate("window.saved") == "CURRENT"
        assert s.page.evaluate("scrollY") == 0


def test_ai_fallback_uses_whole_document_dom_without_screenshot(session, monkeypatch):
    class DOMClient:
        def generate(self, parts, schema):
            assert len(parts) == 1 and "inlineData" not in parts[0]
            candidates = json.loads(parts[0]["text"].split("\n候选：", 1)[1])
            item = next(c for c in candidates if c["text"] == "新的入口")
            return dict(
                action="target",
                candidate_id=item["id"],
                confidence=0.98,
                reason="DOM文字",
            )

    with session as s:
        s.check_login()
        s.page.set_content('<div style="height:2400px"></div><button>新的入口</button>')
        monkeypatch.setattr(s.page, "screenshot", lambda **_: pytest.fail("不应截图"))
        target = PageAI(s.page, DOMClient(), threading.Event()).find("查找新入口")
        assert target.inner_text() == "新的入口"
        assert s.page.evaluate("scrollY") == 0


def test_size_only_cache_and_invalid_chart_do_not_invoke_color_path(tmp_path):
    product = make_product(tmp_path)
    cache = ResultCache("size-only", tmp_path / "cache")

    class SizesOnly(Client):
        def generate(self, parts, schema):
            assert set(schema["properties"]) in ({"size_chart_ids"}, {"sizes"})
            return super().generate(parts, schema)

    client = SizesOnly()
    for _ in range(2):
        prepare_size_sources(client, product, threading.Event(), cache=cache)
        result = analyze(
            client, product, [], threading.Event(), cache=cache, upload_colors=False
        )
        assert result.sizes and result.colors == {} and result.skipped_colors == []
    assert client.calls == 2

    class Missing:
        def generate(self, *_):
            return {"size_chart_ids": []}

    with pytest.raises(ListingError, match="可靠尺码图"):
        prepare_size_sources(Missing(), product, threading.Event())
