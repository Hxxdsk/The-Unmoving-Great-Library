"""只缓存非敏感 AI 结果；使用内容摘要，无 Key、原始图片或整张表格。"""

import hashlib
import json
import os
import uuid
from pathlib import Path


def cache_directory():
    base = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        if os.name == "nt"
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    )
    return base / "GreatLibraryToolbox" / "ai-cache"


class ResultCache:
    def __init__(self, namespace, directory=None):
        self.directory = (
            Path(directory) if directory else cache_directory()
        ) / namespace

    def key(self, data, files=()):
        digest = hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        )
        for path in files:
            path = Path(path)
            digest.update(path.name.encode())
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
        return digest.hexdigest()

    def get(self, key):
        try:
            return json.loads(
                (self.directory / (key + ".json")).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def put(self, key, data):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / (key + ".json")
            temporary = target.with_name(target.stem + "_" + uuid.uuid4().hex + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            pass  # 缓存不可写不影响本次识别。
        finally:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
