import base64
import io
import threading

import pytest
from PIL import Image

from test_listing import session, short_fixture_waits
from suite.listing.site import DouyinPage
from suite.listing.spec_reader import decode_rows


@pytest.mark.parametrize("ids", [["row_0", "row_1"], ["0", "1"], [0.0, 1.0], [1, 2]])
def test_spec_number_variants_preserve_row_binding(ids):
    result = {"rows": [{"row_id": i, "name": n} for i, n in zip(ids, ["黑色", "深灰"])]}
    assert decode_rows(result, [0, 1]) == {0: "黑色", 1: "深灰"}


def test_invalid_spec_rows_are_isolated_without_losing_valid_ones():
    result = {
        "rows": [
            {"row_id": "row_0", "name": "黑色"},
            {"row_id": "row_0", "name": "黑色"},
            {"row_id": "no-such-row", "name": "浅绿"},
            None,
            {"row_id": "row_1", "name": ""},
        ]
    }
    assert decode_rows(result, [0, 1]) == {0: "黑色"}


def test_invalid_batch_retries_only_unknown_caption_then_reuses_cache(session):
    class Reader:
        calls = 0

        def generate(self, parts, schema):
            self.calls += 1
            if self.calls == 1:
                assert schema["properties"]["rows"]["items"]["properties"]["row_id"][
                    "enum"
                ] == ["row_0"]
                return {"rows": [{"row_id": "wrong-id", "name": ""}]}
            assert self.calls == 2, "成功行不得反复请求AI"
            assert "row_id" not in schema["properties"]
            return {"name": "黑色"}

    with session as s:
        s.check_login()
        s.page.set_content(
            """<label>颜色分类</label><div style="display:flex;align-items:center;gap:5px"><div style="width:120px;height:32px"><canvas width="120" height="32"></canvas></div><input placeholder="备注" style="width:88px"><svg width="25" height="25"><rect width="20" height="20"/></svg></div><label>尺码大小</label>"""
        )
        s.page.evaluate(
            "()=>{const c=document.querySelector('canvas').getContext('2d');c.font='18px sans-serif';c.fillText('Black',5,22)}"
        )
        client = Reader()
        page = DouyinPage(s, threading.Event(), client=client)
        _, first = page.specs("颜色分类")
        _, second = page.specs("颜色分类")
        assert first[0]["name"] == second[0]["name"] == "黑色"
        assert first[0]["note"] == second[0]["note"]
        assert client.calls == 2


def test_caption_crop_captures_visible_value_beneath_transparent_input(session):
    class Reader:
        calls = 0

        def generate(self, parts, schema):
            self.calls += 1
            part = next(p["inlineData"] for p in parts if "inlineData" in p)
            im = Image.open(io.BytesIO(base64.b64decode(part["data"]))).convert("L")
            assert im.width >= 100
            assert im.getextrema()[0] < 80, "截图必须包含实际可见文字"
            return {
                "rows": [{"row_id": "0", "name": "黑色" if self.calls == 1 else "深灰"}]
            }

    with session as s:
        s.check_login()
        s.page.set_content(
            """<label>颜色分类</label><div style="display:flex;align-items:center;gap:5px"><div style="position:relative;width:120px;height:32px"><canvas width="120" height="32"></canvas><input readonly role="combobox" style="position:absolute;inset:0;opacity:0;width:120px;height:32px"></div><input placeholder="备注" style="width:88px"><svg width="25" height="25"><rect width="20" height="20"/></svg></div><label>尺码大小</label>"""
        )
        s.page.evaluate(
            "()=>{const c=document.querySelector('canvas').getContext('2d');c.font='18px sans-serif';c.fillText('Black',5,22)}"
        )
        client = Reader()
        page = DouyinPage(s, threading.Event(), client=client)
        assert page.specs("颜色分类")[1][0]["name"] == "黑色"
        s.page.evaluate(
            "()=>{const c=document.querySelector('canvas').getContext('2d');c.clearRect(0,0,120,32);c.fillText('Dark Gray',5,22)}"
        )
        assert page.specs("颜色分类")[1][0]["name"] == "深灰"
        assert client.calls == 2
