"""Persist the local extension capability, never infer deletion from offline state."""

from contextlib import contextmanager
import json
import os
import re
from pathlib import Path


class EdgePairing:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = {}
        if self.path and self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("token", "")))
                or type(value.get("port")) is not int
                or not 0 < value["port"] < 65536
            ):
                raise ValueError(
                    "Edge 配对记录格式无效，请检查本机配置文件；未覆盖原记录"
                )
            self.data = value

    @contextmanager
    def lock(self):
        if not self.path:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+b") as stream:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    if not stream.read(1):
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    "已有上架组件运行，请使用已打开的组件；配对记录保留"
                ) from exc
            yield

    def save(self, **values):
        self.data.update(values)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            try:
                with open(
                    temp,
                    "w",
                    encoding="utf-8",
                    opener=lambda p, f: os.open(p, f, 0o600),
                ) as stream:
                    json.dump(self.data, stream, ensure_ascii=False)
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)

    def mark_connected(self, origin):
        values = {"paired": True}
        match = re.fullmatch(r"chrome-extension://([a-p]{32})", origin)
        if match:
            extension_id = match[1]
            values["extension_id"] = extension_id
            # Only a profile where we have positively observed this extension
            # can later provide evidence of deletion. Missing/unreadable files
            # or a stopped browser are not deletion signals.
            base = os.environ.get("LOCALAPPDATA")
            if base:
                root = Path(base) / "Microsoft/Edge/User Data"
                for path in root.glob("*/Secure Preferences"):
                    if self._installed(path, extension_id) is True:
                        values["profile_preferences"] = str(path)
                        break
        self.save(**values)

    @staticmethod
    def _installed(path, extension_id):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            settings = data.get("extensions", {}).get("settings")
            if isinstance(settings, dict):
                return extension_id in settings
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def removed(self):
        path, extension_id = self.data.get("profile_preferences"), self.data.get(
            "extension_id"
        )
        return bool(
            path and extension_id and self._installed(path, extension_id) is False
        )
