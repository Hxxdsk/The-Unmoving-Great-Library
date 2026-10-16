import threading

import pytest

from test_listing import session, short_fixture_waits
from suite.listing.models import ListingError
from suite.listing.site import DouyinPage


def test_recommended_title_uses_first_portal_option_after_returning_to_top(session):
    with session as s:
        s.check_login()
        s.page.set_content("""<button>图文信息</button>
        <aside><button onclick="window.wrong=true">AI 推荐</button></aside>
        <section style="margin-top:900px;width:850px"><label>商品标题</label>
        <span>Dickies</span><input id="title" style="width:600px" value="短标题">
        <span role="button" id="recommend" onclick="setTimeout(showTitles,30)">✦ AI 推荐</span></section>
        <button onclick="window.saved=true">保存草稿</button>
        <script>function showTitles(){
          const r=document.querySelector('#title').getBoundingClientRect();
          const p=document.createElement('div');p.setAttribute('role','listbox');
          p.style.cssText='position:absolute;left:'+r.left+'px;top:'+(r.bottom+scrollY)+'px;width:680px;background:white';
          p.innerHTML='<div role="option"><span>网站第一个完整商品标题</span></div><div role="option"><span>网站第二个商品标题</span></div><button>AI 重新生成</button>';
          for(const e of p.querySelectorAll('[role=option]'))e.onclick=()=>{document.querySelector('#title').value=e.innerText;window.chosen=e.innerText;p.remove()};document.body.append(p);
        }</script>""")
        s.page.evaluate("window.scrollTo(0,1000)")
        page = DouyinPage(s, threading.Event())
        page.select_recommended_title()
        assert s.page.locator("#title").input_value() == "网站第一个完整商品标题"
        assert s.page.evaluate("window.chosen") == "网站第一个完整商品标题"
        assert not s.page.evaluate("Boolean(window.wrong || window.saved)")
        assert s.page.locator("[role=listbox]").count() == 0


def test_no_recommendations_reports_failure_before_save(session):
    with session as s:
        s.check_login()
        s.page.set_content("""<button>图文信息</button><section><label>商品标题</label>
        <input value="短标题"><button>AI 推荐</button></section>
        <button onclick="window.saved=true">保存草稿</button>""")
        page = DouyinPage(s, threading.Event())
        with pytest.raises(ListingError, match="推荐未加载"):
            page.select_recommended_title()
        assert not s.page.evaluate("Boolean(window.saved)")


def test_animated_first_title_uses_guarded_fresh_click(session):
    with session as s:
        s.check_login()
        s.page.evaluate("editor()")
        s.page.add_style_tag(content="""
            @keyframes moveTitle {from{transform:translateX(0)}to{transform:translateX(12px)}}
            .title-popover>div:first-child {animation:moveTitle .16s linear infinite alternate}
        """)
        messages = []
        page = DouyinPage(s, threading.Event(), log=messages.append)
        page.select_recommended_title()
        assert (
            s.page.locator("#final-title").input_value()
            == "网站推荐的第一个完整商品标题"
        )
        assert any("重新定位第一项" in message for message in messages)
        assert [e["event"] for e in s.page.evaluate("window.audit")] == [
            "recommended-final-title"
        ]
