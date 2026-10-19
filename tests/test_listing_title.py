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


def test_recommendation_open_timeout_keeps_visible_dropdown_and_selects_first(session):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    with session as s:
        s.check_login()
        s.page.evaluate("editor()")
        page = DouyinPage(s, threading.Event())
        original = page.click_field
        calls = []

        def click(target):
            if target.inner_text().strip() == "AI 推荐":
                calls.append(True)
                target.click()
                raise BrowserTimeout("Timeout after scrolling and delivering click")
            original(target)

        page.click_field = click
        page.select_recommended_title()
        assert calls == [True]
        assert (
            s.page.locator("#final-title").input_value()
            == "网站推荐的第一个完整商品标题"
        )
        assert s.page.evaluate("Object.keys(window.__glTitleClicks).length") == 0
        assert not any(e["event"] == "save" for e in s.page.evaluate("audit"))


@pytest.mark.parametrize("delay", [30, 350, 900])
def test_delivered_open_click_waits_for_delayed_list_without_toggling(session, delay):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    with session as s:
        s.check_login()
        s.page.evaluate(
            """editor();const b=[...document.querySelectorAll('button')].find(e=>e.textContent==='AI 推荐');
          const original=b.onclick;b.onclick=()=>{window.openCalls=(window.openCalls||0)+1;setTimeout(()=>original.call(b),window.titleDelay)};
          document.addEventListener('click',e=>{(window.clicks??=[]).push(e.target.outerHTML)},true);"""
        )
        s.page.evaluate("delay=>window.titleDelay=delay", delay)
        page = DouyinPage(s, threading.Event())
        original = page.click_field

        def click(target):
            if target.inner_text().strip() == "AI 推荐":
                target.click()
                raise BrowserTimeout("Click acknowledgement timed out")
            original(target)

        page.click_field = click
        page.select_recommended_title()
        assert s.page.evaluate("window.openCalls") == 1, s.page.evaluate(
            "window.clicks"
        )
        assert (
            s.page.locator("#final-title").input_value()
            == "网站推荐的第一个完整商品标题"
        )


def test_already_open_list_is_used_without_clicking_recommend_again(session):
    with session as s:
        s.check_login()
        s.page.evaluate("editor();titleRecommendations()")
        page = DouyinPage(s, threading.Event())
        original = page.click_field

        def click(target):
            assert target.inner_text().strip() != "AI 推荐", "已展开时不得再次点击开关"
            original(target)

        page.click_field = click
        page.select_recommended_title()
        assert (
            s.page.locator("#final-title").input_value()
            == "网站推荐的第一个完整商品标题"
        )


def test_animated_recommend_button_uses_fresh_guarded_click(session):
    with session as s:
        s.check_login()
        s.page.evaluate("editor()")
        s.page.add_style_tag(
            content="""@keyframes driftRecommend{from{transform:translateX(0)}to{transform:translateX(12px)}}
        #editor-title button {animation:driftRecommend .16s infinite alternate linear}"""
        )
        messages = []
        page = DouyinPage(s, threading.Event(), log=messages.append)
        page.select_recommended_title()
        assert (
            s.page.locator("#final-title").input_value()
            == "网站推荐的第一个完整商品标题"
        )
        assert any("AI 推荐按钮正在重绘" in text for text in messages)


def test_title_applied_is_confirmed_even_when_recommendation_popup_remains(session):
    with session as s:
        s.check_login()
        s.page.evaluate("""editor();titleRecommendations=()=>{
          const popup=document.createElement('div');popup.className='title-popover';
          popup.innerHTML='<div id="recommended-first">Dickies 男士美式复古宽松阔腿休闲裤加绒加厚</div><button>AI 重新生成</button>';
          popup.firstChild.onclick=()=>{document.querySelector('#final-title').value='男士美式复古宽松阔腿休闲裤加绒加厚'};
          document.querySelector('#editor-title').append(popup);
        }""")
        page = DouyinPage(s, threading.Event())
        page.select_recommended_title()
        assert (
            s.page.locator("#final-title").input_value()
            == "男士美式复古宽松阔腿休闲裤加绒加厚"
        )
        assert s.page.locator("#recommended-first").is_visible()


def test_changed_input_with_wrong_title_is_not_accepted(session):
    with session as s:
        s.check_login()
        s.page.evaluate("""editor();titleRecommendations=()=>{
          const popup=document.createElement('div');popup.className='title-popover';
          popup.innerHTML='<div>网站推荐的第一个完整商品标题</div><button>AI 重新生成</button>';
          popup.firstChild.onclick=()=>{document.querySelector('#final-title').value='错误的另一个标题';popup.remove()};
          document.querySelector('#editor-title').append(popup);
        }""")
        page = DouyinPage(s, threading.Event())
        with pytest.raises(ListingError, match="推荐标题尚未应用"):
            page.select_recommended_title()
