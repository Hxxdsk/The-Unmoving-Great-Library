import threading
from unittest.mock import patch

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.models import scan_products
from suite.listing.site import DouyinPage
from suite.listing.runner import BatchRunner
from suite.listing.browser import BrowserConfig


def test_size_source_is_color_filename_and_separate_folder_is_unneeded(tmp_path):
    product = make_product(tmp_path)
    assert not (product.path / "尺码").exists()
    assert product.images["尺码"] == (product.path / "颜色" / "尺码.png",)
    assert not product.issues
    assert product.images["尺码"][0] not in product.color_photos


def test_full_draft_never_reopens_size_chart_upload(tmp_path, session):
    product = make_product(tmp_path)
    with patch.object(
        DouyinPage,
        "try_parse_size_chart",
        side_effect=AssertionError("不应再次上传尺码图"),
    ):
        result = BatchRunner(
            tmp_path,
            [product],
            BrowserConfig(),
            Client(),
            threading.Event(),
            session_factory=lambda *_: session,
            upload_colors=True,
        ).run()
    assert result == [(product.sku, "已保存")]
    generation = next(e for e in session.audit if e["event"] == "generation")
    assert "尺码.png" in generation["details"]
    assert not any(e["event"] == "site-size-ai" for e in session.audit)
