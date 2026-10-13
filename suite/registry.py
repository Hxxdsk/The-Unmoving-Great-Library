from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    subtitle: str
    description: str
    filename: str = ""
    app_class: str = ""


TOOLS = (
    Tool("color", "商品图片颜色配对", "AI 识色 · 商品编码命名", "Gemini 比较服装颜色，人工连接未匹配颜色，按各尺码商品编码复制原图。"),
    Tool("matcher", "商品编码匹配工具", "聚水潭 ↔ 抖店", "款式与 PID 关联、颜色配对，结合尺码写入抖店商家编码。", "商品编码匹配工具.py", "ExcelMatcherApp"),
    Tool("generator", "送审资料生成器", "规则匹配 · Excel 导出", "读取商品资料和主数据，整理面料、尺码、颜色与分类，生成送审文件。", "送审资料生成器.py", "App"),
    Tool("joiner", "送审拼接图", "智能排序 · 正反面拼接", "多文件夹加载、同色正反面排序、拖动调整、批量重命名与拼接。", "送审拼接图.py", "ImageJoinerApp"),
)


def get_tool(key: str) -> Tool:
    for tool in TOOLS:
        if tool.key == key:
            return tool
    raise ValueError("未知工具入口：" + key)
