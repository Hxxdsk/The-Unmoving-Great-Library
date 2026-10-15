from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from .models import Cancelled, ListingError

ENTRY_URL = "https://fxg.jinritemai.com/ffa/g/list?sov_goodsType=0&tab=all"
# Store only connection/tab identifiers, never Playwright objects across worker threads.
_WINDOWS = {}
_START_LOCK = threading.Lock()
_LOCAL_HTTP = build_opener(ProxyHandler({}))


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


def edge_executable(configured=""):
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise ListingError("没有找到指定的 Edge 程序，请检查 msedge.exe 路径。")
    for name in ("msedge", "microsoft-edge", "microsoft-edge-stable"):
        path = shutil.which(name)
        if path:
            return path
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            path = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if path.is_file():
                return str(path)
    raise ListingError("没有找到 Microsoft Edge，请安装 Edge 或指定浏览器程序路径。")


def local_endpoint(value):
    try:
        parsed = urlparse(value)
        valid = (
            parsed.scheme in ("http", "ws")
            and parsed.hostname in ("127.0.0.1", "localhost")
            and parsed.port
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        valid = False
    if not valid:
        raise ListingError("调试地址必须是本机 http://127.0.0.1:端口。")
    return value


def profile_endpoint(directory):
    """Resolve Chromium's live port marker; reject stale markers/port reuse."""
    try:
        lines = (directory / "DevToolsActivePort").read_text().splitlines()
        port = int(lines[0])
        if not 0 < port < 65536 or not lines[1].startswith("/devtools/browser/"):
            return None
        endpoint = f"http://127.0.0.1:{port}"
        with _LOCAL_HTTP.open(endpoint + "/json/version", timeout=0.6) as response:
            info = json.load(response)
        websocket = local_endpoint(info["webSocketDebuggerUrl"])
        if urlparse(websocket).path != lines[1]:
            return None
        return websocket
    except (OSError, ValueError, IndexError, KeyError, TypeError, ListingError):
        return None


@dataclass
class BrowserConfig:
    user_data: str = ""
    profile: str = "Default"
    executable: str = ""
    endpoint: str = ""


class EdgeSession:
    """Attach to a visible persistent Edge; disconnect without closing its window."""

    def __init__(self, config, cancel):
        self.config, self.cancel = config, cancel
        self.playwright = self.context = self.browser = self.page = None
        self.owned_pages = set()
        self.key = None
        self.endpoint = None
        self.start_page = None

    def own_page(self, page):
        self.owned_pages.add(page)
        self.page = page
        connection = self.context.new_cdp_session(page)
        try:
            target = connection.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        finally:
            connection.detach()
        _WINDOWS[self.key] = (self.endpoint, target)

    def new_work_page(self):
        # Keep the same visible operation tab between products and worker jobs.
        if self.page is not None and not self.page.is_closed():
            return self.page
        target = _WINDOWS.get(self.key, (None, None))[1]
        for page in self.context.pages:
            connection = self.context.new_cdp_session(page)
            try:
                info = connection.send("Target.getTargetInfo")["targetInfo"]
            finally:
                connection.detach()
            if info["targetId"] == target or (
                self.start_page and page.url == self.start_page
            ):
                self.own_page(page)
                page.bring_to_front()
                return page
        connection = self.browser.new_browser_cdp_session()
        try:
            with self.context.expect_page(timeout=10000) as created:
                connection.send(
                    "Target.createTarget",
                    {"url": "about:blank", "newWindow": True, "background": False},
                )
            self.page = created.value
        finally:
            connection.detach()
        self.own_page(self.page)
        self.page.bring_to_front()
        return self.page

    def _connect(self, endpoint):
        self.endpoint = local_endpoint(endpoint)
        self.browser = self.playwright.chromium.connect_over_cdp(
            self.endpoint, timeout=15000
        )
        if not self.browser.contexts:
            raise ListingError("Edge 没有可复用的用户上下文。")
        # The default persistent context contains the existing profile's cookies.
        self.context = self.browser.contexts[0]

    def _open_profile(self):
        if (
            not self.config.profile
            or "/" in self.config.profile
            or "\\" in self.config.profile
            or self.config.profile in (".", "..")
        ):
            raise ListingError("Edge 配置名称无效。")
        directory = Path(self.config.user_data).expanduser().resolve()
        if not directory.is_dir() or not (directory / self.config.profile).is_dir():
            raise ListingError(
                "没有找到所选 Edge 用户资料，请选择实际登录抖店的资料目录。"
            )
        self.key = (str(directory), self.config.profile)
        endpoint = profile_endpoint(directory)
        if not endpoint:
            self.start_page = "about:blank#greatlibrary-listing-" + uuid.uuid4().hex
            command = [
                edge_executable(self.config.executable),
                "--user-data-dir=" + str(directory),
                "--profile-directory=" + self.config.profile,
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "--enable-automation",
                "--disable-session-crashed-bubble",
                "--no-first-run",
                "--new-window",
                self.start_page,
            ]
            # Native browser process survives Playwright disconnects and toolbox exit.
            options = dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.name == "nt":
                options["creationflags"] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                options["start_new_session"] = True
            try:
                subprocess.Popen(command, **options)
            except OSError as exc:
                raise ListingError(f"无法打开 Edge 操作窗口：{exc}") from exc
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.cancel.wait(0.15):
                    raise Cancelled("已停止打开 Edge，已打开的窗口保留。")
                endpoint = profile_endpoint(directory)
                if endpoint:
                    break
            if not endpoint:
                raise ListingError(
                    "Edge 窗口已请求打开，但未能建立操作连接。若普通 Edge 正占用该资料，"
                    "请手动退出一次 Edge（含后台），再点“打开 Edge / 检查登录”；"
                    "之后保留操作窗口即可连续使用，无需每次退出或重新登录。"
                    "若仍失败，请检查 Edge 的启动增强、后台运行或本机调试策略。"
                )
        self._connect(endpoint)
        # An Edge process uses one selected profile; never silently operate another.
        connection = self.browser.new_browser_cdp_session()
        try:
            try:
                arguments = connection.send("Browser.getBrowserCommandLine")[
                    "arguments"
                ]
            except Exception:
                raise ListingError(
                    "发现手动开启的 Edge 调试窗口，但无法核对所用资料。"
                    "请在可选连接中明确填写该窗口的本机调试地址。"
                ) from None
            actual = next(
                (
                    arg.split("=", 1)[1]
                    for arg in arguments
                    if arg.startswith("--profile-directory=")
                ),
                "Default",
            )
            if actual != self.config.profile:
                raise ListingError(
                    "已打开的 Edge 操作窗口使用其他用户资料，请选择相同资料或先关闭该窗口。"
                )
        finally:
            connection.detach()

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        try:
            with _START_LOCK:
                if self.config.endpoint:
                    self.key = (local_endpoint(self.config.endpoint), "explicit")
                    self._connect(self.config.endpoint)
                else:
                    self._open_profile()
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

    def open_login(self):
        """Open once; leave the page available for interactive login if needed."""
        self.goto(ENTRY_URL)
        try:
            self.page.get_by_text("新建商品", exact=True).first.wait_for(
                state="visible", timeout=5000
            )
        except Exception:
            return (
                "Edge 操作窗口已保留。若出现登录或验证，请在该窗口完成后直接开始任务。"
            )
        return "已复用 Edge 登录状态；操作窗口将保留，后续任务继续使用。"

    def check_login(self):
        self.goto(ENTRY_URL)
        try:
            self.page.get_by_text("新建商品", exact=True).first.wait_for(
                state="visible", timeout=20000
            )
        except Exception:
            raise ListingError(
                "请在已打开的 Edge 操作窗口中完成抖店登录或验证，完成后直接重试；无需关闭窗口。"
            ) from None
        return "已复用 Edge 登录状态，正在可见操作窗口中处理。"

    def close(self):
        # Stopping the CDP client disconnects; browser/context/page.close would
        # destroy the reusable window or close the user's ordinary tabs.
        if self.playwright:
            self.playwright.stop()
            self.playwright = None

    def __exit__(self, *_):
        self.close()
