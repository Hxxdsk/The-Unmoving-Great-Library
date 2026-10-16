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
