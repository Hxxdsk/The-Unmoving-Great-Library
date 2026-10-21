from __future__ import annotations

import hashlib
import json
import os
from functools import cmp_to_key
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FOLDERS = ("800", "3.4", "水洗标", "详情", "白底", "颜色")


class ListingError(RuntimeError):
    pass


class Cancelled(ListingError):
    pass


class SaveUncertain(ListingError):
    pass


def natural_key(value):
    # Do not emit an empty text prefix for digit-leading names: that used to
    # place 1.jpg ahead of !0.jpg. Keep punctuation and numeric runs explicit.
    return tuple(
        (
            (1, int(part))
            if part.isdecimal() and part.isascii()
            else (2, part.casefold()) if part[0].isalnum() else (0, part.casefold())
        )
        for part in re.findall(r"[0-9]+|[^\W\d_]+|.", str(value))
    )


if os.name == "nt":
    import ctypes

    _logical_compare = ctypes.WinDLL("shlwapi").StrCmpLogicalW
    _logical_compare.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    _logical_compare.restype = ctypes.c_int
else:
    _logical_compare = None


def filename_key(value):
    """Explorer name-ascending order on Windows, deterministic fallback elsewhere."""
    name = str(value)
    return cmp_to_key(_logical_compare)(name) if _logical_compare else natural_key(name)


@dataclass
class Product:
    path: Path
    images: dict[str, tuple[Path, ...]]
    issues: list[str]
    fingerprint: str
    title: str = ""
    color_references: tuple[Path, ...] = ()
    color_uploads: tuple[Path, ...] | None = None
    sources_prepared: bool = False

    @property
    def color_photos(self):
        return (
            tuple(p for p in self.images.get("颜色", ()) if p.stem != "尺码")
            if self.color_uploads is None
            else self.color_uploads
        )

    @property
    def sku(self):
        return self.path.name

    @property
    def main(self):
        return self.images["800"][:5]

    @property
    def portrait(self):
        return self.images["3.4"][:5]


def scan_products(root: Path) -> list[Product]:
    root = root.resolve()
    if not root.is_dir():
        raise ListingError("请选择包含货号文件夹的目录。")
    products = []
    for folder in sorted(root.iterdir(), key=lambda p: filename_key(p.name)):
        if not folder.is_dir() or folder.name.startswith(("_", ".")):
            continue
        images, issues = {}, []
        digest = hashlib.sha256(folder.name.encode())
        for name in FOLDERS:
            directory = folder / name
            paths = (
                tuple(
                    sorted(
                        (
                            p
                            for p in directory.iterdir()
                            if p.is_file() and p.suffix.lower() in IMAGES
                        ),
                        key=lambda p: filename_key(p.name),
                    )
                )
                if directory.is_dir()
                else ()
            )
            images[name] = paths
            if not paths and name != "颜色":
                issues.append(f"{name} 文件夹缺少图片")
            for path in paths:
                stat = path.stat()
                digest.update(
                    f"{name}/{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
                )
        images["尺码"] = tuple(p for p in images["颜色"] if p.stem == "尺码")
        if len(images["白底"]) > 1:
            issues.append("白底文件夹应只有一张图片")
        products.append(Product(folder, images, issues, digest.hexdigest()))
    if not products:
        raise ListingError("目录中没有货号文件夹。")
    return products


class Journal:
    """按目录独立记录，保存结果不确定的项目禁止自动重建。"""

    def __init__(self, root):
        self.path = Path(root) / "_抖店批量上架记录.json"
        if self.path.exists():
            try:
                self.records = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.records, dict) or any(
                    not isinstance(v, dict) for v in self.records.values()
                ):
                    raise ValueError()
            except (OSError, ValueError) as exc:
                raise ListingError(
                    "任务记录无法读取，请保留原记录并检查文件，避免重复创建草稿。"
                ) from exc
        else:
            self.records = {}
        self.lock = threading.RLock()

    def get(self, product):
        return dict(self.records.get(product.sku, {}))

    def update(self, product, **values):
        with self.lock:
            entry = self.records.setdefault(product.sku, {})
            entry.update(
                values,
                fingerprint=product.fingerprint,
                updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)

    def reset(self, products):
        with self.lock:
            for product in products:
                self.records.pop(product.sku, None)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)

    def resumable(self, product):
        old = self.get(product)
        if old:
            return False, (
                "已处理，请重置记录后重新新建"
                if old.get("status") not in ("saving", "uncertain")
                else "保存结果待核对，请核对后重置记录"
            )
        return True, ""
