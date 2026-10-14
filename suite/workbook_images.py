"""读取图片列：浮动图片、WPS DISPIMG、Excel 单元格内嵌图片及本地文件路径。"""

import io
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
from PIL import Image, ImageOps
import base64

LIMIT = 20 * 1024 * 1024


def image_bytes_part(data, max_side=1024):
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original)
        image.thumbnail((max_side, max_side))
        rgba = image.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        stream = io.BytesIO()
        rgb.save(stream, "JPEG", quality=85)
    return {
        "inlineData": {
            "mimeType": "image/jpeg",
            "data": base64.b64encode(stream.getvalue()).decode("ascii"),
        }
    }


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _attribute(element, name):
    return next(
        (value for key, value in element.attrib.items() if _local(key) == name), ""
    )


def _xml(archive, path):
    return ET.fromstring(archive.read(path)) if path in archive.namelist() else None


def _relationships(archive, owner):
    part = posixpath.join(
        posixpath.dirname(owner), "_rels", posixpath.basename(owner) + ".rels"
    )
    tree = _xml(archive, part)
    result = {}
    if tree is not None:
        for rel in tree:
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            path = posixpath.normpath(
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.join(posixpath.dirname(owner), target)
            )
            if path.startswith("xl/") and ".." not in path.split("/"):
                result[rel.get("Id")] = path
    return result


def _image(archive, part):
    if (
        not part
        or part not in archive.namelist()
        or archive.getinfo(part).file_size > LIMIT
    ):
        return None
    data = archive.read(part)
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return data
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def read_images(path, worksheet, column, warnings):
    if not column:
        return {}
    pictures = {}
    for picture in getattr(worksheet, "_images", []):
        anchor = getattr(picture.anchor, "_from", None)
        if anchor is not None and anchor.col + 1 == column:
            try:
                data = picture._data()
                if len(data) <= LIMIT:
                    pictures.setdefault(anchor.row + 1, data)
            except Exception:
                warnings.append(f"第 {anchor.row+1} 行图片无法读取，已跳过。")
    with zipfile.ZipFile(path) as archive:
        wps = {}
        tree = _xml(archive, "xl/cellimages.xml")
        if tree is not None:
            rels = _relationships(archive, "xl/cellimages.xml")
            for node in tree.iter():
                if _local(node.tag) != "pic":
                    continue
                name = next(
                    (
                        child.get("name")
                        for child in node.iter()
                        if _local(child.tag) == "cNvPr"
                    ),
                    None,
                )
                embed = next(
                    (
                        _attribute(child, "embed")
                        for child in node.iter()
                        if _local(child.tag) == "blip"
                    ),
                    None,
                )
                if name:
                    wps[name] = _image(archive, rels.get(embed))
        rich = []
        rel_part = "xl/richData/richValueRel.xml"
        relations = _relationships(archive, rel_part)
        values = _xml(archive, rel_part)
        if values is not None:
            rich = [
                _image(archive, relations.get(_attribute(node, "id")))
                for node in values
            ]
        records = []
        structures = _xml(archive, "xl/richData/rdrichvaluestructure.xml")
        image_positions = {}
        if structures is not None:
            for index, structure in enumerate(structures):
                for position, key in enumerate(structure):
                    if "LocalImageIdentifier" in key.get("n", ""):
                        image_positions[index] = position
        values = _xml(archive, "xl/richData/rdrichvalue.xml")
        if values is not None:
            for rv in values:
                contents = [child.text for child in rv if _local(child.tag) == "v"]
                try:
                    position = (
                        0
                        if structures is None
                        else image_positions.get(int(rv.get("s", "0")), -1)
                    )
                    first = contents[position] if position >= 0 else None
                    records.append(rich[int(first)])
                except (TypeError, ValueError, IndexError):
                    records.append(None)
        metadata = _xml(archive, "xl/metadata.xml")
        metadata_records = {}
        if metadata is not None:
            types = next(
                (node for node in metadata if _local(node.tag) == "metadataTypes"), None
            )
            rich_type = next(
                (
                    str(index)
                    for index, node in enumerate(
                        list(types) if types is not None else [], 1
                    )
                    if node.get("name") == "XLRICHVALUE"
                ),
                "",
            )
            future = next(
                (
                    node
                    for node in metadata
                    if _local(node.tag) == "futureMetadata"
                    and node.get("name") == "XLRICHVALUE"
                ),
                None,
            )
            indices = []
            if future is not None:
                for block in (node for node in future if _local(node.tag) == "bk"):
                    index = next(
                        (
                            child.get("i")
                            for child in block.iter()
                            if _local(child.tag) == "rvb"
                        ),
                        None,
                    )
                    indices.append(int(index) if index and index.isdigit() else -1)
            values = next(
                (node for node in metadata if _local(node.tag) == "valueMetadata"), None
            )
            if values is not None:
                for index, block in enumerate(values, 1):
                    record = next(
                        (
                            child.get("v")
                            for child in block
                            if _local(child.tag) == "rc"
                            and (types is None or child.get("t") == rich_type)
                        ),
                        None,
                    )
                    try:
                        record_index = indices[int(record)] if indices else int(record)
                        if record_index >= 0:
                            metadata_records[index] = records[record_index]
                    except (TypeError, ValueError, IndexError):
                        pass
        workbook = _xml(archive, "xl/workbook.xml")
        rels = _relationships(archive, "xl/workbook.xml")
        sheet = next(
            (
                node
                for node in workbook.iter()
                if _local(node.tag) == "sheet" and node.get("name") == worksheet.title
            ),
            None,
        )
        sheet_tree = (
            _xml(archive, rels.get(_attribute(sheet, "id"), ""))
            if sheet is not None
            else None
        )
        if sheet_tree is not None:
            for cell in sheet_tree.iter():
                if _local(cell.tag) != "c" or not cell.get("r"):
                    continue
                letter, row = coordinate_from_string(cell.get("r"))
                if column_index_from_string(letter) != column:
                    continue
                formula = next(
                    (node.text or "" for node in cell if _local(node.tag) == "f"), ""
                )
                match = re.search(r'DISPIMG\s*\(\s*"([^"\r\n]+)"', formula, re.I)
                data = wps.get(match.group(1)) if match else None
                if not data and cell.get("vm", "").isdigit():
                    data = metadata_records.get(int(cell.get("vm")))
                if data:
                    pictures.setdefault(row, data)
    for row in range(1, worksheet.max_row + 1):
        value = worksheet.cell(row, column).value
        if row in pictures or not isinstance(value, str) or not value.strip():
            continue
        if value.lower().startswith(("http://", "https://", "=", "_xlfn.")):
            continue
        file = Path(value.strip())
        if not file.is_absolute():
            file = Path(path).parent / file
        try:
            if (
                file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
                and file.is_file()
                and file.stat().st_size <= LIMIT
            ):
                data = file.read_bytes()
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                pictures[row] = data
        except (OSError, ValueError, Image.DecompressionBombError):
            warnings.append(f"第 {row} 行图片路径无法读取，已跳过。")
    return pictures
