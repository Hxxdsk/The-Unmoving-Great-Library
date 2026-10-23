import threading
from contextlib import nullcontext

from test_listing import session, short_fixture_waits, make_product, Client, FIXTURE
from suite.listing.site import DouyinPage
from suite.listing.runner import BatchRunner
from suite.listing.browser import BrowserConfig


def test_color_rows_exclude_ghost_search_empty_inputs_and_following_sizes(session):
    colors = ["黑色", "月光蓝", "浅绿", "卡其", "粉紫", "深灰", "天池蓝"]
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>.line{position:relative;display:flex;width:300px;height:38px;gap:5px;align-items:center}.line input{width:100px}</style><label>颜色分类</label><div id="colors"></div><div id="noise"></div><div>尺码大小<span> 添加规格图</span></div><div id="sizes"></div><label>尺码表</label>"""
        )
        s.page.evaluate(
            """colors=>{
          document.querySelector('#colors').innerHTML=colors.map(n=>`<div class="line"><input readonly value="${n}"><input placeholder="备注"><input style="position:absolute;left:10px;opacity:0;width:20px" value=""></div>`).join('');
          document.querySelector('#noise').innerHTML=Array.from({length:5},()=>'<div class="line"><span>非规格控件</span><input></div>').join('');
          document.querySelector('#sizes').innerHTML=['S','M','L','XL','2XL'].map(n=>`<div class="line"><input readonly value="${n}"><input placeholder="备注"></div>`).join('');
        }""",
            colors,
        )
        page = DouyinPage(s, threading.Event())
        _, rows = page.specs("颜色分类")
        assert [r["name"] for r in rows] == colors
        _, sizes = page.specs("尺码大小")
        assert [r["name"] for r in sizes] == ["S", "M", "L", "XL", "2XL"]


def test_partial_legacy_row_cannot_override_unresolved_layout(session):
    class Reader:
        def generate(self, parts, schema):
            return {"rows": [{"row_id": "row_0", "name": "灰色"}]}

    with session as s:
        s.check_login()
        s.page.set_content(
            """<label>颜色分类</label><div style="display:flex;gap:5px;align-items:center;height:38px"><div style="width:120px;height:30px"><canvas width="120" height="30"></canvas></div><input placeholder="备注" style="width:90px"></div><div style="display:flex;gap:5px;align-items:center;height:38px"><input readonly value="黑色" style="width:120px"><input placeholder="备注" style="width:90px"></div><label>尺码大小</label>"""
        )
        page = DouyinPage(s, threading.Event(), client=Reader())
        original = page.dom

        def dom(op, *args, **kwargs):
            if op == "specs":
                note = s.page.locator('input[placeholder="备注"]').first
                selector = note.evaluate(
                    'e=>{e.dataset.glId=e.dataset.glId||"legacy-note";return "[data-gl-id="+e.dataset.glId+"]"}'
                )
                return [
                    {
                        "name": "错误的单项",
                        "note": selector,
                        "row": "body",
                        "control": "body",
                        "photos": 0,
                    }
                ]
            return original(op, *args, **kwargs)

        page.dom = dom
        _, rows = page.specs("颜色分类")
        assert [r["name"] for r in rows] == ["灰色", "黑色"]


def test_workflow_fills_panels_and_specs_from_top_to_bottom(
    tmp_path, session, monkeypatch
):
    product = make_product(tmp_path)
    calls = []
    original_tab = DouyinPage.tab

    def tab(self, name):
        calls.append(("tab", name))
        return original_tab(self, name)

    monkeypatch.setattr(DouyinPage, "tab", tab)
    for name in ("set_color_photos", "set_sizes", "batch_values"):
        original = getattr(DouyinPage, name)

        def wrapper(self, *args, _name=name, _original=original, **kwargs):
            calls.append(("action", _name))
            if _name == "batch_values":
                _, rows = self.specs("尺码大小")
                assert all(self.page.locator(r["note"]).input_value() for r in rows)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(DouyinPage, name, wrapper)
    # Move white-background above 3:4 to prove the order follows the live page.
    with session as s:
        s.context.unroute("**/*")
        s.context.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html; charset=utf-8",
                body=FIXTURE.read_text()
                + "<script>const oldEditor=editor;editor=()=>{oldEditor();document.querySelector('#portrait').before(document.querySelector('#white'))}</script>",
            ),
        )
        result = BatchRunner(
            tmp_path,
            [product],
            BrowserConfig(),
            Client(),
            threading.Event(),
            session_factory=lambda *_: nullcontext(s),
            upload_colors=True,
        ).run()
        assert result == [(product.sku, "已保存")]
        fields = [
            e["field"]
            for e in s.page.evaluate("window.audit")
            if e["event"] == "upload"
        ]
        assert fields.index("white") < fields.index("portrait") < fields.index("wash")
        assert [n for kind, n in calls if kind == "tab"][:3] == [
            "图文信息",
            "属性信息",
            "价格库存",
        ]
        assert [n for kind, n in calls if kind == "action"] == [
            "set_color_photos",
            "set_sizes",
            "batch_values",
        ]
