"""首页管理凭据；独立工具通过本机认证服务共享配置与限额冷却状态。"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from color_tool.credentials import CredentialError, KeyStore, _dpapi
from color_tool.gemini import GeminiClient, GeminiError, GeminiHTTPError


def config_directory():
    from PySide6.QtCore import QStandardPaths

    return (
        Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.GenericConfigLocation
            )
        )
        / "GreatLibrary"
        / "ColorImageAssistant"
    )


class PoolStore:
    def __init__(self, path=None, codec=_dpapi):
        self.path = Path(path) if path else config_directory() / "gemini-pool.dpapi"
        self.codec = codec

    def load(self):
        if not self.path.exists():
            legacy = KeyStore(self.path.with_name("gemini-key.dpapi"), self.codec)
            key = legacy.load()
            try:
                model = json.loads(
                    self.path.with_name("preferences.json").read_text(encoding="utf-8")
                ).get("model", "")
            except (OSError, ValueError):
                model = ""
            if key:
                entry = {
                    "id": uuid.uuid4().hex,
                    "name": "API 1",
                    "key": key,
                    "models": sorted(legacy.validated_models),
                }
                if model not in entry["models"]:
                    model = legacy.validated_model
                return {"entries": [entry], "model": model, "preferred": entry["id"]}
            return {"entries": [], "model": "", "preferred": ""}
        try:
            data = json.loads(self.codec(self.path.read_bytes(), True))
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                raise ValueError()
            ids, keys = set(), set()
            for entry in data["entries"]:
                if (
                    not isinstance(entry, dict)
                    or any(
                        not isinstance(entry.get(k), str) for k in ("id", "name", "key")
                    )
                    or not isinstance(entry.get("models"), list)
                    or any(not isinstance(m, str) for m in entry["models"])
                    or not entry["id"]
                    or entry["id"] in ids
                    or entry["key"] in keys
                ):
                    raise ValueError()
                GeminiClient(entry["key"])
                ids.add(entry["id"])
                keys.add(entry["key"])
            if any(
                not isinstance(data.get(k, ""), str) for k in ("model", "preferred")
            ):
                raise ValueError()
            return {
                "entries": data["entries"],
                "model": data.get("model", ""),
                "preferred": data.get("preferred", ""),
            }
        except (OSError, ValueError, TypeError) as exc:
            raise CredentialError("API 列表无法读取，请在工具箱主页重新配置。") from exc

    def save(self, data):
        encoded = self.codec(json.dumps(data, ensure_ascii=False).encode(), False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            self.path.stem + "_" + uuid.uuid4().hex + ".tmp"
        )
        try:
            temporary.write_bytes(encoded)
            temporary.replace(self.path)
            self.path.with_name("gemini-key.dpapi").unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)


class PoolManager:
    def __init__(self, store=None, clock=time.time):
        self.store = store or PoolStore()
        self.clock = clock
        self.lock = threading.RLock()
        self.cooldowns = {}
        self.cursors = {}
        self.message = ""
        try:
            self.data = self.store.load()
        except (CredentialError, OSError, GeminiError) as exc:
            self.data = {"entries": [], "model": "", "preferred": ""}
            self.message = str(exc)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def persist(self):
        try:
            self.store.save(self.data)
            self.message = "已由 Windows 当前账户加密记忆"
        except (CredentialError, OSError):
            self.message = "本次会话可用；系统加密记忆不可用"

    def add(self, key, name=""):
        key = GeminiClient(key).api_key
        with self.lock:
            if any(e["key"] == key for e in self.data["entries"]):
                raise GeminiError("这个 API 已在列表中。")
            entry = {
                "id": uuid.uuid4().hex,
                "name": name.strip() or f"API {len(self.data['entries']) + 1}",
                "key": key,
                "models": [],
            }
            self.data["entries"].append(entry)
            self.data["preferred"] = entry["id"]
            self.persist()
            return entry["id"]

    def remove(self, identifier):
        with self.lock:
            self.data["entries"] = [
                e for e in self.data["entries"] if e["id"] != identifier
            ]
            if self.data["preferred"] == identifier:
                self.data["preferred"] = next(
                    (e["id"] for e in self.data["entries"]), ""
                )
            self.cooldowns.pop(identifier, None)
            self.cursors.clear()
            self.persist()

    def select(self, identifier=None, model=None):
        with self.lock:
            if identifier is not None:
                if not any(e["id"] == identifier for e in self.data["entries"]):
                    raise GeminiError("API 已被删除。")
                self.data["preferred"] = identifier
                self.cursors.clear()
            if model is not None:
                if not any(model in e["models"] for e in self.data["entries"]):
                    raise GeminiError("请先检测这个模型。")
                self.data["model"] = model
            self.persist()

    def detected(self, identifier, models):
        with self.lock:
            entry = next(
                (e for e in self.data["entries"] if e["id"] == identifier), None
            )
            if not entry:
                return
            entry["models"] = [m["name"] for m in models]
            if not self.data["model"] or not any(
                self.data["model"] in e["models"] for e in self.data["entries"]
            ):
                self.data["model"] = entry["models"][0] if entry["models"] else ""
            self.persist()

    def acquire(self, model, excluded):
        with self.lock:
            candidates = [e for e in self.data["entries"] if model in e["models"]]
            if not candidates:
                raise GeminiError(
                    "没有支持当前模型的 API，请在工具箱主页新增或检测 API。"
                )
            preferred = self.cursors.get(model, self.data["preferred"])
            index = next(
                (i for i, e in enumerate(candidates) if e["id"] == preferred), 0
            )
            candidates = candidates[index:] + candidates[:index]
            now = self.clock()
            for entry in candidates:
                if (
                    entry["id"] not in excluded
                    and self.cooldowns.get(entry["id"], 0) <= now
                ):
                    return dict(entry)
            remaining = max(
                1,
                int(
                    min(
                        (self.cooldowns.get(e["id"], now + 60) for e in candidates),
                        default=now + 60,
                    )
                    - now
                ),
            )
            raise GeminiError(
                f"当前模型的 API 均已限额或处于冷却，约 {remaining} 秒后可重试；已完成内容保留。同一 Google 项目的 Key 可能共享额度。"
            )

    def cooldown(self, identifier, seconds, model):
        with self.lock:
            self.cooldowns[identifier] = max(
                self.cooldowns.get(identifier, 0),
                self.clock() + min(86400, max(1, seconds)),
            )
            entries = [e for e in self.data["entries"] if model in e["models"]]
            index = next(
                (i for i, e in enumerate(entries) if e["id"] == identifier), -1
            )
            if entries:
                self.cursors[model] = entries[(index + 1) % len(entries)]["id"]


class PoolBroker:
    def __init__(self, manager):
        self.manager = manager
        self.token = secrets.token_urlsafe(32)
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + broker.token
                ):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError()
                    args = json.loads(self.rfile.read(length))
                    if self.path == "/snapshot":
                        result = manager.snapshot()
                    elif self.path == "/acquire":
                        result = manager.acquire(args["model"], args["excluded"])
                    elif self.path == "/cooldown":
                        manager.cooldown(
                            args["id"], float(args["seconds"]), args["model"]
                        )
                        result = {}
                    else:
                        self.send_error(404)
                        return
                    response = {"result": result}
                except GeminiError as exc:
                    response = {"error": str(exc)}
                except (ValueError, TypeError, KeyError):
                    response = {"error": "本机 API 配置请求无效。"}
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def environment(self):
        return {"GREATLIBRARY_AI_URL": self.url, "GREATLIBRARY_AI_TOKEN": self.token}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RemotePool:
    def __init__(self, url, token):
        # Only authenticated loopback traffic uses this direct opener.
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise GeminiError("本机 API 配置地址无效。")
        self.url, self.token = url.rstrip("/"), token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, operation, **args):
        request = urllib.request.Request(
            self.url + "/" + operation,
            data=json.dumps(args).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(request, timeout=3) as response:
                result = json.load(response)
        except (OSError, ValueError) as exc:
            raise GeminiError(
                "无法读取工具箱主页的 API 配置，请重新打开工具箱。"
            ) from None
        if result.get("error"):
            raise GeminiError(result["error"])
        return result["result"]

    def snapshot(self):
        return self.call("snapshot")

    def acquire(self, model, excluded):
        return self.call("acquire", model=model, excluded=list(excluded))

    def cooldown(self, identifier, seconds, model):
        self.call("cooldown", id=identifier, seconds=seconds, model=model)


class RotatingGeminiClient(GeminiClient):
    def __init__(self, pool, model, cancel=None, log=None):
        super().__init__("pool-managed", model, cancel)
        self.pool = pool
        self.log = log or (lambda message: None)

    def _request(self, method, path, payload=None):
        attempted = set()
        while True:
            self.check_cancel()
            entry = self.pool.acquire(self.model, attempted)
            self.check_cancel()
            attempted.add(entry["id"])
            # A separate client per attempt prevents concurrent style requests from
            # overwriting one another's key. Model, path and payload never change.
            client = GeminiClient(
                entry["key"], self.model, self.cancel, retry_rate_limit=False
            )
            try:
                return client._request(method, path, payload)
            except GeminiHTTPError as exc:
                if exc.status != 429:
                    raise
                self.pool.cooldown(entry["id"], exc.retry_after, self.model)
                self.log(
                    f"{entry['name']} 达到额度/速率上限，冷却 {int(exc.retry_after)} 秒；尝试下一条支持当前模型的 API。"
                )


_standalone = None
_standalone_stamp = None


def shared_pool():
    if os.environ.get("GREATLIBRARY_AI_URL"):
        return RemotePool(
            os.environ["GREATLIBRARY_AI_URL"],
            os.environ.get("GREATLIBRARY_AI_TOKEN", ""),
        )
    global _standalone, _standalone_stamp
    store = PoolStore()
    stamp = store.path.stat().st_mtime_ns if store.path.exists() else None
    if _standalone is None or stamp != _standalone_stamp:
        _standalone = PoolManager(store)
        _standalone_stamp = stamp
    return _standalone


def shared_client(cancel=None, log=None, pool=None):
    pool = pool or shared_pool()
    snapshot = pool.snapshot()
    model = snapshot.get("model", "")
    if not model or not any(model in e["models"] for e in snapshot["entries"]):
        raise GeminiError("请在工具箱主页配置 API 并检测、选择模型。")
    return RotatingGeminiClient(pool, model, cancel, log)
