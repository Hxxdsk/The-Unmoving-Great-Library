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
    Tool("color", "商品图片颜色配对", "AI 识色 · 商品编码命名", "Gemini 比较服装颜色，人工连接未匹配颜色，按各尺码商品编码复制原图。"),
    Tool("matcher", "商品编码匹配工具", "聚水潭 ↔ 抖店", "款式与 PID 关联、颜色配对，结合尺码写入抖店商家编码。", "商品编码匹配工具.py", "ExcelMatcherApp"),
    Tool("generator", "送审资料生成器", "规则匹配 · Excel 导出", "读取商品资料和主数据，整理面料、尺码、颜色与分类，生成送审文件。", "送审资料生成器.py", "App"),
    Tool("joiner", "送审拼接图", "智能排序 · 正反面拼接", "多文件夹加载、同色正反面排序、拖动调整、批量重命名与拼接。", "送审拼接图.py", "ImageJoinerApp"),
):
    register_tool(_tool)

from .extensions import register_extensions
register_extensions(register_tool)


def get_tool(key: str) -> Tool:
    for tool in TOOLS:
        if tool.key == key:
            return tool
    raise ValueError("未知工具入口：" + key)
