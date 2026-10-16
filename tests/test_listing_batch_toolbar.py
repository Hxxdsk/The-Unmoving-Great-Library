import threading

from test_listing import session, short_fixture_waits
from suite.listing.site import DouyinPage


def test_actual_batch_toolbar_is_found_without_using_navigation_heading(session):
    with session as s:
        s.check_login()
        s.page.set_content("""<nav><button>价格库存</button></nav>
        <aside><input placeholder="价格" value="123"><input placeholder="现货库存" value="456"><button>批量设置</button></aside>
        <div id="actual-card"><div>价格与库存<span>价格填写进度 0%</span></div>
          <div id="toolbar"><select><option>全部颜色分类</option></select><select><option>全部尺码大小</option></select>
          <input id="batch-price" placeholder="价格"><input id="batch-stock" placeholder="现货库存"><input placeholder="商家编码">
          <button id="apply" disabled>批量设置</button></div>
          <table><thead><tr><th>颜色分类</th><th>尺码大小</th><th>*价格</th><th>*现货库存</th><th>SKUID</th><th>SKU状态</th></tr></thead>
          <tbody><tr><td rowspan="2">黑色</td><td>2XL（160-175斤）</td><td><input class="row-price" placeholder="请输入"></td><td><input class="row-stock" value="0"></td><td></td><td>已上架</td></tr>
          <tr><td>L（120-140斤）</td><td><input class="row-price" placeholder="请输入"></td><td><input class="row-stock" value="0"></td><td></td><td>已上架</td></tr></tbody></table>
        </div><script>(()=>{
        const price=document.querySelector('#batch-price'),stock=document.querySelector('#batch-stock'),button=document.querySelector('#apply');
        for(const e of [price,stock])e.oninput=()=>button.disabled=!(price.value&&stock.value);
        button.onclick=()=>{for(const e of document.querySelectorAll('.row-price'))e.value=price.value;for(const e of document.querySelectorAll('.row-stock'))e.value=stock.value;window.batchCalls=(window.batchCalls||0)+1};
        })();</script>""")
        page = DouyinPage(s, threading.Event())
        page.section = lambda *_: (_ for _ in ()).throw(
            AssertionError("不能寻找顶部价格库存标签或依赖字段标题")
        )
        assert page.batch_values(399, 1000) is True
        assert s.page.locator("#batch-price").input_value() == "399"
        assert s.page.locator("#batch-stock").input_value() == "1000"
        assert s.page.evaluate("window.batchCalls") == 1
        assert s.page.locator("aside input").first.input_value() == "123"
        assert s.page.locator("aside input").last.input_value() == "456"
        fields = page.price_inventory_fields()
        rows = page.dom("sku_rows", selector=fields["selector"])
        assert all(r["price"] == "399" and r["stock"] == "1000" for r in rows)


def test_div_sku_table_and_row_role_toolbar_need_no_native_headers(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>.sku{display:flex;gap:30px;height:45px}.sku input{width:120px}</style><nav><button>价格库存</button></nav>
        <section id="card"><div>价格与库存</div><div role="row" id="bar"><input id="p" placeholder="价格"><input id="q" placeholder="现货库存"><input placeholder="商家编码"><button id="apply">批量设置</button></div>
        <div class="custom-grid"><div>颜色分类　尺码大小　价格　现货库存</div><div class="sku"><input class="pr" placeholder="请输入"><input class="st" value="0"></div><div class="sku"><input class="pr" placeholder="请输入"><input class="st" value="0"></div></div></section>
        <script>(()=>{document.querySelector('#apply').onclick=()=>{for(const e of document.querySelectorAll('.pr'))e.value=document.querySelector('#p').value;for(const e of document.querySelectorAll('.st'))e.value=document.querySelector('#q').value;window.applied=true}})();</script>"""
        )
        page = DouyinPage(s, threading.Event())
        page.section = lambda *_: (_ for _ in ()).throw(
            AssertionError("不得退回AI标题定位")
        )
        assert page.batch_values(399, 1000) is True
        assert s.page.evaluate("window.applied")
        assert s.page.locator(".st").first.input_value() == "1000"


def test_batch_click_needs_no_sku_rows_or_result_validation(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<div><input placeholder="价格"><input placeholder="现货库存">
        <button onclick="window.clicked=true">批量设置</button></div>"""
        )
        page = DouyinPage(s, threading.Event())
        original = page.dom

        def without_rows(op, *args, **kwargs):
            assert op != "sku_rows", "Direct-stock toolbar must not require SKU rows"
            return original(op, *args, **kwargs)

        page.dom = without_rows
        assert page.batch_values(399, 1000) is True
        assert page.batch_applied
        assert s.page.evaluate("window.clicked")


def test_color_moves_using_own_header_and_confirms(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>header{display:flex;justify-content:space-between}section{height:120px}</style>
        <section id="sizes"><header><label>尺码大小</label><button onclick="throw Error('wrong move')">上移</button></header></section>
        <section id="colors"><header><label>颜色分类</label><button id="up" onclick="document.querySelector('[role=dialog]').hidden=false">上移</button></header></section>
        <div role="dialog" hidden><button onclick="document.querySelector('#sizes').before(document.querySelector('#colors'));document.querySelector('#up').disabled=true;this.parentElement.hidden=true;window.confirmed=true">确定</button></div>"""
        )
        page = DouyinPage(s, threading.Event())
        assert not page.dom("color_spec_order")["first"]
        page.ensure_color_first()
        assert s.page.evaluate("window.confirmed")
        assert page.dom("color_spec_order")["first"]
        page.ensure_color_first()


def test_final_verification_ignores_sku_table_after_batch_click(session):
    from types import SimpleNamespace
    from suite.listing.vision import Analysis

    with session as s:
        s.check_login()
        s.page.evaluate("editor()")
        page = DouyinPage(s, threading.Event())
        sku = page.fill_sku("CURRENT-SKU")
        page.ship_48h()
        page.batch_applied = True
        original = page.dom

        def without_table(op, *args, **kwargs):
            assert op not in {
                "sku_rows",
                "batch_price_stock",
            }, "最终核验不得再读取价格库存表"
            return original(op, *args, **kwargs)

        page.dom = without_table
        page.verify(
            SimpleNamespace(sku="CURRENT-SKU"), Analysis({}, [], []), sku, 399, 1000
        )


def test_batch_timeout_after_delivered_click_does_not_apply_twice(session):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    with session as s:
        s.check_login()
        s.page.set_content(
            """<div><input placeholder="价格"><input placeholder="现货库存">
        <button onclick="window.batchCalls=(window.batchCalls||0)+1">批量设置</button></div>"""
        )
        page = DouyinPage(s, threading.Event())

        def delivered_then_timeout(target):
            target.click()
            raise BrowserTimeout(
                "performing click action: timeout waiting for acknowledgement"
            )

        page.click_field = delivered_then_timeout
        assert page.batch_values(399, 1000)
        assert s.page.evaluate("window.batchCalls") == 1
        assert page.batch_applied
        assert s.page.evaluate("Object.keys(window.__glBatchClicks).length") == 0


def test_animated_batch_button_can_use_guarded_click_once(session):
    with session as s:
        s.check_login()
        s.page.set_content(
            """<style>@keyframes drift{from{transform:translateX(0)}to{transform:translateX(12px)}}button{animation:drift .16s infinite alternate linear}</style>
        <div><input placeholder="价格"><input placeholder="现货库存"><button onclick="window.batchCalls=(window.batchCalls||0)+1">批量设置</button></div>"""
        )
        page = DouyinPage(s, threading.Event())
        assert page.batch_values(399, 1000)
        assert s.page.evaluate("window.batchCalls") == 1
