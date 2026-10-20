from dataclasses import dataclass
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Dependency:
    module: str
    package: str


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    subtitle: str
    description: str
    filename: str = ""
    app_class: str = ""
    entrypoint: str = ""
    dependencies: tuple[Dependency, ...] = ()


TOOLS: list[Tool] = []


def register_tool(tool: Tool):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", tool.key):
        raise ValueError("工具 key 必须是小写字母、数字或下划线")
    if any(existing.key == tool.key for existing in TOOLS):
        raise ValueError("重复工具入口：" + tool.key)
    if tool.entrypoint and not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", tool.entrypoint):
        raise ValueError("entrypoint 必须为 module:function")
    TOOLS.append(tool)


for _tool in (
    Tool("color", "商品图片颜色配对", "AI 识色 · 商品编码命名", "识别图片颜色，配对商品编码并导出命名图片。"),
    Tool("matcher", "商品编码匹配工具", "聚水潭 ↔ 抖店", "按款号、颜色和尺码匹配聚水潭与抖店商品编码。", "商品编码匹配工具.py", "ExcelMatcherApp"),
    Tool("generator", "送审资料生成器", "规则匹配 · Excel 导出", "匹配主数据和面料信息，生成送审资料表。", "送审资料生成器.py", "App"),
    Tool("joiner", "送审拼接图", "智能排序 · 正反面拼接", "识别正反面，将同款图片成对拼接导出。", "送审拼接图.py", "ImageJoinerApp"),
):
    register_tool(_tool)

from .extensions import register_extensions
register_extensions(register_tool)


def get_tool(key: str) -> Tool:
    for tool in TOOLS:
        if tool.key == key:
            return tool
    raise ValueError("未知工具入口：" + key)
