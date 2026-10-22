import threading
import time
from types import SimpleNamespace

import pytest

from test_listing import session, short_fixture_waits, Client, make_product
from suite.listing.browser import EdgeSession, BrowserConfig, is_connection_failure
from suite.listing.models import Journal
from suite.listing.runner import BatchRunner
from suite.listing.site import DouyinPage
from suite.listing.workflow import WorkflowStore, DESCRIBE, page_key, replay


def test_cleanup_closes_only_idle_entry_pages_of_session():
    class Page:
        def __init__(self, url):
            self.url, self.closed = url, False

        def is_closed(self):
            return self.closed

        def close(self):
            self.closed = True

    blank = Page("about:blank")
    entry = Page("https://fxg.jinritemai.com/ffa/g/list?tab=all")
    editor = Page("https://fxg.jinritemai.com/ffa/g/edit?id=old")
    other = Page("https://fxg.jinritemai.com/ffa/g/list?task=other")
    current = Page("https://fxg.jinritemai.com/ffa/g/create")
    s = EdgeSession(BrowserConfig(), threading.Event())
    s.page, s.entry_pages = current, {blank, entry, editor}
    s.release_entry_pages()
    assert blank.closed and entry.closed
    assert not current.closed and not editor.closed and not other.closed
    s.entry_pages = {current}
    s.release_entry_pages()
    assert not current.closed


def test_confirmed_child_creation_closes_management_but_keeps_child(tmp_path, session):
    from test_listing import FIXTURE

    product = make_product(tmp_path)
    with session as s:
        s.context.unroute("**/*")
        script = """<script>if(location.pathname.endsWith('/create'))wizard();else{list=()=>app.innerHTML='<a href="/ffa/g/create" target="_blank">新建商品</a>';list()}</script>"""
        s.context.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html", body=FIXTURE.read_text() + script
            ),
        )
        s.check_login()
        entry = s.page
        closed = []

        def release():
            assert s.page is not entry and not s.page.is_closed()
            assert "/create" in s.page.url or "/edit" in s.page.url
            entry.close()
            closed.append(True)

        s.release_entry_pages = release
        page = DouyinPage(s, threading.Event())
        page.create(product)
        assert closed == [True] and entry.is_closed()
        assert s.page.locator("#sku").count() == 1 and not s.page.is_closed()


def test_memory_recovers_locally_without_screenshot_or_ai(
    tmp_path, session, monkeypatch
):
    with session as s:
        s.check_login()
        s.page.set_content(
            '<button id="old" onclick="window.done=true">继续处理</button>'
        )
        store = WorkflowStore(tmp_path / "memory.json")
        store.remember(
            "进入下一步",
            page_key(s.page),
            [{"kind": "target", "target": s.page.locator("button").evaluate(DESCRIBE)}],
        )
        s.page.locator("button").evaluate("e=>e.id='new'")
        monkeypatch.setattr(
            s.page, "screenshot", lambda **_: pytest.fail("已知步骤不应截图")
        )

        class NoAI:
            def generate(self, *_):
                pytest.fail("已知步骤不应请求模型")

        page = DouyinPage(s, threading.Event(), client=NoAI(), memory=store)
        page.click("进入下一步")
        assert s.page.evaluate("window.done")


def test_stale_hover_memory_times_out_then_uses_live_control(
    tmp_path, session, monkeypatch
):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<button id="old" style="position:absolute;left:0;top:0">旧入口</button>
            <div style="position:absolute;left:0;top:0;width:180px;height:90px;z-index:100">遮挡</div>
            <button id="live" style="position:absolute;left:0;top:150px" onclick="window.done=true">图文信息</button>"""
        )
        store = WorkflowStore(tmp_path / "memory.json")
        store.remember(
            "图文信息",
            page_key(s.page),
            [
                {"kind": "hover", "target": s.page.locator("#old").evaluate(DESCRIBE)},
                {
                    "kind": "target",
                    "target": s.page.locator("#live").evaluate(DESCRIBE),
                },
            ],
        )
        monkeypatch.setattr(
            s.page, "screenshot", lambda **_: pytest.fail("可见控件无需截图")
        )
        started = time.monotonic()
        page = DouyinPage(s, threading.Event(), memory=store)
        page.click("图文信息")
        assert time.monotonic() - started < 3
        assert s.page.evaluate("window.done")
        assert not store.recipes("图文信息", page_key(s.page))


def test_unbounded_memory_and_exhausted_budget_never_click(session):
    with session as s:
        s.check_login()
        s.page.set_content('<button onclick="window.wrong=true">步骤</button>')
        descriptor = s.page.locator("button").evaluate(DESCRIBE)
        assert replay(s.page, [{"kind": "click", "target": descriptor}] * 17) is None
        assert (
            replay(s.page, [{"kind": "click", "target": descriptor}], budget=0) is None
        )
        assert not s.page.evaluate("window.wrong||false")


def test_canvas_spec_memory_skips_screenshot_until_pixels_change(session, monkeypatch):
    class Reader:
        def __init__(self):
            self.calls = 0

        def generate(self, parts, schema):
            self.calls += 1
            return {
                "rows": [
                    {"row_id": "row_0", "name": "黑色" if self.calls == 1 else "深灰"}
                ]
            }

    with session as s:
        s.check_login()
        s.page.set_content(
            """<section><label>颜色分类</label><div style="display:flex;gap:5px;align-items:center">
            <div style="width:120px;height:32px"><canvas width="120" height="32"></canvas></div>
            <input placeholder="备注" style="width:88px;height:26px"></div></section><label>尺码大小</label>"""
        )
        s.page.evaluate(
            "const ctx=document.querySelector('canvas').getContext('2d');ctx.font='18px sans-serif';ctx.fillText('black',10,22)"
        )
        captures = []
        original = s.page.screenshot

        def capture(**kwargs):
            captures.append(True)
            return original(**kwargs)

        monkeypatch.setattr(s.page, "screenshot", capture)
        reader = Reader()
        page = DouyinPage(s, threading.Event(), client=reader)
        assert page.specs("颜色分类")[1][0]["name"] == "黑色"
        count = len(captures)
        assert count > 0
        assert page.specs("颜色分类")[1][0]["name"] == "黑色"
        assert len(captures) == count and reader.calls == 1
        s.page.evaluate(
            "const c=document.querySelector('canvas').getContext('2d');c.clearRect(0,0,120,32);c.fillText('grey',10,22)"
        )
        assert page.specs("颜色分类")[1][0]["name"] == "深灰"
        assert len(captures) > count and reader.calls == 2


@pytest.mark.parametrize("uncertain", [False, True])
def test_only_confirmed_saved_draft_can_enter_energy_saving(
    tmp_path, session, uncertain
):
    product = make_product(tmp_path)
    parked = []
    session.no_receipt = uncertain
    session.park_saved_page = lambda: parked.append(True)
    result = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        Client(),
        threading.Event(),
        session_factory=lambda *_: session,
    ).run()
    assert result == [(product.sku, "待核对" if uncertain else "已保存")]
    assert parked == ([] if uncertain else [True])
    assert Journal(tmp_path).get(product)["status"] == (
        "uncertain" if uncertain else "saved"
    )


def test_renderer_crash_is_a_stop_condition():
    assert is_connection_failure(RuntimeError("Page crashed"))
    assert is_connection_failure(RuntimeError("Target crashed"))


def test_extension_serializes_creation_and_manages_saved_page_lifecycle():
    import shutil
    import subprocess
    from pathlib import Path
    from suite.listing.bridge import EXTENSION_DIR

    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node 运行扩展测试")
    result = subprocess.run(
        [
            node,
            str(Path(__file__).parent / "fixtures/listing_stability.cjs"),
            str(EXTENSION_DIR / "background.js"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "debugger deduplication" in result.stdout
