"""Run existing synchronous page workflows over one asynchronous CDP connection.

Playwright objects live on one event loop. Task threads wait only for their own
commands, so uploads/navigation in separate tabs can progress together.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
from urllib.parse import urlparse

from .browser import EdgeSession
from .models import ListingError


class BrowserRuntime:
    def __init__(self, config, cancel, connect=None):
        self.config, self.cancel, self.connect = config, cancel, connect
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.cache = {}

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, function):
        async def invoke():
            value = function()
            if inspect.isawaitable(value):
                value = await value
            return self.wrap(value)
        if threading.current_thread() is self.thread:
            return self.loop.create_task(invoke())
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result()

    def wrap(self, value, seen=None):
        seen = {} if seen is None else seen
        if id(value) in seen:
            return seen[id(value)]
        if isinstance(value, (list, dict)):
            result = [] if isinstance(value, list) else {}
            seen[id(value)] = result
            if isinstance(value, list):
                result.extend(self.wrap(v, seen) for v in value)
            else:
                result.update((k, self.wrap(v, seen)) for k, v in value.items())
            return result
        if isinstance(value, tuple):
            return tuple(self.wrap(v, seen) for v in value)
        if type(value).__module__.startswith('playwright.'):
            key = id(value)
            if key not in self.cache:
                self.cache[key] = Proxy(self, value)
            return self.cache[key]
        return value

    def unwrap(self, value):
        if isinstance(value, Proxy):
            return value.raw
        if isinstance(value, (list, tuple)):
            return type(value)(self.unwrap(v) for v in value)
        if isinstance(value, dict):
            return {k: self.unwrap(v) for k, v in value.items()}
        return value

    def __enter__(self):
        endpoint = urlparse(self.config.endpoint)
        if not self.connect and (
            endpoint.scheme != "ws" or endpoint.hostname != "127.0.0.1"
            or not endpoint.path.startswith("/cdp/")
        ):
            raise ListingError("请先点击“连接 Edge”，在当前 Edge 中加载连接扩展。")
        self.thread.start()
        async def start():
            if self.connect:
                self.driver, self.browser = await self.connect()
            else:
                from playwright.async_api import async_playwright
                self.driver = await async_playwright().start()
                self.browser = await self.driver.chromium.connect_over_cdp(
                    self.config.endpoint, timeout=15000
                )
            self.context = self.browser.contexts[0]
            self.context.set_default_timeout(12000)
        try:
            self.call(start)
        except Exception:
            self.__exit__()
            raise
        return self

    def session(self):
        return TaskSession(self)

    def __exit__(self, *_):
        try:
            if getattr(self, 'driver', None):
                self.call(self.driver.stop)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self.loop.close()


class Proxy:
    def __init__(self, runtime, raw):
        self.runtime, self.raw = runtime, raw
        self.callbacks = {}

    def __getattr__(self, name):
        runtime = self.runtime
        # Resolve properties on the owning loop, as well as method calls.
        async def read():
            value = getattr(self.raw, name)
            if callable(value):
                return None, True
            if inspect.isawaitable(value):
                value = await value
            return value, False
        if threading.current_thread() is runtime.thread:
            value = getattr(self.raw, name)
            if not callable(value):
                return runtime.wrap(value)
        else:
            value, method = runtime.call(read)
            if not method:
                return value
        def invoke(*args, **kwargs):
            return runtime.call(lambda: getattr(self.raw, name)(
                *runtime.unwrap(args), **runtime.unwrap(kwargs)
            ))
        return invoke

    def on(self, event, callback):
        def wrapped(*args):
            callback(*self.runtime.wrap(args))
        self.callbacks[(event, callback)] = wrapped
        self.runtime.call(lambda: self.raw.on(event, wrapped))

    def remove_listener(self, event, callback):
        wrapped = self.callbacks.pop((event, callback), None)
        if wrapped:
            self.runtime.call(lambda: self.raw.remove_listener(event, wrapped))

    def __enter__(self):
        return self.runtime.call(self.raw.__aenter__)

    def __exit__(self, *args):
        return self.runtime.call(lambda: self.raw.__aexit__(*args))


class TaskContext:
    """Expose only this task's root tab and its popup descendants."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.owned = set()
        self.listeners = {}

    async def _pages(self):
        pages = self.runtime.context.pages
        for page in pages:
            current, seen = page, set()
            while current is not None and current not in seen:
                if current in self.owned:
                    self.owned.add(page)
                    break
                seen.add(current)
                current = await current.opener()
        return [p for p in pages if p in self.owned]

    @property
    def pages(self):
        return self.runtime.call(self._pages)

    def new_page(self):
        async def create():
            page = await self.runtime.context.new_page()
            self.owned.add(page)
            return page
        return self.runtime.call(create)

    def on(self, event, callback):
        if event != 'page':
            raise ListingError('任务上下文只支持工作页事件')
        async def dispatch(page):
            if page in await self._pages():
                callback(self.runtime.wrap(page))
        self.listeners[(event, callback)] = dispatch
        self.runtime.call(lambda: self.runtime.context.on(event, dispatch))

    def remove_listener(self, event, callback):
        dispatch = self.listeners.pop((event, callback), None)
        if dispatch:
            self.runtime.call(lambda: self.runtime.context.remove_listener(event, dispatch))


class TaskSession(EdgeSession):
    def __init__(self, runtime):
        super().__init__(runtime.config, runtime.cancel)
        self.runtime = runtime
        self.context = TaskContext(runtime)

    def __enter__(self):
        return self

    def reconnect_current_page(self):
        # The connection belongs to every running task. Never stop it to recover
        # one tab, and never select another editor just because its URL matches.
        self.page.evaluate('document.readyState')
        return self.page

    def close(self):
        pass  # Runtime disconnects the shared driver after all task threads end.
