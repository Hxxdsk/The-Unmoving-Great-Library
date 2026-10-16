import contextlib
import threading

import pytest
from playwright.sync_api import Error as BrowserError

from test_listing import Client, make_product, session, short_fixture_waits
from suite.listing.browser import BrowserConfig, is_connection_failure
from suite.listing.models import Journal, ListingError, SaveUncertain, scan_products
from suite.listing.runner import BatchRunner
from suite.listing.site import DouyinPage


def run(root, products, browser_session, logs):
    return BatchRunner(
        root,
        products,
        BrowserConfig(),
        Client(),
        threading.Event(),
        log=logs.append,
        session_factory=lambda *_: contextlib.nullcontext(browser_session),
    ).run()


def test_disconnect_after_upload_replaces_same_field_once(
    tmp_path, session, monkeypatch
):
    product = make_product(tmp_path)
    original = DouyinPage.replace_images
    attempts, recovered, logs = [], [], []

    def upload(self, names, paths):
        original(self, names, paths)
        if names[0] == "主图3:4":
            attempts.append(self.page.url)
            if len(attempts) == 1:
                raise BrowserError(
                    "Locator.set_input_files: Connection closed while reading from the driver"
                )

    monkeypatch.setattr(DouyinPage, "replace_images", upload)
    with session as browser_session:
        browser_session.reconnect_current_page = lambda: recovered.append(
            browser_session.page.url
        )
        result = run(tmp_path, [product], browser_session, logs)
        assert result == [(product.sku, "已保存")], logs
        assert len(recovered) == 1 and attempts == recovered * 2, logs
        assert browser_session.page.locator("#portrait img").count() == 5
        assert (
            len(browser_session.page.evaluate('audit.filter(e => e.event === "save")'))
            == 1
        )
    assert not any("跳过图片字段" in line for line in logs)


@pytest.mark.parametrize("recoverable", [False, True])
def test_dead_driver_stops_batch_without_marking_next_product(
    tmp_path, session, monkeypatch, recoverable
):
    first = make_product(tmp_path, "A")
    make_product(tmp_path, "B")
    second = next(p for p in scan_products(tmp_path) if p.sku == "B")
    second.title = "用户输入的商品标题"
    logs, reconnects = [], []

    original = DouyinPage.replace_images

    def upload(self, names, paths):
        if names[0] == "主图3:4":
            raise BrowserError("Connection closed while reading from the driver")
        return original(self, names, paths)

    def reconnect():
        reconnects.append(True)
        if not recoverable:
            raise ListingError("原编辑页已关闭")

    monkeypatch.setattr(DouyinPage, "replace_images", upload)
    with session as browser_session:
        browser_session.reconnect_current_page = reconnect
        result = run(tmp_path, [first, second], browser_session, logs)
        assert result == [(first.sku, "失败")]
        assert reconnects == [True]
    assert Journal(tmp_path).get(second) == {}
    assert not any("跳过图片字段" in line for line in logs)


def test_disconnect_while_saving_stays_uncertain_and_stops_batch(
    tmp_path, session, monkeypatch
):
    first = make_product(tmp_path, "A")
    make_product(tmp_path, "B")
    second = next(p for p in scan_products(tmp_path) if p.sku == "B")
    second.title = "用户输入的商品标题"
    reconnects = []

    def save(self):
        try:
            raise BrowserError("Connection closed while reading from the driver")
        except BrowserError:
            raise SaveUncertain("保存结果待核对") from None

    monkeypatch.setattr(DouyinPage, "save_draft", save)
    with session as browser_session:
        browser_session.reconnect_current_page = lambda: reconnects.append(True)
        assert run(tmp_path, [first, second], browser_session, []) == [
            (first.sku, "待核对")
        ]
    assert not reconnects
    assert Journal(tmp_path).get(first)["status"] == "uncertain"
    assert Journal(tmp_path).get(second) == {}


def test_only_transport_failures_are_classified_as_disconnected():
    assert not is_connection_failure(
        BrowserError("Locator.click: Timeout 2500ms exceeded")
    )
    try:
        raise BrowserError("Connection closed while reading from the driver")
    except BrowserError as exc:
        wrapped = ListingError("重连失败")
        wrapped.__cause__ = exc
    assert is_connection_failure(wrapped)
