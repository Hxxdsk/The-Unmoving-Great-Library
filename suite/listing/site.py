from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageOps

from .models import ListingError, Cancelled, SaveUncertain
from .browser import ENTRY_URL
from .vision import normalize_size
from .page_ai import PageAI, FORBIDDEN
from .workflow import DESCRIBE, page_key, scope_key, replay, safe_target

DOM = Path(__file__).with_name("page.js").read_text(encoding="utf-8")


class DouyinPage:
    def __init__(
        self, session, cancel, log=lambda _: None, client=None, memory=None, guide=None
    ):
        self.session, self.page, self.cancel, self.log = (
            session,
            session.page,
            cancel,
            log,
        )
        self.editor_url = ""
        self.client = client
        self.memory, self.guide = memory, guide
        self.pending = []
        self.last_plan = None
        self.popups = []
        self.phase = "进入单商品创建流程"

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("已停止；未确认保存的货号保留进度。")
        parsed = urlparse(self.page.url)
        if parsed.hostname != "fxg.jinritemai.com" or re.search(
            r"/login|/passport|/verify|/captcha", parsed.path, re.I
        ):
            raise ListingError(
                "当前页面不是已登录的抖店商品编辑页面，请检查登录或验证码。"
            )
        if self.page.get_by_text(
            re.compile(r"请完成安全验证|拖动滑块完成验证|请先登录")
        ).count():
            raise ListingError("店铺要求重新登录或安全验证，当前货号已暂停。")

    def dom(self, op, label="", selector=None):
        self.check()
        return self.page.evaluate(DOM, {"op": op, "label": label, "selector": selector})

    def wait(self, condition, message, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            if condition():
                self.confirm_memory()
                return
            if self.cancel.wait(0.25):
                self.check()
        raise ListingError(message)

    def visible_control(self, text, root=None):
        scope = root or self.page
        queries = [
            scope.get_by_role("button", name=text, exact=isinstance(text, str)),
            scope.get_by_text(text, exact=isinstance(text, str)),
        ]
        if isinstance(text, str):
            queries += [
                scope.get_by_role("button", name=text, exact=False),
                scope.get_by_text(re.compile(re.escape(text))),
            ]
        for query in queries:
            visible = [
                query.nth(i) for i in range(query.count()) if query.nth(i).is_visible()
            ]
            if len(visible) == 1:
                return visible[0]
        return None

    def memory_failed(self, recipe):
        if self.memory:
            try:
                self.memory.failed(recipe)
            except Exception as exc:
                self.log("流程记忆暂不可写，本次继续运行：" + str(exc))
                self.memory = None

    def confirm_memory(self):
        for plan in self.pending:
            if self.memory:
                try:
                    self.memory.remember(
                        **{k: v for k, v in plan.items() if not k.startswith("_")},
                        outcome=page_key(self.page),
                    )
                except Exception as exc:
                    self.log("流程记忆暂不可写，本次继续运行：" + str(exc))
                    self.memory = None
        self.pending.clear()

    def discard_pending(self):
        if self.memory:
            for plan in self.pending:
                if plan.get("_recipe"):
                    self.memory_failed(plan["_recipe"])
        self.pending.clear()

    def memorized(self, goal, root=None, allow_save=False):
        if not self.memory:
            return None
        for recipe in self.memory.recipes(goal, page_key(self.page), scope_key(root)):
            if recipe.get("failures", 0) >= 3:
                continue
            try:
                target = replay(
                    self.page, recipe["actions"], root, allow_save, self.cancel
                )
                if target is not None and target is not True:
                    self.log("优先使用流程记忆：" + goal)
                    return (
                        target,
                        list(recipe["actions"]),
                        recipe.get("source", "记忆"),
                        recipe,
                    )
            except Cancelled:
                raise
            except Exception:
                pass
            self.memory_failed(recipe)
        return None

    def ai_context(self, goal):
        return {
            "完整流程": "进入单商品创建页 → 本地上传800主图前5张 → 网站推荐标题第一个 → 继续生成编辑页 → 替换3:4、白底、详情、水洗标 → 填当前货号 → 48小时发货 → 颜色正面图、尺码和体重 → 价格399、目标现货库存1000 → 校验 → 仅保存草稿",
            "当前阶段": self.phase,
            "本次目的": goal,
            "操作原则": "根据实际页面决定入口；名字可以不同。菜单已展开就选子项，不反复寻找或点击上级按钮。悬停出现的弹层也属于当前页面。不得发布或改动其他商品。",
            "成功经验": (
                self.memory.hints(goal, page_key(self.page)) if self.memory else []
            ),
        }

    def control(self, text, root=None, allow_save=False):
        self.check()
        goal = text if isinstance(text, str) else text.pattern
        before, scope = page_key(self.page), scope_key(root)
        recalled = self.memorized(goal, root, allow_save)
        source, actions, recipe = "程序", [], None
        if recalled:
            target, actions, source, recipe = recalled
        else:
            target = self.visible_control(text, root)
            if target is None:
                source = "AI"
                try:
                    target = PageAI(
                        self.page,
                        self.client,
                        self.cancel,
                        self.log,
                        trace=actions,
                        context=self.ai_context(goal),
                    ).find(goal, root, allow_save)
                except Cancelled:
                    raise
                except Exception as exc:
                    if not self.guide:
                        raise
                    self.log(f"自动定位未完成，转交用户指导：{goal}（{exc}）")
                    target, actions = self.guide.run(
                        self.page, goal, root=root, allow_save=allow_save
                    )
                    source = "用户指导"
            if not actions or actions[-1]["kind"] != "target":
                actions.append({"kind": "target", "target": target.evaluate(DESCRIBE)})
        # Memory locates the final element, but does not click it twice.
        self.last_plan = dict(
            goal=goal,
            page=before,
            scope=scope,
            source=source,
            actions=actions,
            _recipe=recipe,
        )
        return target

    def record_action(self, target, kind="click"):
        if not self.last_plan:
            return
        plan = dict(self.last_plan)
        # Recipes locate the target after any preceding hover/scroll actions.
        plan["actions"] = [dict(a) for a in plan["actions"]]
        if plan["actions"] and plan["actions"][-1]["kind"] == "target":
            self.pending.append(plan)
        self.last_plan = None

    def click(self, text, root=None):
        self.check()
        goal = text if isinstance(text, str) else text.pattern
        if FORBIDDEN.search(re.sub(r"\s+", "", goal)):
            raise ListingError("工具只保存草稿，禁止点击发布。")
        target = self.control(text, root)
        actual = target.evaluate(
            "e=>{const p=e.closest('button,a,[role=button],[role=menuitem]')||e;return [p.innerText,p.getAttribute('aria-label'),p.title].filter(Boolean).join(' ')}"
        )
        if FORBIDDEN.search(re.sub(r"\s+", "", actual)):
            raise ListingError("工具只保存草稿，禁止点击发布。")
        self.record_action(target)
        target.click(timeout=8000)

    def tab(self, text):
        self.click(text)

    def open_create_menu(self):
        # The real Douyin control opens its single-product menu on hover.
        if self.visible_control("单商品") is not None:
            return
        trigger = self.control("新建商品")
        self.record_action(trigger, "hover")
        trigger.hover(timeout=6000)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.creation_state() in ("upload", "titles", "editor"):
                return
            self.check()
            if self.visible_control("单商品") is not None:
                return
            if self.cancel.wait(0.15):
                self.check()
        # Also support versions whose menu opens by clicking the trigger.
        trigger.click(timeout=5000)
        self.page.wait_for_timeout(250)
        self.creation_state()

    def section(self, *names):
        for name in names:
            selector = self.dom("section", name)
            if selector:
                self.page.locator(selector).scroll_into_view_if_needed()
                return selector
        if self.client is not None or self.guide:
            target = self.control(
                "定位字段标题：" + "/".join(names) + "，不要选择其他字段或实际上传按钮"
            )
            leaf = target.evaluate(
                "e=>{if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id=\"'+e.dataset.glId+'\"]'}"
            )
            selector = self.dom("section_from", names[0], leaf)
            if selector:
                self.record_action(target)
                self.page.locator(selector).scroll_into_view_if_needed()
                return selector
        raise ListingError("页面未识别到字段：" + "/".join(names))

    def photo_count(self, selector):
        return len(self.dom("photos", selector=selector) or [])

    def hover_in_field(self, target, field):
        """Hover the real thumbnail/upload hit area, including its own overlay."""
        target.scroll_into_view_if_needed(timeout=4000)
        leaf = target.evaluate(
            "e=>{if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id=\"'+e.dataset.glId+'\"]'}"
        )
        for attempt in range(2):
            self.check()
            point = self.dom("hover_point", label=field, selector=leaf)
            if point:
                self.page.locator(point["selector"]).hover(
                    position={"x": point["x"], "y": point["y"]}, timeout=4000
                )
                return
            close = self.dom("assistant_close", selector=leaf)
            if close:
                self.log("收起遮挡当前图片字段的辅助浮层")
                self.page.locator(close).click(timeout=3000)
            else:
                # Move out of hover previews and close transient menus before
                # trying again; never force-click through an unrelated overlay.
                self.page.mouse.move(0, 0)
                self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(250)
        raise ListingError(
            "当前图片字段被浮层遮挡，请收起辅助浮窗后重试；未强制点击或删除其他字段。"
        )

    def upload(self, selector, paths):
        paths = [str(Path(path).resolve()) for path in paths]
        if not paths:
            raise ListingError("待上传图片为空。")
        before = self.photo_count(selector)
        inputs = self.dom("file_inputs", selector=selector) or []
        multiple = next((item for item in inputs if item["multiple"]), None)
        if multiple:
            self.page.locator(multiple["selector"]).set_input_files(paths)
        elif inputs:
            for index, path in enumerate(paths):
                inputs = self.dom("file_inputs", selector=selector) or []
                target = next(
                    (item for item in inputs if not item["filled"]),
                    inputs[0] if inputs else None,
                )
                if not target:
                    raise ListingError("上传控件已变化，停止当前货号。")
                self.page.locator(target["selector"]).set_input_files(path)
                self.wait(
                    lambda: self.photo_count(selector) >= before + index + 1
                    and not self.dom("busy", selector=selector),
                    "图片未完成上传",
                )
        else:
            scope = self.page.locator(selector)
            target = self.visible_control(
                re.compile(r"^(上传图片|上传主图|上传辅助图|图片|本地上传)$"), scope
            )
            if target is None:
                trigger = self.dom("upload_trigger", selector=selector)
                target = (
                    self.page.locator(trigger)
                    if trigger
                    else self.control(
                        "定位此字段的上传图片入口，可悬停展开本地上传菜单", scope
                    )
                )
            self.record_action(target, "hover")
            self.hover_in_field(target, selector)
            # React portals can be mounted after hover and outside the field.
            # Wait for a nearby visible local-upload entry, not the last match
            # from unrelated fields or hidden popovers.
            local = None
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                self.check()
                local = self.dom("upload_menu", selector=selector)
                if local:
                    break
                self.page.wait_for_timeout(100)
            if local:
                target = self.page.locator(local)
            else:
                # Some sites open the picker directly from the upload tile.
                # A popup with a renamed entry can also be located with AI.
                menu = self.page.locator(
                    "[role=menu]:visible,.ant-popover:visible,[class*=dropdown]:visible"
                )
                if menu.count() and self.client is not None:
                    target = self.control(
                        "选择当前图片上传浮层中的本地上传入口，不要图库选择或其他字段"
                    )
                    self.record_action(target)
            with self.page.expect_file_chooser(timeout=10000) as chooser:
                target.click(timeout=5000)
            if len(paths) > 1 and not chooser.value.is_multiple():
                raise ListingError("该上传控件仅支持单张图片，请检查页面适配。")
            chooser.value.set_files(paths)
        self.wait(
            lambda: self.photo_count(selector) >= before + len(paths)
            and not self.dom("busy", selector=selector),
            "图片上传超时或被网站拒绝",
            timeout=90,
        )

    def clear(self, selector):
        scope = self.page.locator(selector)
        count = self.photo_count(selector)
        if not count:
            return
        clear = scope.get_by_text("清空", exact=True)
        if clear.count() == 1 and clear.is_visible():
            clear.click()
            self.confirm_modal(("确定", "确认", "清空"))
        else:
            while count:
                image = self.page.locator(
                    (self.dom("photos", selector=selector) or [])[0]
                )
                self.hover_in_field(image, selector)
                remove = self.dom("remove_photo", selector=selector)
                if not remove:
                    raise ListingError("无法确认删除该字段的原图片，未追加新图。")
                self.page.locator(remove).click()
                self.confirm_modal(("确定", "确认", "删除"))
                self.wait(lambda: self.photo_count(selector) < count, "原图片未删除")
                count = self.photo_count(selector)
        self.wait(
            lambda: self.photo_count(selector) == 0, "字段原图片没有清空，未上传替换图"
        )

    def confirm_modal(self, names):
        dialogs = self.page.get_by_role("dialog")
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            if not dialog.is_visible():
                continue
            for name in names:
                button = dialog.get_by_role("button", name=name, exact=True)
                if button.count() == 1 and button.is_visible():
                    button.click()
                    return
            if self.client is not None or self.guide:
                target = self.control(
                    "在当前弹窗确认这一步操作，定位含义相同的按钮：" + "/".join(names),
                    dialog,
                    allow_save="保存草稿" in names,
                )
                self.record_action(target)
                target.click(timeout=10000)
                return

    def replace_images(self, names, paths):
        selector = self.section(*names)
        self.clear(selector)
        self.upload(selector, paths)
        if self.photo_count(selector) != len(paths):
            raise ListingError("字段图片数量与当前货号资料不符。")

    def creation_state(self):
        # A navigation can advance directly, or open a new tool-owned tab.
        for candidate in list(self.popups):
            if (
                not candidate.is_closed()
                and "fxg.jinritemai.com" == urlparse(candidate.url).hostname
            ):
                self.page = self.session.page = candidate
                if hasattr(self.session, "own_page"):
                    self.session.own_page(candidate)
        if self.page.is_closed():
            return "unknown"
        if urlparse(self.page.url).hostname != "fxg.jinritemai.com":
            return "loading"
        if self.dom("section", "主图3:4") or self.dom("section", "主图3：4"):
            self.editor_url = self.page.url
            return "editor"
        if self.dom("recommendations"):
            return "titles"
        if (
            self.visible_control(re.compile(r"^(本地上传|上传资料|上传文件|选择文件)$"))
            is not None
        ):
            return "upload"
        if self.visible_control("单商品") is not None:
            return "menu"
        if any(
            not p.is_closed() and p.url == "about:blank" for p in self.popups
        ) or re.search(
            r"/(?:create|publish|add|edit)(?:/|$)", urlparse(self.page.url).path
        ):
            return "loading"
        return "list"

    def navigation_wait(self, condition, message, timeout=30):
        # Reuse the proven recovery path before waiting for a failed fixed step.
        key, before = "流程：" + message, page_key(self.page)
        if not condition() and self.memory:
            for recipe in self.memory.recipes(key, before):
                if recipe.get("failures", 0) >= 3:
                    continue
                try:
                    replay(
                        self.page,
                        recipe["actions"],
                        cancel=self.cancel,
                        observe=lambda: (self.creation_state(), self.page)[1],
                        done=condition,
                    )
                    self.wait(condition, message, timeout=3)
                    self.log("已按记忆恢复流程：" + message)
                    return
                except Cancelled:
                    raise
                except Exception:
                    self.memory_failed(recipe)
        deadline = time.monotonic() + timeout
        try:
            self.wait(condition, message, min(timeout, 8))
            return
        except Cancelled:
            raise
        except ListingError:
            self.discard_pending()
        actions, history, humans = [], [], 0
        for attempt in range(8):
            self.check()
            while not condition() and self.dom("busy") and time.monotonic() < deadline:
                self.page.wait_for_timeout(500)
                self.check()
            if condition():
                if self.memory and actions:
                    try:
                        self.memory.remember(
                            key,
                            before,
                            actions,
                            source="流程决策",
                            outcome=page_key(self.page),
                        )
                    except Exception as exc:
                        self.log("流程记忆暂不可写：" + str(exc))
                self.confirm_memory()
                return
            error = message
            if self.client is not None:
                try:
                    result = PageAI(
                        self.page,
                        self.client,
                        self.cancel,
                        self.log,
                        trace=actions,
                        context=self.ai_context(key),
                    ).find(
                        message + "；按实际页面寻找可推进此阶段的操作，处理中先等待",
                        navigate=True,
                        history=history[-8:],
                    )
                    history.append(result)
                    self.log(
                        "AI 下一步："
                        + str(result.get("reason") or result.get("action"))
                    )
                    self.page.wait_for_timeout(500)
                    if len(history) < 2 or history[-1] != history[-2]:
                        continue
                except Cancelled:
                    raise
                except Exception as exc:
                    error = str(exc)
            if not self.guide or humans >= 3:
                raise ListingError(error)
            self.log("等待用户完成流程步骤：" + message)
            _, guided = self.guide.run(
                self.page,
                message,
                mode="record",
                condition=condition,
                observe=lambda: (self.creation_state(), self.page)[1],
                allow_partial=True,
            )
            actions = guided if humans == 0 else actions + guided
            humans += 1
            history.append({"action": "用户已操作", "页面状态": self.creation_state()})
        # A final click may have reached the destination on the last round.
        if condition():
            if self.memory and actions:
                try:
                    self.memory.remember(
                        key,
                        before,
                        actions,
                        source="流程决策",
                        outcome=page_key(self.page),
                    )
                except Exception as exc:
                    self.log("流程记忆暂不可写：" + str(exc))
            self.confirm_memory()
            return
        raise ListingError(message + "；多次观察后仍未推进，已保留进度。")

    def reach_creation(self):
        """Advance toward a page outcome, not a compulsory button caption."""
        goal = "流程：打开单商品创建页"
        before, actions, history = page_key(self.page), [], []
        ready = lambda: self.creation_state() in ("upload", "titles", "editor")
        if self.memory:
            for recipe in self.memory.recipes(goal, before):
                if recipe.get("failures", 0) >= 3:
                    continue
                try:
                    replay(
                        self.page,
                        recipe["actions"],
                        cancel=self.cancel,
                        observe=lambda: (self.creation_state(), self.page)[1],
                        done=ready,
                    )
                    self.page.wait_for_timeout(400)
                    if ready():
                        self.log("已按记忆恢复流程：打开单商品创建页")
                        return
                except Cancelled:
                    raise
                except Exception as exc:
                    history.append("旧记忆需按当前页面调整：" + str(exc)[:120])
                self.memory_failed(recipe)
        attempted, ai_calls, human_steps = set(), 0, 0
        loading_since = None
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            self.check()
            state = self.creation_state()
            if ready():
                if self.memory and actions:
                    try:
                        self.memory.remember(
                            goal,
                            before,
                            actions,
                            source="流程决策",
                            outcome=page_key(self.page),
                        )
                    except Exception as exc:
                        self.log("流程记忆暂不可写：" + str(exc))
                self.confirm_memory()
                return
            if state == "loading":
                loading_since = loading_since or time.monotonic()
                if time.monotonic() - loading_since < 3:
                    self.page.wait_for_timeout(500)
                    continue
            else:
                loading_since = None
            # Cheap obvious paths first; every attempted action is followed by
            # fresh state observation and is not blindly repeated.
            option = self.visible_control(
                re.compile(r"^(单商品|单个商品|独立商品|单品商品)$")
            )
            entry = self.visible_control(
                re.compile(r"^(新建商品|创建商品|新增商品|创建货品)[\s⌄∨▼▾]*$")
            )
            target, kind = (option, "click") if option is not None else (entry, "hover")
            if target is not None:
                descriptor = target.evaluate(DESCRIBE)
                identity = (kind, str(descriptor))
                if identity in attempted and option is None:
                    kind, identity = "click", ("click", str(descriptor))
                if identity not in attempted:
                    attempted.add(identity)
                    safe_target(target)
                    actions.append({"kind": kind, "target": descriptor})
                    if kind == "hover":
                        target.hover(timeout=4000)
                    else:
                        target.click(timeout=5000)
                    self.page.wait_for_timeout(400)
                    history.append({"action": kind, "text": descriptor["text"]})
                    continue
            error = "当前页面尚未进入创建流程"
            if self.client is not None and ai_calls < 10:
                ai_calls += 1
                try:
                    context = self.ai_context(goal)
                    context["观察状态"] = state
                    context["本轮已执行"] = history[-10:]
                    result = PageAI(
                        self.page,
                        self.client,
                        self.cancel,
                        self.log,
                        trace=actions,
                        context=context,
                    ).find(
                        "进入可上传本款主图的单商品创建流程，或已生成的当前商品编辑页；选择当前有效的下一步，不要求固定按钮文字",
                        navigate=True,
                        history=history[-10:],
                    )
                    history.append(result)
                    self.log(
                        "AI 下一步："
                        + str(result.get("reason") or result.get("action"))
                    )
                    if len(history) < 3 or history[-1] != history[-2]:
                        continue
                    error = "连续动作未推进，改由用户指导"
                except Cancelled:
                    raise
                except Exception as exc:
                    error = str(exc)
                    history.append({"error": error[:160]})
            if not self.guide or human_steps >= 6:
                raise ListingError(error + "；创建流程未推进，当前款已停止。")
            self.discard_pending()
            self.log("等待用户操作创建入口或悬浮菜单：" + error)
            # Let the real click happen, then observe its result. Do not replay
            # an intercepted submenu choice as a hover on the old parent.
            _, guided = self.guide.run(
                self.page,
                "进入单商品创建流程；可悬停展开菜单，再点击有效入口",
                mode="record",
                condition=ready,
                observe=lambda: (self.creation_state(), self.page)[1],
                allow_partial=True,
            )
            human_steps += 1
            actions = guided if human_steps == 1 else actions + guided
            history.append({"action": "用户已操作", "页面状态": self.creation_state()})
            ai_calls = max(0, ai_calls - 2)
        raise ListingError("创建流程长时间未推进，请检查页面或重新指导。")

    def create(self, product):
        self.session.goto(ENTRY_URL)
        self.popups = []

        def capture(page):
            self.popups.append(page)

        self.session.context.on("page", capture)
        uploaded = False
        try:
            self.reach_creation()
            state = self.creation_state()
            if state == "upload":
                local = self.control("本地上传")
                with self.page.expect_file_chooser(timeout=15000) as chooser:
                    self.record_action(local)
                    local.click(timeout=8000)
                chooser.value.set_files([str(path) for path in product.main])
                uploaded = True
                self.wait(
                    lambda: not self.dom("busy")
                    and (
                        self.visible_control(re.compile("下一步")) is None
                        or self.visible_control(re.compile("下一步")).is_enabled()
                    ),
                    "商品主图上传未完成",
                    timeout=90,
                )
                self.phase = "800主图已上传，进入网站推荐标题或商品编辑页"
                self.click(re.compile(r"^下一步.*"))
                self.navigation_wait(
                    lambda: self.creation_state() in ("titles", "editor"),
                    "等待网站生成标题或编辑页",
                    timeout=180,
                )
            state = self.creation_state()
            if state == "titles":
                recommendation = self.dom("recommendations")[0]
                self.page.locator(recommendation["selector"]).click()
                self.phase = "已选择网站第一个推荐标题，需要继续生成商品编辑页"
                self.click(re.compile(r"^确认信息.*继续生成$|^继续生成$"))
                self.navigation_wait(
                    lambda: self.creation_state() == "editor",
                    "进入商品编辑页",
                    timeout=180,
                )
            if self.creation_state() != "editor":
                raise ListingError("创建流程尚未到达商品编辑页")
            self.editor_url = self.page.url
            if not uploaded:
                self.tab("图文信息")
                self.replace_images(("主图",), product.main)
            self.confirm_memory()
        finally:
            self.session.context.remove_listener("page", capture)

    def resume(self, url):
        self.session.goto(url)
        self.wait(
            lambda: bool(self.dom("section", "主图3:4")),
            "旧任务编辑页无法恢复，请核对记录后重试",
        )
        self.editor_url = self.page.url

    def specs(self, label):
        selector = self.section(label)
        records = self.dom("specs", selector=selector) or []
        if not records:
            raise ListingError(f"无法读取页面的{label}选项。")
        return selector, records

    def fill_sku(self, sku, require_same=False):
        selector = self.section("货号")
        control = self.dom("input", selector=selector)
        if not control:
            target = self.control("定位货号输入框", self.page.locator(selector))
            if not target.is_editable():
                raise ListingError("所选位置不是可编辑的货号输入框")
            control = target.evaluate(
                "e=>{if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id=\"'+e.dataset.glId+'\"]'}"
            )
            self.record_action(target)
        if require_same and self.page.locator(control).input_value() not in ("", sku):
            raise ListingError("恢复页面已有不同货号，禁止覆盖，请核对任务记录。")
        self.page.locator(control).fill(sku)
        if self.page.locator(control).input_value() != sku:
            raise ListingError("货号写入未确认。")
        return control

    def ship_48h(self):
        self.tab("价格库存")
        spot = self.page.get_by_role("radio", name="现货", exact=True)
        if spot.count() == 1:
            spot.check()
        else:
            self.click("现货")
        radio = self.page.get_by_role("radio", name="48小时", exact=True)
        if radio.count() == 1:
            radio.check()
        else:
            self.click("48小时")

    def set_color_photos(self, analysis):
        for color, path in analysis.colors.items():
            _, records = self.specs("颜色分类")
            record = next((row for row in records if row["name"] == color), None)
            if not record:
                raise ListingError(f"页面颜色 {color} 已变化，停止当前货号。")
            self.clear(record["row"])
            self.upload(record["row"], [path])

    def set_sizes(self, sizes):
        desired = {normalize_size(row["name"]): row["weight"] for row in sizes}
        selector, records = self.specs("尺码大小")
        for record in records:
            if normalize_size(record["name"]) not in desired:
                if not record["remove"]:
                    raise ListingError("未识别到多余尺码的删除控件。")
                self.page.locator(record["remove"]).click()
                self.confirm_modal(("确定", "确认", "删除"))
        for name in desired:
            selector, records = (
                self.specs("尺码大小")
                if self.dom("specs", selector=selector)
                else (selector, [])
            )
            if any(normalize_size(row["name"]) == name for row in records):
                continue
            scope = self.page.locator(selector)
            entry = scope.get_by_placeholder(
                re.compile("请选择.*尺码大小|请选择尺码|输入尺码")
            )
            if not entry.count():
                entry = scope.get_by_role("combobox").last
            else:
                entry = entry.first
            if not entry.count():
                entry = self.control("定位新增尺码的选择框", scope)
                self.record_action(entry)
            if entry.evaluate("(e)=>e.tagName") == "SELECT":
                choices = entry.locator("option").evaluate_all(
                    "(options)=>options.map(e=>({value:e.value,label:e.textContent}))"
                )
                matches = [
                    option
                    for option in choices
                    if normalize_size(option["label"]) == name
                ]
                if len(matches) != 1:
                    raise ListingError(f"未找到唯一可选尺码 {name}。")
                entry.select_option(matches[0]["value"])
                continue
            entry.click()
            if entry.evaluate("(e)=>e.tagName") == "INPUT" and entry.is_editable():
                entry.fill(name)
            options = self.page.get_by_role("option")
            matches = []
            for i in range(options.count()):
                option = options.nth(i)
                if option.is_visible() and normalize_size(option.inner_text()) == name:
                    matches.append(option)
            if len(matches) != 1:
                option = self.control("选择尺码选项：" + name)
                self.record_action(option)
                option.click()
            else:
                matches[0].click()
            self.page.keyboard.press("Escape")
        _, records = self.specs("尺码大小")
        if {normalize_size(row["name"]) for row in records} != set(desired):
            raise ListingError("页面尺码与识别结果不一致。")
        for record in records:
            weight = desired[normalize_size(record["name"])]
            self.page.locator(record["note"]).fill(weight)
            if self.page.locator(record["note"]).input_value() != weight:
                raise ListingError("体重推荐写入失败。")

    def parse_size_chart(self, paths, temp):
        self.click(re.compile(r"^AI\s*解析尺码图$|^AI\s*识别尺码表$"))
        self.wait(
            lambda: any(
                self.page.get_by_role("dialog").nth(i).is_visible()
                for i in range(self.page.get_by_role("dialog").count())
            ),
            "网站尺码图识别窗口未打开",
        )
        dialogs = self.page.get_by_role("dialog")
        dialog = next(
            dialogs.nth(i)
            for i in range(dialogs.count())
            if dialogs.nth(i).is_visible()
        )
        selector = dialog.evaluate(
            '(e)=>{e.dataset.glSize="active";return "[data-gl-size=active]"}'
        )
        if len(paths) > 1:
            images = []
            for path in paths:
                with Image.open(path) as original:
                    image = ImageOps.exif_transpose(original).convert("RGB")
                    image.thumbnail((1800, 12000))
                    images.append(image)
            if sum(image.height for image in images) > 24000:
                raise ListingError("尺码图总高度过大，请合并为清晰的单张尺码表。")
            canvas = Image.new(
                "RGB",
                (max(i.width for i in images), sum(i.height for i in images)),
                "white",
            )
            y = 0
            for image in images:
                canvas.paste(image, (0, y))
                y += image.height
            merged = Path(temp) / "尺码合并.jpg"
            canvas.save(merged, quality=95)
            paths = [merged]
        self.upload(selector, paths)
        for text in ("开始识别", "识别", "解析", "确认识别"):
            button = dialog.get_by_role("button", name=text, exact=True)
            if button.count() == 1 and button.is_visible():
                button.click()
                break
        self.wait(
            lambda: any(
                dialog.get_by_role("button", name=text, exact=True).count()
                and dialog.get_by_role("button", name=text, exact=True).is_visible()
                for text in ("使用识别结果", "确认填入", "应用", "确定")
            )
            or not dialog.is_visible(),
            "网站尺码图识别未完成",
            timeout=180,
        )
        if dialog.is_visible():
            self.confirm_modal(("使用识别结果", "确认填入", "应用", "确定"))
        self.wait(lambda: not dialog.is_visible(), "网站尺码识别结果尚未应用")

    def batch_values(self, price, inventory):
        selector = self.section("价格与库存")
        scope = self.page.locator(selector)
        price_input = scope.get_by_placeholder("价格", exact=True)
        stock_input = scope.get_by_placeholder(re.compile("现货库存|请输入库存|库存"))
        if price_input.count() != 1 or stock_input.count() != 1:
            raise ListingError("未识别到唯一的价格/现货库存批量输入框。")
        rows = self.dom("sku_rows", selector=selector) or []
        if not rows:
            raise ListingError("无法读取 SKU 表的价格与库存列。")
        try:
            amounts = {float(row["stock"]) for row in rows}
        except ValueError:
            raise ListingError("无法确认当前现货库存，未执行增减。") from None
        stock_selector = stock_input.evaluate(
            '(e)=>{e.dataset.glStock="batch";return "[data-gl-stock=batch]"}'
        )
        mode = self.dom("stock_mode", selector=stock_selector)
        price_input.fill(str(price))
        if mode and len(amounts) == 1:
            current = next(iter(amounts))
            delta = inventory - current
            if delta:
                self.click("增" if delta > 0 else "减", self.page.locator(mode))
                stock_input.fill(str(int(abs(delta))))
            else:
                stock_input.fill("")
        elif not mode:
            stock_input.fill(str(inventory))
        else:
            stock_input.fill("")
        self.click("批量设置", scope)
        if mode and len(amounts) > 1:
            for row in self.dom("sku_rows", selector=selector):
                current = float(row["stock"])
                delta = inventory - current
                if not delta:
                    continue
                cell = self.page.locator(row["stockCell"])
                target = cell.locator("input:not([type=hidden])")
                if target.count() != 1:
                    raise ListingError(
                        "不同 SKU 的库存需要逐行调整，但页面控件不唯一。"
                    )
                self.click("增" if delta > 0 else "减", cell)
                target.fill(str(int(abs(delta))))
                target.press("Enter")
                target.blur()

        def matched():
            values = self.dom("sku_rows", selector=selector) or []
            try:
                return bool(values) and all(
                    float(row["price"]) == float(price)
                    and float(row["stock"]) == inventory
                    for row in values
                )
            except ValueError:
                return False

        self.wait(matched, "部分 SKU 未确认目标价格与库存，未保存草稿", timeout=15)
        return len(self.dom("sku_rows", selector=selector))

    def verify(self, product, analysis, sku_control, price, inventory):
        self.tab("属性信息")
        sku_control = self.dom("input", selector=self.section("货号"))
        if (
            not sku_control
            or self.page.locator(sku_control).input_value() != product.sku
        ):
            raise ListingError("最终货号校验失败，禁止保存，避免串款。")
        self.tab("价格库存")
        _, colors = self.specs("颜色分类")
        if {r["name"] for r in colors} != set(analysis.colors) or any(
            r["photos"] != 1 for r in colors
        ):
            raise ListingError("颜色规格图未全部确认。")
        _, sizes = self.specs("尺码大小")
        expected = {normalize_size(s["name"]): s["weight"] for s in analysis.sizes}
        if {normalize_size(r["name"]) for r in sizes} != set(expected):
            raise ListingError("最终尺码校验失败。")
        for row in sizes:
            if (
                self.page.locator(row["note"]).input_value()
                != expected[normalize_size(row["name"])]
            ):
                raise ListingError("体重备注校验失败。")
        rows = self.dom("sku_rows", selector=self.section("价格与库存")) or []
        if len(rows) != len(colors) * len(sizes) or any(
            float(row["price"]) != float(price) or float(row["stock"]) != inventory
            for row in rows
        ):
            raise ListingError("SKU 行数或最终价格库存未完整确认。")
        radio = self.page.get_by_role("radio", name="48小时", exact=True)
        if radio.count() != 1 or not radio.is_checked():
            raise ListingError("发货时间未确认是 48 小时。")

    def save_draft(self):
        self.check()
        button = self.control("保存草稿", allow_save=True)
        success = re.compile("草稿保存成功|保存成功|已保存至草稿箱|已保存到草稿箱")
        existing = self.page.get_by_text(success)
        if any(existing.nth(i).is_visible() for i in range(existing.count())):
            raise ListingError("页面残留旧的保存回执，请重新打开当前任务后重试。")
        try:
            self.record_action(button)
            button.click()
            self.confirm_modal(("保存草稿", "保存", "确定"))
            self.wait(
                lambda: any(
                    self.page.get_by_text(success).nth(i).is_visible()
                    for i in range(self.page.get_by_text(success).count())
                ),
                "未检测到保存成功回执",
                timeout=45,
            )
        except Exception:
            raise SaveUncertain(
                "保存草稿已点击，但未取得明确成功回执；请到草稿箱核对，禁止自动重复创建。"
            ) from None
        return self.page.url
