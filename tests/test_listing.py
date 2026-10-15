import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from suite.listing.models import scan_products, Journal, ListingError
from suite.listing.browser import BrowserConfig
from suite.listing.runner import BatchRunner
from suite.listing.site import DouyinPage
from suite.listing.vision import analyze, normalize_size

FIXTURE = Path(__file__).parent / "fixtures/listing.html"


@pytest.fixture(autouse=True)
def short_fixture_waits(monkeypatch):
    original = DouyinPage.wait

    def wait(self, condition, message, timeout=45):
        return original(self, condition, message, min(timeout, 3))

    monkeypatch.setattr(DouyinPage, "wait", wait)


def make_product(root, name="货号A"):
    for folder in ("800", "3.4", "白底", "详情", "水洗标", "尺码"):
        directory = root / name / folder
        directory.mkdir(parents=True)
        for number in (
            range(1, 8)
            if folder in ("800", "3.4")
            else range(1, 3) if folder == "详情" else [1]
        ):
            Image.new("RGB", (60, 80), "black" if number % 2 else "gray").save(
                directory / f"{number}.png"
            )
    return scan_products(root)[0]


class Client:
    model = "models/gemini-3.5-flash-lite"

    def __init__(self):
        self.cancel = threading.Event()
        self.calls = 0

    def generate(self, parts, schema):
        self.calls += 1
        return {
            "colors": [
                {
                    "color": color,
                    "photo_id": str(i),
                    "view": "正面",
                    "confidence": 0.98,
                    "needs_review": False,
                    "reason": "前门襟可见",
                }
                for i, color in enumerate(("灰色", "黑色"))
            ],
            "sizes": [
                {
                    "name": name,
                    "weight": weight,
                    "confidence": 0.98,
                    "needs_review": False,
                    "reason": "尺码表原文",
                }
                for name, weight in [
                    ("M", "80-100斤"),
                    ("L", "100-120斤"),
                    ("XL", "120-140斤"),
                    ("2XL", "140-160斤"),
                    ("3XL", "160-180斤"),
                ]
            ],
        }


@pytest.fixture
def session():
    from playwright.sync_api import sync_playwright

    executable = shutil.which("chromium") or shutil.which("msedge")
    if not executable:
        for root in ("C:/Program Files (x86)", "C:/Program Files"):
            candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if candidate.exists():
                executable = str(candidate)
                break
    if not executable:
        pytest.skip("本地浏览器夹具需要已安装 Chromium / Edge")

    class Session:
        def __enter__(self):
            self.p = sync_playwright().start()
            self.browser = self.p.chromium.launch(
                executable_path=executable, headless=True
            )
            self.context = self.browser.new_context()
            self.context.route(
                "**/*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=FIXTURE.read_text(encoding="utf-8")
                    + (
                        "<script>saveReply=false</script>"
                        if getattr(self, "no_receipt", False)
                        else ""
                    ),
                ),
            )
            self.page = self.context.new_page()
            self.audit = []
            return self

        def goto(self, url):
            if self.page.url != "about:blank":
                self.audit.extend(self.page.evaluate("window.audit || []"))
            self.page.goto(url, wait_until="domcontentloaded")

        def check_login(self):
            self.goto("https://fxg.jinritemai.com/ffa/g/list")
            return "fixture-login"

        def __exit__(self, *_):
            self.audit.extend(self.page.evaluate("audit"))
            self.context.close()
            self.browser.close()
            self.p.stop()

    instance = Session()
    return instance


def test_asset_scan_first_five_and_journal_prevent_duplicates(tmp_path):
    product = make_product(tmp_path)
    assert not product.issues
    assert [p.name for p in product.main] == [f"{i}.png" for i in range(1, 6)]
    assert len(product.images["800"]) == 7 and len(product.portrait) == 5
    journal = Journal(tmp_path)
    journal.update(product, status="saved")
    assert not Journal(tmp_path).resumable(product)[0]
    journal.reset([product])
    journal.update(product, status="uncertain")
    assert not journal.resumable(product)[0]


def test_vision_uses_only_valid_fronts_and_explicit_weights(tmp_path):
    product = make_product(tmp_path)
    client = Client()
    result = analyze(client, product, ["灰色", "黑色"], threading.Event())
    assert result.colors["黑色"] == product.images["800"][1]
    assert result.sizes[-1] == {"name": "3XL", "weight": "160-180斤"}
    assert normalize_size("XXXL") == "3XL"
    bad = client.generate([], {})
    bad["colors"][0]["view"] = "背面"
    with patch.object(client, "generate", return_value=bad), pytest.raises(
        ListingError, match="正面图需核对"
    ):
        analyze(client, product, ["灰色", "黑色"], threading.Event())


def test_full_browser_workflow_preserves_sku_replaces_details_and_only_saves_draft(
    tmp_path, session
):
    product = make_product(tmp_path)
    results = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        Client(),
        threading.Event(),
        session_factory=lambda *_: session,
    ).run()
    assert results == [(product.sku, "已保存")], (
        results,
        Journal(tmp_path).get(product),
    )
    saves = [e for e in session.audit if e["event"] == "save"]
    assert len(saves) == 1 and saves[0]["sku"] == product.sku
    assert saves[0]["details"] == ["1.png", "2.png"]
    assert set(map(float, saves[0]["price"])) == {399}
    assert set(map(int, saves[0]["stock"])) == {1000}
    assert [row["name"] for row in saves[0]["sizes"]] == ["L", "XL", "2XL", "M", "3XL"]
    assert {row["name"]: row["weight"] for row in saves[0]["sizes"]}[
        "3XL"
    ] == "160-180斤"
    assert len([e for e in session.audit if e["event"] == "wizard"][0]["files"]) == 5
    assert any(e["event"] == "site-size-ai" for e in session.audit)
    assert not any(e["event"] == "FORBIDDEN_PUBLISH" for e in session.audit)
    assert Journal(tmp_path).get(product)["status"] == "saved"


def test_missing_or_unconfirmed_ai_data_never_saves(tmp_path, session):
    product = make_product(tmp_path)
    client = Client()
    bad = client.generate([], {})
    bad["sizes"][0]["needs_review"] = True
    with patch.object(client, "generate", return_value=bad):
        results = BatchRunner(
            tmp_path,
            [product],
            BrowserConfig(),
            client,
            threading.Event(),
            session_factory=lambda *_: session,
        ).run()
    assert results == [(product.sku, "失败")]
    assert not any(e["event"] == "save" for e in session.audit)
    assert Journal(tmp_path).get(product)["status"] == "failed"


def test_two_products_finish_serially_without_cross_sku_images(tmp_path, session):
    make_product(tmp_path, "货号A")
    make_product(tmp_path, "货号B")
    for path in (tmp_path / "货号B" / "800").iterdir():
        path.rename(path.with_name("B" + path.name))
    products = scan_products(tmp_path)
    results = BatchRunner(
        tmp_path,
        products,
        BrowserConfig(),
        Client(),
        threading.Event(),
        session_factory=lambda *_: session,
    ).run()
    assert results == [("货号A", "已保存"), ("货号B", "已保存")], Journal(
        tmp_path
    ).records
    saves = [event for event in session.audit if event["event"] == "save"]
    uploads = [event for event in session.audit if event["event"] == "wizard"]
    assert [event["sku"] for event in saves] == ["货号A", "货号B"]
    assert uploads[0]["files"][0] == "1.png" and uploads[1]["files"][0] == "B1.png"


def test_save_without_receipt_is_uncertain_and_cannot_auto_retry(tmp_path, session):
    product = make_product(tmp_path)
    session.no_receipt = True
    result = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        Client(),
        threading.Event(),
        session_factory=lambda *_: session,
    ).run()
    assert result == [(product.sku, "待核对")], Journal(tmp_path).get(product)
    assert len([e for e in session.audit if e["event"] == "save"]) == 1
    assert Journal(tmp_path).get(product)["status"] == "uncertain"
    assert not Journal(tmp_path).resumable(product)[0]


def test_scope_never_clears_another_image_field(session, tmp_path):
    with session as s:
        s.check_login()
        s.page.evaluate("editor()")
        page = DouyinPage(s, threading.Event())
        s.page.evaluate(
            "picture('keep-main.png',document.querySelector('#main .photos'));document.querySelector('#portrait').querySelector('input').remove()"
        )
        selector = page.section("主图3:4")
        assert page.photo_count(selector) == 0
        page.clear(selector)
        assert s.page.locator("#main img").count() == 1
        with pytest.raises(ListingError, match="禁止点击发布"):
            page.click("发布商品")


def test_restored_editor_refuses_a_different_sku(session):
    with session as s:
        s.check_login()
        s.page.evaluate("editor()")
        s.page.locator("#sku").fill("OTHER-SKU")
        page = DouyinPage(s, threading.Event())
        with pytest.raises(ListingError, match="不同货号"):
            page.fill_sku("CURRENT-SKU", require_same=True)
        assert s.page.locator("#sku").input_value() == "OTHER-SKU"


def test_listing_interface_has_expected_defaults(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QEvent
    from suite.listing.ui import ListingWindow

    app = QApplication.instance() or QApplication([])
    product = make_product(tmp_path)
    window = ListingWindow()
    window.directory.setText(str(tmp_path))
    window.scan_root()
    assert window.price.value() == 399 and window.inventory.value() == 1000
    assert window.table.rowCount() == 1 and window.selected()[0].sku == product.sku
    assert window.start.text() == "开始保存草稿"
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_visible_edge_reconnect_keeps_profile_window_and_other_tabs(tmp_path):
    """Exercise native launch -> worker disconnect -> another worker reconnect."""
    import os
    from suite.listing.browser import EdgeSession, profile_endpoint

    executable = shutil.which("chromium")
    if os.name == "nt" or not executable or not os.environ.get("DISPLAY"):
        pytest.skip("本检查需要 Linux Chromium 和可见 X11 显示服务")
    profile = tmp_path / "existing-edge-data"
    (profile / "Default").mkdir(parents=True)
    wrapper = tmp_path / "browser"
    wrapper.write_text(
        '#!/bin/sh\nexec "'
        + executable
        + '" --no-sandbox --disable-background-networking "$@"\n'
    )
    wrapper.chmod(0o700)
    config = BrowserConfig(str(profile), "Default", str(wrapper))
    results, errors = [], []

    def job(first):
        try:
            with EdgeSession(config, threading.Event()) as session:
                cdp = session.browser.new_browser_cdp_session()
                arguments = cdp.send("Browser.getBrowserCommandLine")["arguments"]
                cdp.detach()
                assert not any(
                    "--headless" in a or "--incognito" in a for a in arguments
                )
                assert "--profile-directory=Default" in arguments
                page = session.page
                page.route(
                    "**/*",
                    lambda route: route.fulfill(
                        content_type="text/html; charset=utf-8",
                        body="<button>新建商品</button>",
                    ),
                )
                if first:
                    session.context.add_cookies(
                        [
                            {
                                "name": "fixture-login",
                                "value": "existing-login",
                                "domain": "fxg.jinritemai.com",
                                "path": "/",
                                "secure": True,
                            }
                        ]
                    )
                    other = session.context.new_page()
                    other.goto("about:blank#ordinary-user-tab")
                    assert "已复用" in session.open_login()
                else:
                    assert any(
                        c["name"] == "fixture-login" for c in session.context.cookies()
                    )
                    assert any(
                        p.url.endswith("#ordinary-user-tab")
                        for p in session.context.pages
                    )
                    assert session.page.url.startswith("https://fxg.jinritemai.com/")
                    assert "已复用" in session.check_login()
                assert session.new_work_page() is page
                target = session.context.new_cdp_session(page)
                target_id = target.send("Target.getTargetInfo")["targetInfo"][
                    "targetId"
                ]
                target.detach()
                results.append(
                    (session.endpoint, target_id, len(session.context.pages))
                )
        except BaseException as exc:
            errors.append(exc)

    try:
        for first in (True, False):
            worker = threading.Thread(target=job, args=(first,))
            worker.start()
            worker.join(50)
            assert not worker.is_alive(), "浏览器工作线程未按时返回"
            if errors:
                raise errors[0]
            assert profile_endpoint(profile), "工作线程退出后浏览器应继续运行"
        assert results[0] == results[1], "两个工作线程应复用同一浏览器、操作标签和资料"
    finally:
        endpoint = profile_endpoint(profile)
        if endpoint:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint)
                connection = browser.new_browser_cdp_session()
                connection.send("Browser.close")


def test_edge_rejects_invalid_profile_without_launching(tmp_path):
    from suite.listing.browser import EdgeSession

    session = EdgeSession(BrowserConfig(str(tmp_path), "../Default"), threading.Event())
    with patch("suite.listing.browser.subprocess.Popen") as launch:
        with pytest.raises(ListingError, match="配置名称无效"):
            session._open_profile()
        launch.assert_not_called()
