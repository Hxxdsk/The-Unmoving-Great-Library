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
        assert page.batch_values(399, 1000) == 2
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
        assert page.batch_values(399, 1000) == 2
        assert s.page.evaluate("window.applied")
        assert s.page.locator(".st").first.input_value() == "1000"
