from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .models import ListingError

ENTRY_URL = "https://fxg.jinritemai.com/ffa/g/list?sov_goodsType=0&tab=all"


def edge_data_path():
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft"
            / "Edge"
            / "User Data"
        )
    return Path.home() / ".config" / "microsoft-edge"


def edge_profiles(directory):
    directory = Path(directory)
    names, preferred = {}, "Default"
    try:
        info = json.loads((directory / "Local State").read_text(encoding="utf-8")).get(
            "profile", {}
        )
        names = {
            key: value.get("name", key)
            for key, value in info.get("info_cache", {}).items()
        }
        preferred = info.get("last_used", preferred)
    except (ValueError, OSError, AttributeError):
        pass
    profiles = (
        [
            p.name
            for p in directory.iterdir()
            if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))
        ]
        if directory.is_dir()
        else []
    )
    return [
        (key, names.get(key, key)) for key in sorted(set(profiles) | set(names))
    ], preferred


@dataclass
class BrowserConfig:
    user_data: str = ""
    profile: str = "Default"
    executable: str = ""
    endpoint: str = ""


class EdgeSession:
    def __init__(self, config, cancel):
        self.config, self.cancel = config, cancel
        self.playwright = self.context = self.browser = self.page = None
        self.owned = False
        self.owned_pages = set()

    def own_page(self, page):
        self.owned_pages.add(page)

    def new_work_page(self):
        for page in list(self.owned_pages):
            if not page.is_closed():
                page.close()
        self.owned_pages.clear()
        if self.browser and not self.owned:
            connection = self.browser.new_browser_cdp_session()
            try:
                with self.context.expect_page(timeout=10000) as created:
                    connection.send(
                        "Target.createTarget",
                        {"url": "about:blank", "background": True},
                    )
                self.page = created.value
            finally:
                connection.detach()
        else:
            self.page = self.context.new_page()
        self.own_page(self.page)
        return self.page

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        try:
            if self.config.endpoint:
                parsed = urlparse(self.config.endpoint)
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in ("127.0.0.1", "localhost")
                    or not parsed.port
                ):
                    raise ListingError("调试地址必须是本机 http://127.0.0.1:端口。")
                self.browser = self.playwright.chromium.connect_over_cdp(
                    self.config.endpoint, timeout=15000
                )
                if not self.browser.contexts:
                    raise ListingError("Edge 没有可复用的用户上下文。")
                self.context = self.browser.contexts[0]
            else:
                directory = Path(self.config.user_data).expanduser().resolve()
                if (
                    not directory.is_dir()
                    or not (directory / self.config.profile).is_dir()
                ):
                    raise ListingError(
                        "没有找到所选 Edge 用户资料，请选择实际登录抖店的资料目录。"
                    )
                if (
                    "/" in self.config.profile
                    or "\\" in self.config.profile
                    or self.config.profile in (".", "..")
                ):
                    raise ListingError("Edge 配置名称无效。")
                options = dict(
                    user_data_dir=str(directory),
                    headless=True,
                    timeout=30000,
                    viewport={"width": 1600, "height": 1000},
                    accept_downloads=False,
                    args=[
                        "--profile-directory=" + self.config.profile,
                        "--disable-session-crashed-bubble",
                    ],
                )
                if self.config.executable:
                    options["executable_path"] = self.config.executable
                else:
                    options["channel"] = "msedge"
                try:
                    self.context = self.playwright.chromium.launch_persistent_context(
                        **options
                    )
                except Exception:
                    raise ListingError(
                        "无法以后台模式使用 Edge 资料。请先完全退出 Edge（包括后台进程）后重试；也可连接已启用本机调试的 Edge。不会自动新建空白登录资料或强行关闭浏览器。"
                    ) from None
                self.owned = True
            self.context.set_default_timeout(12000)
            self.new_work_page()
            return self
        except Exception:
            self.close()
            raise

    def goto(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "fxg.jinritemai.com":
            raise ListingError("只允许进入抖店商家后台页面。")
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)

    def check_login(self):
        self.goto(ENTRY_URL)
        try:
            self.page.get_by_text("新建商品", exact=True).first.wait_for(
                state="visible", timeout=20000
            )
        except Exception:
            raise ListingError(
                "未检测到已登录的商品列表。请在所选 Edge 资料中登录抖店、完成验证码后退出 Edge，再重试；程序不会代过验证。"
            ) from None
        return "已读取现有 Edge 登录状态"

    def close(self):
        try:
            for page in self.owned_pages:
                if not page.is_closed():
                    page.close()
            if self.owned and self.context:
                self.context.close()
        finally:
            if self.playwright:
                self.playwright.stop()

    def __exit__(self, *_):
        self.close()
