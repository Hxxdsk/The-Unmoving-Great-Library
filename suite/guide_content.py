"""Local workflow state for contextual desktop-pet guidance."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Note:
    key: str
    title: str
    text: str
    action: str = ""


BOOKS = {
    "hub": [
        Note(
            "welcome",
            "帕秋莉在这里",
            "唔，想整理哪一摞资料？点开一间小工房，我会跟着当前步骤陪你一起整理。",
        ),
        Note(
            "quiet",
            "不打扰的小约定",
            "点击我可以聊天或查看提示；可以把我拖到窗口任意位置，靠到边缘我就会扒窗休息。本次启动不再主动提醒，想找我时点点露出的脑袋。",
        ),
    ],
    "color": [
        Note(
            "files",
            "先找到这一摞图片",
            "选含表格和款号文件夹的目录吧。表格要有“供应商款式编码、颜色、商品编码”，每款图片放进同名文件夹。",
        ),
        Note(
            "read",
            "把颜色书签夹好",
            "读取表格后再识色。想用 AI，放一张名为“颜色”的总览图；只想手动配对也可以，不必准备总览图。",
        ),
        Note(
            "pair",
            "让我看看衣服本来的颜色",
            "先核对同名颜色，再为待连接的颜色选原图。悬停文件名就能在右侧看图；双击可看全图，拿不准的先别急着确认。",
        ),
        Note(
            "export",
            "最后点一遍书签",
            "检查每个颜色的原图，再确认输出。同色各尺码共用原图；只导出一部分时勾“仅输出已配对”，原图会留好。",
        ),
    ],
    "matcher": [
        Note(
            "files",
            "两本表，先摆到桌上",
            "聚水潭表要有款式编码、颜色、规格、商品编码；抖店表要有商品ID、商品规格、商家编码。选好这两本，再往下一页走。",
        ),
        Note(
            "pid",
            "先认款，再认 PID",
            "点左边的款号，再到右边选对应 PID，看看两边颜色是否对得上。同一款可关联多个 PID；不要只凭相似颜色就认成同款。",
        ),
        Note(
            "colors",
            "把同色的书签连起来",
            "连接两边对应颜色再导出；未配对颜色保留原编码。尺码括号里的体重备注不会妨碍 M、XL 配对，有多个候选编码时会停下来保护你。",
        ),
    ],
    "generator": [
        Note(
            "files",
            "送审小档案，从这里开始",
            "先选商品资料表和主数据表。资料填款号、款式、尺码、颜色、面料、成本；上下装不用手填，我会按款式帮你判断。",
        ),
        Note(
            "materials",
            "面料原文，别丢小字",
            "面料、里料、填充物分段写清成分和比例。图片可选，能插在“图片”列或填本地路径；主数据决定品类和标准价。",
        ),
        Note(
            "review",
            "生成之后，再翻一遍",
            "“加绒针织裤”会按“针织裤”匹配，标准价加20。生成后看看黄色格和AI核对页，缺的编码或不确定标准别直接提交。",
        ),
    ],
    "joiner": [
        Note(
            "files",
            "正面背面，先来排排坐",
            "可添加多个图片文件夹，也能多选图片。加载只摆放图片；想让我帮忙辨认正反面，再点“智能排序”。",
        ),
        Note(
            "order",
            "这一张，应该站哪边？",
            "看看每对是否同款同色、正面在前背面在后。拿不准的徽标可点着改；图片也能拖动调整，不必全听我的。",
        ),
        Note(
            "export",
            "把配好的两页装订起来",
            "确认顺序后再拼接，成品去第一个文件夹的“处理后”，原图留着。重命名会改原文件名，点之前再看一眼哦。",
        ),
    ],
    "listing": [
        Note(
            "files",
            "草稿工房，先收好素材",
            "选资料根目录：每款准备800、3.4、白底、详情、水洗标、颜色文件夹；尺码图放“颜色”里并命名为“尺码”。",
        ),
        Note(
            "titles",
            "每款都夹一张标题书签",
            "勾选款号，填好每款标题，再看价格和库存。主图和3:4取前5张；详情和颜色图按原流程上传，缺失颜色会跳过。",
        ),
        Note(
            "run",
            "这一页，我来慢慢填",
            "确认后开始保存草稿。工作页会在后台运行；你可以继续用其他窗口，别手动改工具工作页。只有看到保存回执才记为成功。",
        ),
        Note(
            "review",
            "草稿收好了，再检查一遍",
            "保存后到抖店核对。失败款保留任务记录，查明原因后重置选中记录再重试；我不会替你直接发布商品。",
        ),
    ],
}


def notes_for(component, state):
    notes = list(BOOKS.get(component, BOOKS["hub"]))
    if component == "hub" and not state.get("ai_ready", False):
        notes.insert(
            0,
            Note(
                "setup-ai",
                "先给魔法书一点墨水",
                "还没选好可用的 AI 模型呢。到“API / 模型 · 管理”添加 API、检测后选模型；配好了我就不再重复念这页。",
                "去配置 AI",
            ),
        )
    if component == "listing" and not state.get("paired", False):
        notes.insert(
            0,
            Note(
                "setup-edge",
                "先和 Edge 认个门",
                "第一次请安装连接扩展并连接 Edge。配对会记住，重启后自动恢复；如果只是还没打开浏览器，不用反复重新连接。",
                "查看连接设置",
            ),
        )
    return notes


def current_note(component, state):
    notes = notes_for(component, state)
    if notes[0].key.startswith("setup-"):
        return notes[0]
    if state.get("busy"):
        phase = str(state.get("phase") or "这一摞资料正在整理")[:76]
        return Note(
            "working",
            "嘘，魔法书正在翻页",
            phase + "。我先轻轻陪着，想停下来就用原来的取消或停止按钮。",
        )
    if component == "color" and state.get("loaded") and state.get("missing_images"):
        return Note(
            "pair",
            "还缺几张颜色原图",
            "这款图片还没收齐：请检查款号文件夹中的原图，再为待连接的颜色手动选图。不要把缺图的颜色随便配给其他图片。",
        )
    if component == "listing" and state.get("directory") and not state.get("loaded"):
        return Note(
            "read",
            "目录选好了，读入款号吧",
            "现在点击“读取货号”。读入之后，给需要处理的款填写标题，核对价格和库存，再开始保存草稿。",
        )
    if component == "matcher" and state.get("step") == 1:
        if state.get("js_ready") and not state.get("dd_ready"):
            return Note(
                "dd-file",
                "还缺抖店编码表",
                "聚水潭表已经选好啦。再选择包含商品ID、商品规格、商家编码的抖店导出表，然后继续下一步。",
            )
    if component == "hub":
        return notes[0]
    if component == "generator" and state.get("input") and not state.get("master"):
        return Note(
            "materials",
            "还缺一本主数据表",
            "商品资料已经选好啦，还需要选择主数据表，才能匹配品类和标准价格。请在这里补上，再生成送审资料。",
        )
    keys = {
        "color": (
            "export"
            if state.get("output") or state.get("ready_export")
            else (
                "pair"
                if state.get("loaded")
                else "read" if state.get("directory") else "files"
            )
        ),
        "matcher": (
            "colors"
            if state.get("step") == 3
            else "pid" if state.get("step") == 2 else "files"
        ),
        "generator": (
            "review"
            if state.get("input") and state.get("master")
            else "materials" if state.get("input") else "files"
        ),
        "joiner": "order" if state.get("loaded") else "files",
        "listing": (
            "titles"
            if state.get("missing_titles")
            else "run" if state.get("loaded") else "files"
        ),
    }
    return next((n for n in notes if n.key == keys.get(component)), notes[0])


class GuideSession:
    def __init__(self, component):
        self.component, self.muted, self.manual = component, False, None
        self.last_automatic = None

    def choose(self, state):
        automatic = current_note(self.component, state)
        if automatic.key != self.last_automatic:
            self.manual = None
            self.last_automatic = automatic.key
        return self.manual or automatic

    def turn(self, state, delta=1):
        notes = notes_for(self.component, state)
        current = self.choose(state)
        index = next((i for i, n in enumerate(notes) if n.key == current.key), -1)
        self.manual = notes[(index + delta) % len(notes)]
        return self.manual


def read_state(owner, component):
    """Observe existing in-memory UI state; never inspect user documents or keys."""

    def text(name):
        value = getattr(owner, name, None)
        try:
            return (
                value.text()
                if hasattr(value, "text")
                else value.get() if hasattr(value, "get") else value
            )
        except (RuntimeError, AttributeError):
            return ""

    def local_file(name):
        value = str(text(name) or "")
        if value.startswith(("\\\\", "//")):
            return bool(value)  # Do not block the UI probing a network share.
        cache = getattr(owner, "_guide_paths", {})
        if name not in cache or cache[name][0] != value:
            try:
                ready = bool(value and Path(value).is_file())
            except OSError:
                ready = False
            cache[name] = (value, ready)
            owner._guide_paths = cache
        return cache[name][1]

    busy = bool(getattr(owner, "job", None) or getattr(owner, "worker", None))
    task = getattr(owner, "task", None)
    if task:
        busy = bool(task.busy)
    state = dict(busy=busy, phase=getattr(owner, "_guide_phase", ""), loaded=False)
    if component == "hub":
        state.update(ai_ready=getattr(owner, "_guide_ai_ready", False), busy=False)
    elif component == "color":
        state.update(
            directory=bool(text("directory")),
            loaded=bool(getattr(owner, "project", None)),
            output=bool(getattr(owner, "last_output", None)),
            ready_export=bool(
                getattr(owner, "export_button", None)
                and owner.export_button.isEnabled()
            ),
            missing_images=bool(
                getattr(owner, "project", None)
                and any(not style.images for style in owner.project.styles)
            ),
        )
    elif component == "listing":
        bridge = getattr(owner, "bridge", None)
        state.update(
            paired=bool(
                bridge
                and (bridge.connected.is_set() or bridge.pairing.data.get("paired"))
            ),
            directory=bool(text("directory")),
            loaded=bool(owner.products),
            missing_titles=any(not p.title.strip() for p in owner.products),
        )
    elif component == "matcher":
        step = str(getattr(owner, "_guide_step", "1"))
        state.update(
            step=3 if "3 / 3" in step else 2 if "2 / 3" in step else 1,
            js_ready=bool(text("jushuitan_path")),
            dd_ready=bool(text("doudian_path")),
        )
    elif component == "generator":
        state.update(input=local_file("var_input"), master=local_file("var_master"))
    elif component == "joiner":
        state.update(
            loaded=bool(getattr(owner, "image_list", [])),
            busy=busy or getattr(owner, "_loading", False),
        )
    return state
