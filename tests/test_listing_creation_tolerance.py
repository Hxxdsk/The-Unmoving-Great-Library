import threading

import pytest

from test_listing import session, short_fixture_waits, make_product, FIXTURE
from suite.listing.site import DouyinPage
from suite.listing.models import ListingError


def fixture_route(s, extra):
    s.context.unroute("**/*")
    s.context.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body=FIXTURE.read_text() + extra
        ),
    )


def test_actual_upload_screen_wins_over_stale_recommendations(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>
        const oldWizard=wizard;wizard=()=>{oldWizard();
        [...app.querySelectorAll('button')].find(e=>e.textContent==='本地上传').innerHTML='<span>本地</span> <span>上传</span>';
        app.insertAdjacentHTML('beforeend','<div><label>AI推荐</label><button class="recommend">之前残留的推荐商品标题</button></div>');};
        list=()=>wizard();list();</script>""",
        )
        page = DouyinPage(s, threading.Event())
        page.create(product)
        assert "/edit" in page.editor_url
        assert (
            len(
                next(e for e in s.page.evaluate("audit") if e["event"] == "wizard")[
                    "files"
                ]
            )
            == 5
        )


def test_animating_hover_submenu_reopens_and_continues(tmp_path, session):
    product = make_product(tmp_path)
    logs = []
    with session as s:
        fixture_route(
            s,
            """<style>#menu button{animation:wobble .12s infinite alternate linear}
        @keyframes wobble{from{transform:translateX(-8px)}to{transform:translateX(8px)}}</style>""",
        )
        page = DouyinPage(s, threading.Event(), log=logs.append)
        page.create(product)
        assert "/edit" in page.editor_url
        assert not any("创建入口暂时不可点" in line for line in logs)
        assert any("当前可见单商品菜单" in line for line in logs)


def test_disabled_generation_waits_and_restores_title_after_rerender(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>
        const oldRecommend=recommend;recommend=()=>{oldRecommend();
        const button=[...app.querySelectorAll('button')].find(e=>/继续生成/.test(e.textContent));button.disabled=true;
        setTimeout(()=>{const old=document.querySelector('#user-title'),fresh=old.cloneNode();fresh.value='';old.replaceWith(fresh);
        fresh.addEventListener('blur',()=>{if(fresh.value)setTimeout(()=>button.disabled=false,250)});},400);
        }; </script>""",
        )
        page = DouyinPage(s, threading.Event())
        page.create(product)
        generation = next(
            e for e in s.page.evaluate("audit") if e["event"] == "generation"
        )
        assert generation["title"] == product.title


def test_persistently_disabled_generation_reports_validation_not_click_timeout(
    tmp_path, session
):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>const oldRecommend=recommend;recommend=()=>{oldRecommend();
        [...app.querySelectorAll('button')].find(e=>/继续生成/.test(e.textContent)).disabled=true;
        app.insertAdjacentHTML('beforeend','<div role="alert">商品标题超过当前类目限制</div>');};</script>""",
        )
        page = DouyinPage(s, threading.Event())
        with pytest.raises(ListingError, match="商品标题超过当前类目限制"):
            page.create(product)
        assert not any(e["event"] == "generation" for e in s.page.evaluate("audit"))


def test_nested_nonbutton_local_upload_is_recognized(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>const oldWizard=wizard;wizard=()=>{oldWizard();
        const button=[...app.querySelectorAll('button')].find(e=>e.textContent==='本地上传');
        const div=document.createElement('div');div.innerHTML='<div><span>本地上传</span></div>';
        div.onclick=()=>document.querySelector('#wizard').click();button.replaceWith(div);};list=()=>wizard();list();</script>""",
        )
        page = DouyinPage(s, threading.Event())
        page.create(product)
        assert "/edit" in page.editor_url
        assert any(e["event"] == "wizard" for e in s.page.evaluate("audit"))


def test_upload_uses_current_file_input_and_ignores_unrelated_spinner(
    tmp_path, session
):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>
        const oldWizard=wizard;wizard=()=>{oldWizard();
          const local=[...app.querySelectorAll('button')].find(e=>e.textContent==='本地上传');
          local.onclick=()=>{throw Error('文件选择按钮不可用，应该直接上传当前文件控件')};
          const file=document.querySelector('#wizard'),next=[...app.querySelectorAll('button')].find(e=>/下一步/.test(e.textContent));
          next.disabled=true;const original=file.onchange;
          file.onchange=()=>{original.call(file);file.remove();local.remove();setTimeout(()=>next.disabled=false,150)};
          document.body.insertAdjacentHTML('beforeend','<aside aria-busy="true">无关助手加载中</aside>');
        };list=()=>wizard();list();</script>""",
        )
        page = DouyinPage(s, threading.Event())
        page.create(product)
        assert "/edit" in page.editor_url
        uploads = [e for e in s.page.evaluate("audit") if e["event"] == "wizard"]
        assert len(uploads) == 1 and len(uploads[0]["files"]) == 5


def test_wait_reobserves_after_transient_react_context_error(session):
    from playwright.sync_api import Error as BrowserError

    with session as s:
        s.check_login()
        page = DouyinPage(s, threading.Event())
        observations = []

        def condition():
            observations.append(1)
            if len(observations) < 3:
                raise BrowserError("Execution context was destroyed during navigation")
            return True

        page.wait(condition, "页面仍在加载", timeout=3)
        assert len(observations) == 3


def test_pending_generation_polls_result_without_replaying_creation_memory(session):
    with session as s:
        s.check_login()
        page = DouyinPage(s, threading.Event())
        page.generation_pending = True

        class ForbiddenMemory:
            def recipes(self, *args):
                raise AssertionError("已提交生成时不能重放创建动作")

        page.memory = ForbiddenMemory()
        observed = []

        def ready():
            observed.append(1)
            return len(observed) >= 3

        page.navigation_wait(ready, "等待生成结果", timeout=3)
        assert len(observed) == 3
        assert not page.generation_pending


def test_color_classification_waits_until_after_creation(tmp_path, session):
    from contextlib import nullcontext
    from suite.listing.runner import BatchRunner
    from suite.listing.browser import BrowserConfig
    from test_listing import Client

    product = make_product(tmp_path)
    with session as s:

        class EditorOnlyClient(Client):
            def generate(self, parts, schema):
                if "reference_ids" in schema.get("properties", {}):
                    assert s.page.locator("#sku").input_value() == product.sku
                    assert any(
                        e["event"] == "generation" for e in s.page.evaluate("audit")
                    )
                return super().generate(parts, schema)

        results = BatchRunner(
            tmp_path,
            [product],
            BrowserConfig(),
            EditorOnlyClient(),
            threading.Event(),
            session_factory=lambda *_: nullcontext(s),
        ).run()
        assert results == [(product.sku, "已保存")]


def test_title_blur_can_advance_without_waiting_for_old_title_input(tmp_path, session):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>
        const oldRecommend=recommend;recommend=()=>{oldRecommend();
        document.querySelector('#user-title').onblur=()=>editor();};</script>""",
        )
        page = DouyinPage(s, threading.Event())
        page.create(product)
        assert "/edit" in page.editor_url
        generation = [e for e in s.page.evaluate("audit") if e["event"] == "generation"]
        assert len(generation) == 1 and generation[0]["title"] == product.title


def test_hover_trigger_remount_is_reentered_without_ai_or_next_detection_round(
    tmp_path, session
):
    product = make_product(tmp_path)
    with session as s:
        fixture_route(
            s,
            """<script>
        window.menuRemounts=0;
        const originalList=list;list=()=>{originalList();
          const root=document.querySelector('#new-product');
          root.querySelector('button').addEventListener('mouseenter',()=>{
            if(window.menuRemounts++)return;
            const fresh=root.cloneNode(true);fresh.querySelector('#menu').style.display='none';fresh.style.transform='translateX(40px)';root.replaceWith(fresh);
            fresh.querySelector('button').addEventListener('mouseenter',()=>fresh.querySelector('#menu').style.display='block');
          });
        };list();</script>""",
        )

        class NoAI:
            def generate(self, *args, **kwargs):
                raise AssertionError("菜单重绘无需交给 AI")

        page = DouyinPage(s, threading.Event(), client=NoAI())
        page.create(product)
        assert "/edit" in page.editor_url
        assert s.page.evaluate("window.menuRemounts") == 1


def test_click_timeout_after_dispatch_waits_for_each_request_once(
    tmp_path, session, monkeypatch
):
    from playwright.sync_api import Locator, TimeoutError as ClickTimeout

    original = Locator.click
    with session as s:
        fixture_route(
            s,
            """<script>
        const oldWizard=wizard;wizard=()=>{oldWizard();
          [...app.querySelectorAll('button')].find(e=>/下一步/.test(e.textContent)).onclick=()=>{
            audit.push({event:'next-request'});setTimeout(recommend,500);
          };
        };
        const oldRecommend=recommend;recommend=()=>{oldRecommend();
          [...app.querySelectorAll('button')].find(e=>/继续生成/.test(e.textContent)).onclick=()=>{
            audit.push({event:'generate-request'});setTimeout(editor,500);
          };
        };list();</script>""",
        )

        def timeout_after_dispatch(locator, *args, **kwargs):
            text = locator.inner_text()
            original(locator, *args, **kwargs)
            if "下一步" in text or "继续生成" in text:
                raise ClickTimeout(
                    "Timeout 1500ms exceeded after performing click action"
                )

        monkeypatch.setattr(Locator, "click", timeout_after_dispatch)
        page = DouyinPage(s, threading.Event())
        page.create(make_product(tmp_path))
        events = s.page.evaluate("audit")
        assert sum(e["event"] == "next-request" for e in events) == 1
        assert sum(e["event"] == "generate-request" for e in events) == 1
        assert "/edit" in page.editor_url


def test_more_than_three_generation_rerenders_retry_within_deadline(
    tmp_path, session, monkeypatch
):
    from playwright.sync_api import Locator, TimeoutError as ClickTimeout

    original = Locator.click
    attempts = []

    def rerender(locator, *args, **kwargs):
        if locator.inner_text().startswith("下一步") and len(attempts) < 4:
            attempts.append(1)
            locator.evaluate(
                """e=>{const fresh=e.cloneNode(true);fresh.disabled=true;e.replaceWith(fresh);setTimeout(()=>fresh.disabled=false,180)}"""
            )
            raise ClickTimeout("Timeout 1500ms exceeded: button replaced during click")
        return original(locator, *args, **kwargs)

    monkeypatch.setattr(Locator, "click", rerender)
    with session as s:
        page = DouyinPage(s, threading.Event())
        page.create(make_product(tmp_path))
        assert len(attempts) == 4
        assert "/edit" in page.editor_url


def test_generation_activation_never_clicks_a_covered_or_disabled_control(session):
    import re

    with session as s:
        s.check_login()
        s.page.set_content(
            """<button disabled onclick="window.clicks=(window.clicks||0)+1">下一步，生成商品</button>"""
        )
        page = DouyinPage(s, threading.Event())
        button = s.page.locator("button")
        pattern = re.compile("^下一步.*")
        assert page.activate_creation_control(button, pattern, "test") == "disabled"
        button.evaluate("e=>e.disabled=false")
        s.page.evaluate(
            """()=>{const overlay=document.createElement('div');overlay.style.cssText='position:fixed;inset:0;background:white;z-index:99';document.body.append(overlay)}"""
        )
        assert page.activate_creation_control(button, pattern, "test") == "covered"
        assert s.page.evaluate("window.clicks||0") == 0
        s.page.locator("div").last.evaluate("e=>e.remove()")
        button.evaluate("e=>e.style.pointerEvents='none'")
        assert page.activate_creation_control(button, pattern, "test") == "covered"
        assert s.page.evaluate("window.clicks||0") == 0


def test_portal_menu_is_selected_without_moving_pointer_off_its_trigger(
    tmp_path, session
):
    with session as s:
        fixture_route(
            s,
            """<script>
        list=()=>{
          app.innerHTML='<button id="create-menu">新建商品</button>';
          const trigger=document.querySelector('#create-menu');
          trigger.onmouseenter=()=>{
            if(document.querySelector('#single-portal'))return;
            const item=document.createElement('button');item.id='single-portal';item.textContent='单商品';
            item.style.cssText='position:fixed;top:150px;left:400px';
            item.onclick=()=>{item.remove();wizard()};document.body.appendChild(item);
          };
          trigger.onmouseleave=()=>document.querySelector('#single-portal')?.remove();
        };list();</script>""",
        )
        page = DouyinPage(s, threading.Event())
        page.create(make_product(tmp_path))
        assert "/edit" in page.editor_url


def test_submitted_menu_click_waits_for_delayed_navigation_without_ai(
    tmp_path, session
):
    with session as s:
        fixture_route(
            s,
            """<script>
        const oldList=list;list=()=>{oldList();
          document.querySelector('#menu button').onclick=()=>{audit.push({event:'menu-request'});setTimeout(wizard,900)};
        };list();</script>""",
        )

        class NoAI:
            def generate(self, *args, **kwargs):
                raise AssertionError("导航已发出，应等待网站切换页面")

        page = DouyinPage(s, threading.Event(), client=NoAI())
        page.create(make_product(tmp_path))
        assert "/edit" in page.editor_url
        assert sum(e["event"] == "menu-request" for e in s.page.evaluate("audit")) == 1
