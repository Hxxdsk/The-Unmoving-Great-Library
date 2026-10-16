import contextlib
import shutil
import threading

import pytest
from PIL import Image

from test_listing import session, short_fixture_waits, make_product, Client
from suite.listing.models import Journal, ListingError
from suite.listing.browser import BrowserConfig
from suite.listing.runner import BatchRunner
from suite.listing.site import DouyinPage
from suite.listing.guidance import UserGuide
from suite.listing.vision import prepare_color_sources


def test_reference_is_in_initial_details_but_not_single_color_photos(tmp_path, session):
    product = make_product(tmp_path)
    Image.new("RGB", (790, 1582), "white").save(
        product.path / "颜色" / "99-多色对照.png"
    )
    from suite.listing.models import scan_products

    for path in product.images["详情"]:
        path.rename(path.with_name("详情-" + path.name))
    product = scan_products(tmp_path)[0]
    product.title = "手填冬季羽绒服标题"

    class ReferenceClient(Client):
        def generate(self, parts, schema):
            if "reference_ids" in schema.get("properties", {}):
                return {"reference_ids": ["2"], "invalid_ids": []}
            if "slots" in schema.get("properties", {}):
                return {
                    "slots": [
                        {
                            "reference_id": "0",
                            "color": "灰色",
                            "box": [0, 0, 500, 1000],
                            "confidence": 0.99,
                        },
                        {
                            "reference_id": "0",
                            "color": "黑色",
                            "box": [500, 0, 1000, 1000],
                            "confidence": 0.99,
                        },
                    ]
                }
            if "matches" in schema.get("properties", {}):
                return {
                    "matches": [
                        {
                            "photo_id": str(i),
                            "slot_id": f"slot_{i}",
                            "confidence": 0.99,
                            "ambiguous": False,
                            "reason": "与对应标注服装格一致",
                        }
                        for i in range(2)
                    ]
                }
            assert any("本款多色对照图" in p.get("text", "") for p in parts)
            return super().generate(parts, schema)

    result = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        ReferenceClient(),
        threading.Event(),
        session_factory=lambda *_: session,
    ).run()
    assert result == [(product.sku, "已保存")]
    generation = next(e for e in session.audit if e["event"] == "generation")
    assert generation["title"] == product.title
    assert generation["details"] == ["1.png", "2.png", "99-多色对照.png", "尺码.png"]
    saved = next(e for e in session.audit if e["event"] == "save")
    assert saved["details"] == ["详情-1.png", "详情-2.png"]
    assert not any(
        "99-多色对照.png" in e.get("files", [])
        for e in session.audit
        if e["event"] == "specphoto"
    )
    assert not any(e["event"] == "title" for e in session.audit)
    assert len([e for e in session.audit if e["event"] == "specphoto"]) == 2


def test_weight_notes_survive_rerenders_and_other_page_focus(session):
    with session as s:
        s.check_login()
        s.page.evaluate(
            """editor();document.querySelector('#sizes .items').innerHTML='';
        for(const n of ['S','M','L','XL','2XL','3XL'])addSize(n);
        document.querySelector('#sizes .items').style='display:grid;grid-template-columns:repeat(3, 1fr)';
        document.querySelector('#colors input[placeholder=备注]').value='颜色备注不能改变';
        document.querySelector('#sizes').addEventListener('change',e=>{
            if(e.target.placeholder==='备注')e.target.replaceWith(e.target.cloneNode(true));
        });"""
        )
        ordinary = s.context.new_page()
        ordinary.set_content('<input id="ordinary" value="日常窗口内容">')
        ordinary.bring_to_front()
        desired = dict(
            zip(
                ["S", "M", "L", "XL", "2XL", "3XL"],
                [
                    "85-100斤",
                    "100-120斤",
                    "120-140斤",
                    "140-160斤",
                    "160-175斤",
                    "175-190斤",
                ],
            )
        )
        page = DouyinPage(s, threading.Event(), log=print)
        page.set_sizes([{"name": n, "weight": w} for n, w in desired.items()])
        _, records = page.specs("尺码大小")
        assert {
            r["name"]: s.page.locator(r["note"]).input_value() for r in records
        } == desired
        assert (
            s.page.locator("#colors input[placeholder=备注]").first.input_value()
            == "颜色备注不能改变"
        )
        assert ordinary.locator("#ordinary").input_value() == "日常窗口内容"
        ordinary.close()


def test_new_creation_page_is_found_even_without_popup_event(session):
    with session as s:
        s.check_login()
        page = DouyinPage(s, threading.Event(), log=print)
        created = s.context.new_page()
        created.goto("https://fxg.jinritemai.com/ffa/g/create")
        created.evaluate("wizard()")
        assert page.popups == []
        assert page.creation_state() == "upload"
        assert page.page is s.page is created


def test_guidance_accepts_anonymous_live_target_and_acknowledges_click(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            '<div role="button" style="width:80px;height:40px" onclick="window.clicked=(window.clicked||0)+1"><svg width="40" height="20"></svg></div>'
        )
        notices = []

        class ClickGuide(UserGuide):
            def install(self, page, mode):
                super().install(page, mode)
                page.locator("svg").click()

        guide = ClickGuide(threading.Event(), lambda goal, mode: notices.append(goal))
        target, _ = guide.run(s.page, "选择正确位置")
        assert s.page.evaluate("window.clicked||0") == 0
        target.click()
        assert s.page.evaluate("window.clicked") == 1
        assert any("已收到点击" in n for n in notices)
        assert s.page.evaluate("Boolean(window.__glHumanStop)") is False


def test_size_chart_direct_local_upload_does_not_require_a_dialog(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        s.check_login()
        s.page.evaluate(
            """editor();const input=document.createElement('input');input.type='file';input.id='direct-size';input.style.display='none';input.onchange=()=>window.uploadedSize=input.files[0].name;document.body.append(input);
        [...document.querySelectorAll('button')].find(e=>e.textContent==='AI解析尺码图').onclick=()=>input.click();"""
        )
        page = DouyinPage(s, threading.Event(), log=print)
        assert page.try_parse_size_chart(product.images["尺码"], tmp_path)
        assert s.page.evaluate("window.uploadedSize") == product.images["尺码"][0].name


def test_optional_size_chart_failure_continues_without_human_wait(
    tmp_path, session, monkeypatch
):
    product = make_product(tmp_path)

    def unavailable(*_):
        raise ListingError("图片不支持或入口暂不可用")

    monkeypatch.setattr(DouyinPage, "parse_size_chart", unavailable)
    guide = UserGuide(
        threading.Event(), lambda *_: pytest.fail("可选上传不应等待用户指导")
    )
    result = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        Client(),
        threading.Event(),
        session_factory=lambda *_: session,
        guide=guide,
    ).run()
    assert result == [(product.sku, "已保存")]
    assert next(e for e in session.audit if e["event"] == "save")["sizes"]


def test_processed_records_require_reset_and_do_not_resume_editor(
    tmp_path, session, monkeypatch
):
    product = make_product(tmp_path)
    journal = Journal(tmp_path)
    journal.update(
        product,
        status="failed",
        editor_url="https://fxg.jinritemai.com/ffa/g/edit?id=old",
    )
    monkeypatch.setattr(
        DouyinPage, "resume", lambda *_: pytest.fail("禁止恢复旧编辑页")
    )
    with session as s:
        runner = BatchRunner(
            tmp_path,
            [product],
            BrowserConfig(),
            Client(),
            threading.Event(),
            session_factory=lambda *_: contextlib.nullcontext(s),
        )
        assert runner.run() == [(product.sku, "跳过")]
        journal.reset([product])
        assert runner.run() == [(product.sku, "已保存")]


def test_optional_wash_upload_failure_does_not_stop_later_fields(
    tmp_path, session, monkeypatch
):
    product = make_product(tmp_path)
    original = DouyinPage.replace_images
    logs = []

    def reject_wash(self, names, paths):
        if names[0] == "水洗标/吊牌图":
            raise ListingError("本款水洗标图片被拒绝")
        return original(self, names, paths)

    monkeypatch.setattr(DouyinPage, "replace_images", reject_wash)
    result = BatchRunner(
        tmp_path,
        [product],
        BrowserConfig(),
        Client(),
        threading.Event(),
        session_factory=lambda *_: session,
        log=logs.append,
    ).run()
    assert result == [(product.sku, "已保存")]
    assert any("跳过水洗标图片" in line for line in logs)
    saved = next(e for e in session.audit if e["event"] == "save")
    assert saved["sku"] == product.sku and set(map(int, saved["price"])) == {399}


def test_title_input_is_per_product_persisted_and_has_correct_status_columns(tmp_path):
    from PySide6.QtWidgets import QApplication
    from suite.listing.ui import ListingWindow

    app = QApplication.instance() or QApplication([])
    product = make_product(tmp_path)
    window = ListingWindow(force_exit=lambda _: None)
    try:
        window.directory.setText(str(tmp_path))
        window.scan_root()
        window.table.cellWidget(0, 2).setText("每款记忆的手填标题")
        window.scan_root()
        assert window.products[0].title == "每款记忆的手填标题"
        assert window.table.cellWidget(0, 2).text() == window.products[0].title
        window.update_row(product.sku, "已保存", "明确保存成功", 100)
        assert window.table.item(0, 7).text() == "已保存"
        assert window.table.item(0, 9).text() == "明确保存成功"
    finally:
        window.close()


def test_extension_keeps_new_work_pages_in_background():
    import subprocess
    from pathlib import Path
    from suite.listing.bridge import EXTENSION_DIR

    node = (
        shutil.which("node")
        or "/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/node"
    )
    if not Path(node).exists():
        pytest.skip("后台窗口策略测试需要 Node")
    result = subprocess.run(
        [
            node,
            str(Path(__file__).parent / "fixtures" / "listing_background_policy.cjs"),
            str(EXTENSION_DIR / "background.js"),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert "focus suppression: passed" in result.stdout


def test_edge_initialization_error_retains_cause_and_hides_token():
    from unittest.mock import patch
    from suite.listing.browser import EdgeSession

    token = "a" * 64
    config = BrowserConfig("ws://127.0.0.1:12345/cdp/" + token)
    with patch("playwright.sync_api.sync_playwright") as factory:
        driver = factory.return_value.start.return_value
        driver.chromium.connect_over_cdp.side_effect = RuntimeError(
            "Cannot create an unfocused maximized window: " + config.endpoint
        )
        with pytest.raises(ListingError) as error:
            EdgeSession(config, threading.Event()).__enter__()
        assert "Cannot create an unfocused maximized window" in str(error.value)
        assert token not in str(error.value)
        assert "重新加载" in str(error.value)
        driver.stop.assert_called_once()
