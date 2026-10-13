from __future__ import annotations

import json
from pathlib import Path


class Settings:
    """只保存非敏感偏好；API Key 只存在当前进程内。"""

    def __init__(self, path: Path):
        self.path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.data = {k: v for k, v in data.items() if k in {"model", "directory", "workbook"} and isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            self.data = {}

    def get(self, key: str, default="") -> str:
        return self.data.get(key, default)

    def set(self, key: str, value: str):
        if key not in {"model", "directory", "workbook"}:
            raise ValueError("不允许保存此设置")
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
