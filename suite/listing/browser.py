from __future__ import annotations

from urllib.parse import urlparse
import re

from .bridge import BrowserConfig
from .models import ListingError

ENTRY_URL = "https://fxg.jinritemai.com/ffa/g/list?sov_goodsType=0&tab=all"


class EdgeSession:
    """Control extension-owned tabs inside the user's ordinary Edge session."""

    def __init__(self, config, cancel):
        self.config, self.cancel = config, cancel
        self.playwright = self.context = self.browser = self.page = None

    def own_page(self, page):
        self.page = page

    def next_work_page(self):
        old = list(self.context.pages)
        self.page = self.context.new_page()
        for page in old:
            if not page.is_closed():
                page.close()
        return self.page

    def new_work_page(self):
        if self.page is not None and not self.page.is_closed():
            return self.page
        pages = self.context.pages
        self.page = pages[-1] if pages else self.context.new_page()
        return self.page

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        endpoint = urlparse(self.config.endpoint)
        if (
            endpoint.scheme != "ws"
            or endpoint.hostname != "127.0.0.1"
            or not endpoint.path.startswith("/cdp/")
        ):
            raise ListingError("请先点击“连接 Edge”，在当前 Edge 中加载连接扩展。")
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(
                self.config.endpoint, timeout=15000
            )
            if not self.browser.contexts:
                raise ListingError("没有可用的抖店操作页，请重新连接 Edge。")
            self.context = self.browser.contexts[0]
            self.context.set_default_timeout(12000)
            self.new_work_page()
            return self
        except Exception as exc:
            self.close()
            detail = re.sub(r"[a-fA-F0-9]{64}", "[连接令牌已隐藏]", str(exc))
            detail = detail.replace(self.config.endpoint, "[本机连接地址]")[:700]
            raise ListingError(
                "Edge 操作页初始化失败："
                + detail
                + "。若扩展通道显示已连接，请在 edge://extensions 重新加载当前程序目录的连接扩展，再重新连接；无需关闭 Edge。"
            ) from exc

    def goto(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "fxg.jinritemai.com":
            raise ListingError("只允许进入抖店商家后台页面。")
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)

    def wait_product_list(self, timeout):
        self.page.wait_for_function(
            """() => [...document.querySelectorAll('button,[role=button],a,span,h1,h2,h3')].some(e => {
          const s=getComputedStyle(e), text=(e.innerText||'').replace(/\\s+/g,'').trim();
          return text.length<35 && /新建商品|创建商品|商品管理|商品列表/.test(text) && e.getClientRects().length && s.visibility!=='hidden' && s.display!=='none';
        })""",
            timeout=timeout,
        )

    def open_login(self):
        self.goto(ENTRY_URL)
        try:
            self.wait_product_list(5000)
        except Exception:
            return "已连接当前 Edge。请在工具新建的抖店页完成登录或验证，再开始任务。"
        return "已连接当前 Edge，沿用现有抖店登录；其他标签页可正常使用。"

    def check_login(self):
        self.goto(ENTRY_URL)
        try:
            self.wait_product_list(20000)
        except Exception:
            raise ListingError(
                "请在工具新建的抖店页完成登录或验证，再重试；无需关闭 Edge。"
            ) from None
        return "已连接 Edge 的专用操作页，复用当前登录。"

    def close(self):
        if self.playwright:
            self.playwright.stop()
            self.playwright = None

    def __exit__(self, *_):
        self.close()
