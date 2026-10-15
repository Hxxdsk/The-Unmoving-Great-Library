"""Authenticated local relay between Playwright and the user's Edge extension."""

from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .models import ListingError

EXTENSION_DIR = Path(__file__).with_name("edge_extension")


@dataclass(frozen=True)
class BrowserConfig:
    endpoint: str = ""


class EdgeBridge:
    def __init__(self):
        self.token = secrets.token_hex(32)
        self.port = None
        self.ready = threading.Event()
        self.connected = threading.Event()
        self.thread = None
        self.loop = self.stop_event = None
        self.extension = self.client = None
        self.client_epoch = None
        self.failure = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        if not self.ready.wait(5) or self.failure:
            raise ListingError(f"无法启动 Edge 连接：{self.failure or '启动超时'}")
        return BrowserConfig(f"ws://127.0.0.1:{self.port}/cdp/{self.token}")

    @property
    def connect_url(self):
        self.start()
        return f"http://127.0.0.1:{self.port}/greatlibrary/connect#{self.token}"

    def open_edge(self):
        url = self.connect_url
        if os.name == "nt":
            os.startfile("microsoft-edge:" + url)
        else:
            webbrowser.open(url)

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self.failure = str(exc)
            self.ready.set()
        finally:
            self.connected.clear()

    def _help(self):
        path = html.escape(str(EXTENSION_DIR.resolve()))
        return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>大图书馆 · 连接 Edge</title><style>
body{{background:#f5f1fc;color:#302445;font:16px "Microsoft YaHei",sans-serif;margin:7vh auto;max-width:760px;padding:25px}}
main{{background:white;border:1px solid #e6dcf5;border-radius:20px;padding:36px}}h1{{font-size:24px}}li{{margin:14px 0}}code{{overflow-wrap:anywhere;background:#f5f1fc;padding:6px}}#status{{color:#7652ae;line-height:1.8}}
</style><main><h1>大图书馆 · 连接 Edge</h1>
<p id="status">首次使用请加载连接扩展；已加载时会自动连接。</p>
<ol><li>打开 <b>edge://extensions</b>，开启“开发人员模式”。</li>
<li>点击“加载解压缩的扩展”，选择下方目录。</li>
<li>回到工具箱，重新点击“连接 Edge”；在新建的抖店页确认已登录，然后开始任务。</li></ol>
<p><code>{path}</code></p><p>使用当前 Edge 登录状态；普通标签页可以继续使用。请勿手动操作工具的抖店工作页。Edge 会显示扩展调试提示，任务期间请保留。</p></main></html>"""

    async def _request(self, connection, request):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        def response(code, reason, body):
            data = body.encode("utf-8")
            return Response(
                code,
                reason,
                Headers(
                    {
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Length": str(len(data)),
                        "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer",
                        "X-Content-Type-Options": "nosniff",
                    }
                ),
                data,
            )

        if request.headers.get("Host") != f"127.0.0.1:{self.port}":
            return response(403, "Forbidden", "本机连接地址无效")
        if request.path == "/greatlibrary/connect":
            return response(200, "OK", self._help())
        valid = request.path in (f"/extension/{self.token}", f"/cdp/{self.token}")
        origin = request.headers.get("Origin", "")
        if not valid or (origin and not origin.startswith("chrome-extension://")):
            return response(403, "Forbidden", "未授权连接")
        return None

    async def _handle(self, socket):
        from websockets.exceptions import ConnectionClosed

        if socket.request.path.startswith("/extension/"):
            if self.extension:
                await socket.close(1008, "已有 Edge 连接，请关闭旧连接后重试")
                return
            self.extension = socket
            self.connected.set()
            try:
                async for message in socket:
                    value = json.loads(message)
                    client = self.client
                    if (
                        value.get("type") != "heartbeat"
                        and client
                        and value.get("relayEpoch") == self.client_epoch
                    ):
                        try:
                            await client.send(message)
                        except ConnectionClosed:
                            pass
            except (ConnectionClosed, ValueError):
                pass
            finally:
                self.extension = None
                self.connected.clear()
                if self.client:
                    await self.client.close(
                        1011, "Edge 连接已断开，请重新连接后核对当前货号"
                    )
        else:
            if self.client or not self.extension:
                await socket.close(1008, "请先点击连接 Edge，并加载连接扩展")
                return
            self.client = socket
            self.client_epoch = secrets.token_hex(16)
            try:
                async for message in socket:
                    if not self.extension:
                        break
                    value = json.loads(message)
                    value["relayEpoch"] = self.client_epoch
                    await self.extension.send(json.dumps(value))
            except (ConnectionClosed, ValueError):
                pass
            finally:
                self.client_epoch = None
                if self.extension:
                    try:
                        await self.extension.send(json.dumps({"type": "detach"}))
                    except ConnectionClosed:
                        pass
                self.client = None

    async def _serve(self):
        from websockets.asyncio.server import serve

        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        async with serve(
            self._handle,
            "127.0.0.1",
            0,
            process_request=self._request,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
        ) as server:
            self.port = server.sockets[0].getsockname()[1]
            self.ready.set()
            await self.stop_event.wait()

    def close(self):
        if self.loop and self.stop_event and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop_event.set)
