import shutil
import threading

import pytest

from suite.listing.browser import BrowserConfig, EdgeSession
from suite.listing.models import Journal, scan_products
from suite.listing.parallel import BrowserRuntime
from suite.listing.runner import BatchRunner
from test_listing import Client, FIXTURE, make_product, short_fixture_waits


async def connect_fixture():
    from playwright.async_api import async_playwright
    executable = shutil.which('chromium') or shutil.which('msedge')
    if not executable:
        pytest.skip('本地浏览器测试需要 Chromium / Edge')
    driver = await async_playwright().start()
    browser = await driver.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
    context = await browser.new_context()
    async def keep_active(page):
        cdp = await context.new_cdp_session(page)
        await cdp.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
        await cdp.send('Page.setWebLifecycleState', {'state': 'active'})
    context.on('page', keep_active)
    await context.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=FIXTURE.read_text()))
    return driver, browser


def test_new_task_preserves_previous_page_and_url():
    class Page:
        closed = False
        url = 'https://fxg.jinritemai.com/edit/old'
        def close(self):
            self.closed = True
    old, new = Page(), Page()
    class Context:
        pages = [old]
        def new_page(self):
            self.pages.append(new)
            return new
    session = EdgeSession(BrowserConfig(), threading.Event())
    session.context = Context()
    assert session.next_work_page() is new
    assert not old.closed
    assert old.url.endswith('/old')


def test_parallel_pages_popups_chooser_and_waits_are_isolated():
    from concurrent.futures import ThreadPoolExecutor
    with BrowserRuntime(BrowserConfig(), threading.Event(), connect_fixture) as runtime:
        first, second = runtime.session(), runtime.session()
        first.next_work_page()
        second.next_work_page()
        first.goto('https://fxg.jinritemai.com/a')
        second.goto('https://fxg.jinritemai.com/b')
        popups = []
        first.context.on('page', popups.append)
        second.page.evaluate("window.open('https://fxg.jinritemai.com/child')")
        second.page.wait_for_timeout(100)
        assert len(second.context.pages) == 2
        assert first.context.pages == [first.page]
        assert not popups
        first.page.set_content('<input type=file>')
        with first.page.expect_file_chooser() as chooser:
            first.page.locator('input').click()
        assert chooser.value.is_multiple() is False
        chooser.value.set_files([])
        first.page.set_content('<button onclick="confirm(\'ok\');window.done=true">go</button>')
        def accept(dialog):
            dialog.accept()
        first.page.on('dialog', accept)
        first.page.locator('button').click()
        assert first.page.evaluate('window.done')
        first.page.remove_listener('dialog', accept)
        # Both browser waits must be outstanding together, not queued behind one another.
        barrier = threading.Barrier(2)
        def wait_page(session):
            barrier.wait(timeout=3)
            session.page.evaluate('window.ready=false;setTimeout(()=>window.ready=true,300)')
            session.page.wait_for_function('window.ready')
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(wait_page, [first, second]))


@pytest.mark.parametrize('all_parallel', [False, True])
def test_parallel_complete_workflows_keep_both_editors(tmp_path, monkeypatch, all_parallel):
    make_product(tmp_path, 'A')
    make_product(tmp_path, 'B')
    products = scan_products(tmp_path)
    for product in products:
        product.title = '用户输入的商品标题'
    def factory(config, cancel):
        runtime = BrowserRuntime(config, cancel, connect_fixture)
        return runtime
    monkeypatch.setattr('suite.listing.parallel.BrowserRuntime', factory)
    # Inspect the surviving tabs before stopping the fixture driver.
    inspected = []
    original = BrowserRuntime.__exit__
    def finish(self, *args):
        async def inspect():
            for page in self.context.pages:
                if await page.locator('#sku').count():
                    inspected.append((await page.locator('#sku').input_value(), await page.evaluate('audit.filter(x=>x.event==="save").length')))
        self.call(inspect)
        original(self, *args)
    monkeypatch.setattr(BrowserRuntime, '__exit__', finish)
    logs = []
    result = BatchRunner(tmp_path, products, BrowserConfig(), Client(), threading.Event(), log=logs.append, concurrency=2 if not all_parallel else 1, all_parallel=all_parallel).run()
    assert result == [('A', '已保存'), ('B', '已保存')], logs
    assert sorted(inspected) == [('A', 1), ('B', 1)]
    assert all(Journal(tmp_path).get(p)['status'] == 'saved' for p in products)


@pytest.mark.parametrize('all_parallel,stopped', [(False, False), (True, False), (False, True)])
def test_scheduler_limits_refills_and_stops_queue(tmp_path, monkeypatch, all_parallel, stopped):
    import contextlib
    import time
    from types import SimpleNamespace

    products = [SimpleNamespace(sku=str(i), issues=[]) for i in range(5)]
    cancel = threading.Event()
    width = 5 if all_parallel else 2
    barrier = threading.Barrier(width)
    lock = threading.Lock()
    active = peak = 0
    started = []
    class Session:
        def next_work_page(self):
            pass
        def check_login(self):
            return 'fixture-login'
    class Runtime:
        def __init__(self, *_):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def session(self):
            return contextlib.nullcontext(Session())
    def process(self, session, journal, tasks):
        nonlocal active, peak
        product = tasks[0]
        with lock:
            started.append(product.sku)
            active += 1
            peak = max(peak, active)
        if int(product.sku) < width:
            barrier.wait(timeout=5)
        if stopped:
            cancel.set()
        time.sleep(.01)
        with lock:
            active -= 1
        return [(product.sku, '已保存')]
    monkeypatch.setattr('suite.listing.parallel.BrowserRuntime', Runtime)
    monkeypatch.setattr(BatchRunner, '_run_products', process)
    result = BatchRunner(tmp_path, products, BrowserConfig(), Client(), cancel, concurrency=2, all_parallel=all_parallel).run()
    assert peak == width
    assert sorted(started) == [str(i) for i in range(width if stopped else 5)]
    assert len(result) == len(started)


def test_parallel_controls_persist_and_follow_busy_state(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QStandardPaths
    from suite.listing.ui import ListingWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda *_: str(tmp_path))
    first = ListingWindow()
    try:
        assert first.concurrency.value() == 1 and not first.all_parallel.isChecked()
        first.concurrency.setValue(3)
        first.all_parallel.setChecked(True)
        assert not first.concurrency.isEnabled()
        first.save_preferences()
        first.busy(True)
        assert not first.all_parallel.isEnabled()
        first.busy(False)
        assert first.all_parallel.isEnabled() and not first.concurrency.isEnabled()
    finally:
        first.close()
    second = ListingWindow()
    try:
        assert second.concurrency.value() == 3 and second.all_parallel.isChecked()
        second.all_parallel.setChecked(False)
        assert second.concurrency.isEnabled()
    finally:
        second.close()


def test_parallel_through_extension_relay_preserves_drafts(tmp_path, monkeypatch):
    """Real CDP + original extension JS, with a Node Chrome-API stand-in."""
    import json
    import subprocess
    import time
    from playwright.async_api import async_playwright
    from suite.listing.bridge import EdgeBridge, EXTENSION_DIR
    from websockets.sync.client import connect

    executable, node = shutil.which('chromium'), shutil.which('node')
    if not executable or not node:
        pytest.skip('扩展协议模拟需要 Chromium 和 Node')
    bridge = EdgeBridge(tmp_path / 'pairing.json')
    config = bridge.start()
    profile = tmp_path / 'browser'
    profile.mkdir()
    browser = subprocess.Popen([executable, '--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--no-first-run', '--remote-debugging-port=0', '--user-data-dir=' + str(profile), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    host = None
    try:
        deadline = time.monotonic() + 15
        while not (profile / 'DevToolsActivePort').exists():
            assert browser.poll() is None and time.monotonic() < deadline
            time.sleep(.1)
        port, path = (profile / 'DevToolsActivePort').read_text().splitlines()[:2]
        endpoint = f'ws://127.0.0.1:{port}{path}'
        host = subprocess.Popen([node, str(FIXTURE.with_name('listing_extension_host.cjs')), str(EXTENSION_DIR / 'background.js'), endpoint, str(bridge.port), bridge.token], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        assert bridge.connected.wait(10)
        body = FIXTURE.read_text() + """<script>
        if(location.pathname.includes('/g/create'))wizard();
        else {const entry=document.querySelector('#menu button');
          if(entry)entry.onclick=()=>window.open('https://fxg.jinritemai.com/ffa/g/create','_blank').focus();}
        </script>"""
        async def connect_relay():
            driver = await async_playwright().start()
            connected = await driver.chromium.connect_over_cdp(config.endpoint, timeout=15000)
            await connected.contexts[0].route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=body))
            return driver, connected
        runtime = BrowserRuntime(config, threading.Event(), connect_relay)
        inspected, page_urls, saved_records = [], [], []
        original_exit = BrowserRuntime.__exit__
        def finish(self, *args):
            async def inspect():
                for page in self.context.pages:
                    if await page.locator('#sku').count():
                        inspected.append((await page.locator('#sku').input_value(), await page.evaluate('audit.filter(x=>x.event==="save").length')))
                        page_urls.append(page.url)
                        saved_records.extend(await page.evaluate('audit.filter(x=>x.event==="save")'))
            self.call(inspect)
            original_exit(self, *args)
        monkeypatch.setattr(BrowserRuntime, '__exit__', finish)
        monkeypatch.setattr('suite.listing.parallel.BrowserRuntime', lambda *_: runtime)
        root = tmp_path / 'products'
        make_product(root, 'A')
        make_product(root, 'B')
        for path in root.rglob('*.png'):
            if path.stem != '尺码':
                path.rename(path.with_name(path.parent.parent.name + '-' + path.name))
        products = scan_products(root)
        for product in products:
            product.title = '用户输入的商品标题'
        logs = []
        result = BatchRunner(root, products, config, Client(), runtime.cancel, concurrency=2, log=logs.append).run()
        assert result == [('A', '已保存'), ('B', '已保存')], logs
        assert sorted(inspected) == [('A', 1), ('B', 1)], logs
        assert all(r['details'] == [r['sku'] + '-1.png', r['sku'] + '-2.png'] for r in saved_records)
        assert not any('跳过图片字段' in message for message in logs), logs
        assert browser.poll() is None and bridge.connected.is_set()
        with connect(endpoint, proxy=None) as socket:
            socket.send(json.dumps({'id': 1, 'method': 'Target.getTargets'}))
            while True:
                reply = json.loads(socket.recv(timeout=5))
                if reply.get('id') == 1:
                    targets = reply['result']['targetInfos']
                    break
        assert any(t['url'] == 'about:blank#ordinary-user-tab' for t in targets)
        assert sum(t['url'] in page_urls for t in targets) == 2
    finally:
        bridge.close()
        if host:
            host.terminate()
            host.wait(5)
            host.stderr.close()
        browser.terminate()
        browser.wait(10)
