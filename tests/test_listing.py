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
    for folder in ("800", "3.4", "白底", "详情", "水洗标", "尺码", "颜色"):
        directory = root / name / folder
        directory.mkdir(parents=True)
        for number in (
            range(1, 8)
            if folder in ("800", "3.4")
            else range(1, 3) if folder in ("详情", "颜色") else [1]
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
    assert result.colors["黑色"] == product.images["颜色"][1]
    assert result.sizes[-1] == {"name": "3XL", "weight": "160-180斤"}
    assert normalize_size("XXXL") == "3XL"
    bad = client.generate([], {})
    bad["colors"][0]["view"] = "背面"
    with patch.object(client, "generate", return_value=bad):
        partial = analyze(client, product, ["灰色", "黑色"], threading.Event())
    assert "灰色" not in partial.colors
    assert partial.skipped_colors == ["灰色"]


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
    assert window.login.text() == "连接 Edge"
    assert not hasattr(window, "user_data") and not hasattr(window, "advanced")
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_edge_bridge_rejects_unpaired_and_foreign_origin():
    from urllib.request import build_opener, ProxyHandler
    from urllib.error import HTTPError
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus
    from suite.listing.bridge import EdgeBridge

    bridge = EdgeBridge()
    try:
        config = bridge.start()
        local = build_opener(ProxyHandler({}))
        with local.open(bridge.connect_url.split("#")[0]) as reply:
            assert "加载解压缩的扩展" in reply.read().decode()
        with pytest.raises(HTTPError) as failure:
            local.open(f"http://127.0.0.1:{bridge.port}/cdp/wrong-token")
        assert failure.value.code == 403
        with pytest.raises(InvalidStatus):
            connect(config.endpoint, origin="https://untrusted.example", proxy=None)
        with connect(config.endpoint, proxy=None) as socket:
            with pytest.raises(Exception, match="请先点击连接 Edge"):
                socket.recv()
    finally:
        bridge.close()


def test_edge_extension_relay_full_draft_and_reconnect(tmp_path):
    """Real Chromium + unchanged extension JS; Chrome APIs use a Node stand-in.

    This is not an installed-extension test: cloud policy blocks unpacked
    extensions. No policy is changed, and all merchant requests are local fixtures.
    """
    import os
    import subprocess
    import time
    from urllib.request import build_opener, ProxyHandler
    from websockets.sync.client import connect
    from suite.listing.bridge import EdgeBridge, EXTENSION_DIR
    from suite.listing.browser import EdgeSession

    executable = shutil.which("chromium")
    node = (
        shutil.which("node")
        or "/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/node"
    )
    if (
        os.name == "nt"
        or not executable
        or not Path(node).exists()
        or not os.environ.get("DISPLAY")
    ):
        pytest.skip(
            "连接协议夹具需要 Linux Chromium、Node 和 X11；不代表真实 Edge 扩展测试"
        )
    bridge = EdgeBridge()
    config = bridge.start()
    profile = tmp_path / "fixture-browser"
    profile.mkdir()
    browser = subprocess.Popen(
        [
            executable,
            "--no-sandbox",
            "--disable-background-networking",
            "--no-first-run",
            "--remote-debugging-port=0",
            "--user-data-dir=" + str(profile),
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    host = None
    try:
        deadline = time.monotonic() + 15
        while not (profile / "DevToolsActivePort").exists():
            assert time.monotonic() < deadline, "本地浏览器没有启动"
            time.sleep(0.1)
        port, path = (profile / "DevToolsActivePort").read_text().splitlines()[:2]
        real_endpoint = f"ws://127.0.0.1:{port}{path}"
        with connect(real_endpoint, proxy=None) as socket:
            socket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Storage.setCookies",
                        "params": {
                            "cookies": [
                                {
                                    "name": "fixture-login",
                                    "value": "already-logged-in",
                                    "domain": "fxg.jinritemai.com",
                                    "path": "/",
                                    "secure": True,
                                }
                            ]
                        },
                    }
                )
            )
            assert json.loads(socket.recv()).get("result") == {}
        host = subprocess.Popen(
            [
                node,
                str(FIXTURE.with_name("listing_extension_host.cjs")),
                str(EXTENSION_DIR / "background.js"),
                real_endpoint,
                str(bridge.port),
                bridge.token,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert bridge.connected.wait(10), "扩展协议夹具未连接"

        class FixtureSession(EdgeSession):
            def __enter__(self):
                super().__enter__()
                self.page.route(
                    "**/*",
                    lambda route: route.fulfill(
                        content_type="text/html; charset=utf-8",
                        body=FIXTURE.read_text(),
                    ),
                )
                return self

        product = make_product(tmp_path / "products")
        result = BatchRunner(
            tmp_path / "products",
            [product],
            config,
            Client(),
            threading.Event(),
            session_factory=FixtureSession,
        ).run()
        assert result == [(product.sku, "已保存")]
        assert Journal(tmp_path / "products").get(product)["status"] == "saved"
        # Reconnect from another worker, retaining existing profile login and tabs.
        errors = []

        def reconnect():
            try:
                with FixtureSession(config, threading.Event()) as session:
                    assert len(session.context.pages) == 1
                    session.check_login()
                    assert "fixture-login=already-logged-in" in session.page.evaluate(
                        "document.cookie"
                    )
                    from suite.listing.page_ai import PageAI

                    navigation = NavigationClient(lambda *_: ("target", "新建商品 ⌄"))
                    target = PageAI(session.page, navigation, threading.Event()).find(
                        "创建商品入口"
                    )
                    assert "新建商品" in target.inner_text()
                    assert len(navigation.calls) == 1
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=reconnect)
        worker.start()
        worker.join(30)
        assert not worker.is_alive()
        if errors:
            raise errors[0]
        with connect(config.endpoint, proxy=None) as socket:
            socket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Target.closeTarget",
                        "params": {"targetId": "not-a-work-tab"},
                    }
                )
            )
            while True:
                reply = json.loads(socket.recv())
                if reply.get("id") == 1:
                    assert "禁止关闭用户原有标签页" in reply["error"]["message"]
                    break
        local = build_opener(ProxyHandler({}))
        with local.open(f"http://127.0.0.1:{port}/json/list") as reply:
            targets = json.load(reply)
        assert any(t["url"] == "about:blank#ordinary-user-tab" for t in targets)
        assert bridge.connected.is_set() and browser.poll() is None
    finally:
        bridge.close()
        if host:
            host.terminate()
            host.wait(5)
            host.stderr.close()
        if browser.poll() is None:
            os.killpg(browser.pid, 15)
        browser.wait(10)


class NavigationClient:
    def __init__(self, decide):
        self.decide, self.calls = decide, []

    def generate(self, parts, schema):
        candidates = json.loads(parts[0]["text"].split("\n候选：", 1)[1])
        self.calls.append((parts, candidates))
        action, text = self.decide(len(self.calls), candidates)
        candidate = next((c for c in candidates if c["text"] == text), None)
        return {
            "action": action,
            "candidate_id": candidate["id"] if candidate else "not-a-real-id",
            "confidence": 0.78,
            "reason": "根据当前页面图像识别菜单入口",
        }


def test_page_ai_hovers_then_clicks_a_renamed_menu(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>#menu{display:none}#entry:hover #menu{display:block}</style>
            <div id="entry"><button>创建货品</button><div id="menu"><button onclick="window.picked=true">独立商品</button></div></div>"""
        )
        client = NavigationClient(
            lambda number, _: (
                ("hover", "创建货品") if number == 1 else ("target", "独立商品")
            )
        )
        page = DouyinPage(s, threading.Event(), client=client)
        page.click("单商品")
        assert s.page.evaluate("window.picked") is True
        assert len(client.calls) == 2
        assert all(
            parts[1]["inlineData"]["mimeType"] == "image/jpeg"
            for parts, _ in client.calls
        )
        assert "独立商品" not in [c["text"] for c in client.calls[0][1]]
        assert "独立商品" in [c["text"] for c in client.calls[1][1]]
        s.page.evaluate("window.audit=[]")


def test_page_ai_refuses_publish_and_unlisted_candidates(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            '<button onclick="window.published=true">发布 商品</button><button>其他</button>'
        )
        client = NavigationClient(lambda *_: ("target", "发布 商品"))
        page = DouyinPage(s, threading.Event(), client=client)
        with pytest.raises(ListingError, match="涉及发布"):
            page.click("继续生成")
        assert s.page.evaluate("window.published || false") is False
        s.page.locator("button").first.evaluate("e=>e.textContent='下架'")
        page.client = NavigationClient(lambda *_: ("target", "下架"))
        with pytest.raises(ListingError, match="涉及发布"):
            page.click("继续生成")
        assert s.page.evaluate("window.published || false") is False
        client = NavigationClient(lambda *_: ("target", "页面中不存在的控件"))
        page.client = client
        with pytest.raises(ListingError, match="不属于当前页面候选"):
            page.click("继续生成")
        s.page.evaluate("window.audit=[]")


def test_page_ai_does_not_click_after_cancel_or_target_disappears(session):
    from suite.listing.models import Cancelled

    with session as s:
        s.check_login()
        s.page.set_content(
            '<button id="candidate" onclick="window.clicked=true">新的下一步</button>'
        )
        cancel = threading.Event()

        def cancel_decision(*_):
            cancel.set()
            return "target", "新的下一步"

        client = NavigationClient(cancel_decision)
        page = DouyinPage(s, cancel, client=client)
        with pytest.raises(Cancelled):
            page.click("继续生成")
        assert s.page.evaluate("window.clicked || false") is False
        cancel.clear()

        def remove_decision(number, *_):
            if number > 1:
                return "not_found", ""
            s.page.locator("#candidate").evaluate("e=>e.remove()")
            return "target", "新的下一步"

        client = NavigationClient(remove_decision)
        page.client = client
        with pytest.raises(ListingError, match="暂未定位"):
            page.click("继续生成")
        assert s.page.evaluate("window.clicked || false") is False
        s.page.evaluate("window.audit=[]")
