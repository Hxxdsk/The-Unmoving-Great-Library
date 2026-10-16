from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageOps
from playwright.sync_api import Error as BrowserError

from .models import ListingError, Cancelled, SaveUncertain
from .browser import ENTRY_URL
from .vision import normalize_size
from .page_ai import PageAI, FORBIDDEN
from .spec_reader import read_rows, SCRIPT as SPEC_SNAPSHOT
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
        self.details_prepared = False
        self.batch_applied = False
        self.generation_pending = False
        self.spec_cache = {}
        self.spec_layouts = {}
        self.client = client
        self.memory, self.guide = memory, guide
        self.pending = []
        self.last_plan = None
        self.popups = []
        self.creation_pages = set(session.context.pages)
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
        next_report = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.check()
            if time.monotonic() >= next_report:
                self.log("仍在等待页面处理：" + message)
                next_report = time.monotonic() + 15
            try:
                if condition():
                    self.confirm_memory()
                    return
            except BrowserError as exc:
                if self.page.is_closed() or not re.search(
                    r"Execution context was destroyed|Cannot find context|Frame was detached|not attached|detached from|Cannot adopt",
                    str(exc),
                    re.I,
                ):
                    raise
                # React/navigation can invalidate one observation, not the task.
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
            "完整流程": "每款新开工作页 → 单商品入口（可能新开标签） → 本地上传800前5张 → 生成前详情上传整个颜色文件夹（包含多色对照图） → 填用户标题，不选推荐 → 继续生成编辑页 → 从上往下替换3:4、白底、全部详情、水洗标 → 当前货号 → 48小时 → 按网站颜色与多色对照图匹配单色图到右侧图片框 → 使用颜色目录中尺码图识别已有尺码的右侧备注体重xx-xx斤，不再上传尺码图 → 价格399、现货1000 → 校验 → 返回顶部，选择商品标题AI推荐下拉框的第一个标题 → 保存草稿并确认成功 → 新开下一款并关闭旧页",
            "当前阶段": self.phase,
            "本次目的": goal,
            "操作原则": "根据实际页面决定入口；名字可以不同。菜单已展开就选子项，不反复寻找或点击上级按钮。悬停出现的弹层也属于当前页面。不得发布或改动其他商品。",
            "成功经验": (
                self.memory.hints(goal, page_key(self.page)) if self.memory else []
            ),
        }

    def control(self, text, root=None, allow_save=False, allow_guidance=True):
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
                    if not self.guide or not allow_guidance:
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

    def interaction_point(self, target):
        for _ in range(3):
            self.check()
            target.scroll_into_view_if_needed(timeout=2500)
            selector = target.evaluate(
                "e=>{if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id=\"'+e.dataset.glId+'\"]'}"
            )
            state = self.dom("interaction_guard", selector=selector) or {}
            if state.get("point"):
                return state["point"]
            if state.get("close"):
                self.log("收起遮挡操作控件的商品预览侧栏")
                self.page.locator(state["close"]).click(timeout=2000)
                self.page.wait_for_timeout(200)
                continue
            raise ListingError(
                "当前控件被商品预览侧栏或其他浮层遮挡，未穿透点击；请收起遮挡面板"
            )
        raise ListingError("商品预览面板尚未收起，请关闭该预览后重试")

    def click_field(self, target):
        point = self.interaction_point(target)
        target.click(position=point, timeout=2500)

    def hover_in_field(self, target, field):
        """Hover the real thumbnail/upload hit area, including its own overlay."""
        target.scroll_into_view_if_needed(timeout=4000)
        guard = target.evaluate(
            "e=>{if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id=\"'+e.dataset.glId+'\"]'}"
        )
        if (self.dom("interaction_guard", selector=guard) or {}).get("close"):
            self.interaction_point(target)
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

    def upload(self, selector, paths, trigger=None):
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
            target = (
                trigger
                if trigger is not None
                else self.visible_control(
                    re.compile(r"^(上传图片|上传主图|上传辅助图|图片|本地上传)$"), scope
                )
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

    def replace_images_in_page_order(self, fields):
        for names, paths in self.fields_in_page_order(fields):
            self.replace_images(names, paths)

    def fields_in_page_order(self, fields):
        entries = []
        for names, paths in fields:
            try:
                if not paths:
                    self.log(f"跳过图片字段 {'/'.join(names)}：没有图片")
                    continue
                entries.append((names, self.section(*names), paths))
            except Cancelled:
                raise
            except Exception as exc:
                self.log(f"跳过图片字段 {'/'.join(names)}：{exc}")
        positions = self.page.evaluate(
            "selectors=>selectors.map(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {y:r.y,x:r.x};})",
            [e[1] for e in entries],
        )
        for index in sorted(
            range(len(entries)),
            key=lambda i: (round(positions[i]["y"] / 8), positions[i]["x"]),
        ):
            names, _, paths = entries[index]
            # Uploads can remount adjacent React fields; resolve each fresh.
            yield names, paths

    def dismiss_optional_dialogs(self):
        for dialog in self.page.get_by_role("dialog").all():
            if not dialog.is_visible():
                continue
            close = dialog.get_by_role(
                "button", name=re.compile(r"^(取消|关闭|返回|Close)$", re.I)
            )
            if close.count() == 1:
                close.click(timeout=2000)
            else:
                dialog.press("Escape", timeout=2000)
            if dialog.is_visible():
                raise ListingError("当前图片弹窗无法关闭，跳过本款以免误操作后续字段。")

    def reconcile_sizes_after_chart(self, sizes):
        desired = {normalize_size(r["name"]): r["weight"] for r in sizes if r["weight"]}
        _, records = self.specs("尺码大小", allow_empty=True)
        if any(
            normalize_size(r["name"]) in desired
            and self.page.locator(r["note"]).input_value()
            != desired[normalize_size(r["name"])]
            for r in records
        ):
            self.set_sizes(sizes)

    def creation_state(self):
        try:
            return self._creation_state()
        except Cancelled:
            raise
        except Exception as exc:
            if any(
                text in str(exc)
                for text in (
                    "Execution context was destroyed",
                    "Cannot find context",
                    "Frame was detached",
                )
            ):
                return "loading"
            raise

    def _creation_state(self):
        # A navigation can advance directly, or open a new tool-owned tab.
        candidates = list(
            dict.fromkeys(
                self.popups
                + [
                    p
                    for p in self.session.context.pages
                    if p not in self.creation_pages
                ]
            )
        )
        for candidate in candidates:
            if (
                not candidate.is_closed()
                and "fxg.jinritemai.com" == urlparse(candidate.url).hostname
            ):
                if candidate is not self.page:
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
        if (
            self.dom("creation_upload")
            or self.visible_control(re.compile(r"^下一步.*生成商品")) is not None
        ):
            return "upload"
        if self.dom("recommendations") or (
            self.visible_control(re.compile(r"确认信息.*继续生成|^继续生成$"))
            is not None
        ):
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
        if self.generation_pending:
            # A submitted generation can take minutes. Observe its result before
            # replaying memory or asking AI to navigate/click generation again.
            try:
                self.wait(condition, message, timeout=timeout)
                return
            except Cancelled:
                raise
            except ListingError as exc:
                detail = self.dom("upload_errors") or str(exc)
                raise ListingError(
                    "生成请求已提交但页面尚未完成："
                    + detail
                    + "；保留当前页，未重复生成"
                ) from exc
            finally:
                self.generation_pending = False
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
        transient_failures = {}
        loading_since = None
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = self.creation_state()
            self.check()
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
                try:
                    descriptor = target.evaluate(DESCRIBE, timeout=1500)
                except Exception:
                    self.page.wait_for_timeout(100)
                    continue
                identity = (kind, str(descriptor))
                if identity in attempted and option is None:
                    kind, identity = "click", ("click", str(descriptor))
                if identity not in attempted:
                    attempted.add(identity)
                    safe_target(target)
                    actions.append({"kind": kind, "target": descriptor})
                    try:
                        if kind == "hover":
                            target.hover(timeout=1500)
                        else:
                            # A hover portal can collapse or animate as the
                            # pointer crosses its gap. Use the visible item's
                            # own click handler after normal pointer retries.
                            target.click(timeout=1500)
                    except Cancelled:
                        raise
                    except Exception as exc:
                        if ready():
                            continue
                        transient_failures[identity] = (
                            transient_failures.get(identity, 0) + 1
                        )
                        self.log(
                            f"创建入口暂时不可点，重新检查页面：{str(exc).splitlines()[0]}"
                        )
                        if option is not None:
                            if entry is not None:
                                try:
                                    entry.hover(timeout=1000)
                                except Exception:
                                    pass
                            fresh = self.visible_control(
                                re.compile(r"^(单商品|单个商品|独立商品|单品商品)$")
                            )
                            if fresh is not None and fresh.is_enabled():
                                try:
                                    safe_target(fresh)
                                    fresh.evaluate(
                                        "e=>{if(!e.isConnected||!e.getClientRects().length||getComputedStyle(e).visibility==='hidden')throw new Error('菜单已收起');e.click()}"
                                    )
                                    self.log("已通过当前可见单商品菜单继续")
                                except Exception:
                                    if not ready() and transient_failures[identity] < 3:
                                        attempted.discard(identity)
                            elif transient_failures[identity] < 3:
                                attempted.discard(identity)
                        self.page.wait_for_timeout(250)
                        continue
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

    def click_generation(self, pattern, destinations, title_text=None):
        """Wait for validation and rerenders before a single generation action."""
        clicked_at, attempts, repairs = 0.0, 0, 0
        started = time.monotonic()

        def progress():
            nonlocal clicked_at, attempts, repairs
            if self.creation_state() in destinations:
                return True
            button = self.visible_control(pattern)
            if button is None:
                return False
            if title_text is not None:
                inputs = self.page.get_by_placeholder(re.compile(r"标题|2.60|1.30"))
                inputs = [e for e in inputs.all() if e.is_visible() and e.is_editable()]
                if len(inputs) == 1:
                    title = inputs[0]
                    lost = title.input_value() != title_text
                    recommit = (
                        not button.is_enabled()
                        and repairs == 0
                        and time.monotonic() - started > 1
                        and not self.dom("busy")
                    )
                    if lost or recommit:
                        if repairs >= 3:
                            raise ListingError(
                                "页面反复重置商品标题，请检查标题校验或上传错误提示"
                            )
                        title.fill("")
                        title.fill(title_text)
                        title.press("Tab")
                        repairs += 1
                        self.log("已重新提交本款手填标题，等待生成按钮可用")
                        return False
            if not button.is_enabled() or self.dom("busy"):
                return False
            if clicked_at:
                # Let the existing request advance; do not generate twice.
                return True
            if attempts >= 3:
                raise ListingError("生成入口多次变化，请检查当前页面上传和标题校验")
            safe_target(button)
            attempts += 1
            try:
                button.click(timeout=1500)
                clicked_at = time.monotonic()
                self.generation_pending = True
                return True
            except Exception as exc:
                if self.creation_state() in destinations:
                    return True
                self.log("生成按钮状态已变化，重新检查：" + str(exc).splitlines()[0])
                return False

        self.log("等待图片上传和标题校验完成，再继续生成")
        try:
            self.wait(progress, "生成按钮仍不可用", timeout=90)
        except Cancelled:
            raise
        except ListingError as exc:
            errors = self.page.locator(
                "[role=alert],[aria-invalid=true],[class*=error],[class*=Error],[class*=explain]"
            ).evaluate_all(
                "nodes=>[...new Set(nodes.filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden').map(e=>(e.innerText||e.getAttribute('aria-label')||'').trim()).filter(t=>t&&t.length<200))].slice(0,6).join('；')"
            )
            raise ListingError(
                f"继续生成尚未就绪：{errors or str(exc)}；请检查图片是否上传成功及商品标题格式，未强制点击禁用按钮"
            ) from exc

    def upload_creation_main(self, paths):
        """Submit once when accepted; retry only an empty/failed upload field."""
        paths = [str(path) for path in paths]
        submitted = False
        baseline_photos = 0
        last_error = "尚未找到上传控件"
        for attempt in range(3):
            self.check()
            if self.creation_state() in ("titles", "editor"):
                return
            files = self.dom("creation_files")
            if files and files["selected"]:
                submitted = True
            if not submitted:
                try:
                    if files:
                        baseline_photos = files.get("photos", 0)
                        self.log(f"使用当前创建页文件控件上传 {len(paths)} 张主图")
                        self.page.locator(files["selector"]).set_input_files(
                            paths, timeout=8000
                        )
                    else:
                        local = self.dom("creation_upload")
                        target = (
                            self.page.locator(local)
                            if local
                            else self.control("本地上传")
                        )
                        with self.page.expect_file_chooser(timeout=8000) as chooser:
                            self.click_field(target)
                        chooser.value.set_files(paths)
                    submitted = True
                except Cancelled:
                    raise
                except Exception as exc:
                    last_error = str(exc).splitlines()[0]
                    self.log("上传入口暂时变化，重读当前创建页：" + last_error)
                    self.cancel.wait(0.5)
                    continue

            def ready():
                state = self.creation_state()
                if state in ("titles", "editor"):
                    return True
                button = self.visible_control(re.compile(r"^下一步.*"))
                return (
                    button is not None and button.is_enabled() and not self.dom("busy")
                )

            try:
                self.wait(ready, "主图仍在上传或校验", timeout=60)
                return
            except Cancelled:
                raise
            except ListingError as exc:
                last_error = str(exc)
                files = self.dom("creation_files")
                errors = self.dom("upload_errors") or ""
                if errors:
                    self.log("创建页上传提示：" + errors)
                # A selected file or an active upload is evidence of acceptance.
                # Keep polling it instead of uploading another duplicate batch.
                submitted = bool(
                    (
                        files
                        and (
                            files["selected"]
                            or files.get("photos", 0) > baseline_photos
                        )
                    )
                    or self.dom("busy")
                )
                self.log(f"主图尚未就绪，保留当前页继续观察（{attempt + 1}/3）")
        raise ListingError(
            "创建页主图上传未完成：" + (self.dom("upload_errors") or last_error)
        )

    def create(self, product):
        if not 2 <= len(product.title.strip()) <= 60:
            raise ListingError("请在货号旁填写2-60字商品标题。")
        self.creation_pages = set(self.session.context.pages)
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
                self.upload_creation_main(product.main)
                uploaded = True
                self.phase = "800主图已上传，进入网站推荐标题或商品编辑页"
                if self.creation_state() == "upload":
                    self.click_generation(
                        re.compile(r"^下一步.*"), ("titles", "editor")
                    )
                self.navigation_wait(
                    lambda: self.creation_state() in ("titles", "editor"),
                    "等待网站生成标题或编辑页",
                    timeout=180,
                )
            state = self.creation_state()
            if state == "titles":
                self.phase = "生成前上传整个颜色文件夹（含多色对照图），填写用户标题"
                if product.images.get("颜色"):
                    self.replace_images(("商品详情",), product.images["颜色"])
                self.wait(
                    lambda: not self.dom("busy", selector=self.section("商品详情")),
                    "颜色图片尚未上传完成",
                    timeout=90,
                )
                title = self.page.get_by_placeholder(re.compile(r"标题|2.60|1.30"))
                if title.count() != 1:
                    title = self.control(
                        "定位商品标题输入框，填写用户提供的标题，不选择AI推荐标题"
                    )
                title.fill(product.title.strip())
                title.press("Tab")
                if title.input_value() != product.title.strip():
                    raise ListingError("用户商品标题未正确填写。")
                self.phase = "已填写用户标题，继续生成商品编辑页"
                self.click_generation(
                    re.compile(r"^确认信息.*继续生成$|^继续生成$"),
                    ("editor",),
                    title_text=product.title.strip(),
                )
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

    def ordered_spec_rows(self, records):
        positions = self.page.evaluate(
            "selectors=>selectors.map(s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y};})",
            [r["note"] for r in records],
        )
        return [
            records[i]
            for i in sorted(
                range(len(records)),
                key=lambda i: (round(positions[i]["y"] / 8), positions[i]["x"]),
            )
        ]

    def specs(self, label, allow_empty=False):
        selector, legacy = None, []
        try:
            selector = self.dom("section", label)
            legacy = (self.dom("specs", selector=selector) or []) if selector else []
        except ListingError:
            pass
        # Visible row geometry handles separate dropdown/note/icon wrappers,
        # selected values stored in inputs, and header-only legacy scopes.
        snapshot = self.page.evaluate(SPEC_SNAPSHOT, label)
        deadline = time.monotonic() + (0.5 if allow_empty else 2.5)
        previous = None
        while time.monotonic() < deadline:
            self.check()
            signature = tuple(r["note"] for r in snapshot.get("rows", []))
            if signature and (
                signature == previous or signature == self.spec_layouts.get(label)
            ):
                break
            previous = signature
            self.page.wait_for_timeout(150)
            snapshot = self.page.evaluate(SPEC_SNAPSHOT, label)
        self.spec_layouts[label] = tuple(r["note"] for r in snapshot.get("rows", []))
        expected = len(snapshot.get("rows", []))
        region, layout = read_rows(
            self.page,
            label,
            None,
            self.cancel,
            self.log,
            self.spec_cache,
            snapshot=snapshot,
        )
        # An incomplete legacy result must not win simply because unknown OCR
        # rows have not returned names yet. Bind by actual remark controls too.
        expected_notes = {r["note"] for r in snapshot.get("rows", [])}
        if (
            legacy
            and len(legacy) == expected
            and expected_notes == {r["note"] for r in legacy}
            and len({r["name"] for r in legacy}) == expected
            and all(
                not r["name"].strip().isdigit() or label != "颜色分类" for r in legacy
            )
            and not layout
        ):
            return selector, self.ordered_spec_rows(legacy)
        if layout:
            self.log(
                f"按实际布局读取{label}：{len(layout)} 项，"
                + " / ".join(r["name"] for r in layout)
            )
            return region, self.ordered_spec_rows(layout)
        if self.client is not None:
            region, layout = read_rows(
                self.page,
                label,
                self.client,
                self.cancel,
                self.log,
                self.spec_cache,
                snapshot=snapshot,
            )
            if layout:
                return region, self.ordered_spec_rows(layout)
        if allow_empty and (selector or region):
            return selector or region, []
        raise ListingError(
            f"无法读取页面的{label}选项；已尝试控件结构、实际布局及 AI 读取。"
        )

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

    def ensure_color_first(self):
        """Move the website's color specification using its own header control."""
        for _ in range(5):
            state = self.dom("color_spec_order")
            if not state or state["first"]:
                return
            if not state.get("move"):
                raise ListingError("颜色规格在下方，但未找到该区域右侧的上移按钮。")
            self.log("颜色规格在下方，点击该区域上移并确认")

            def accept_confirm(dialog):
                if dialog.type == "confirm":
                    dialog.accept()

            self.page.on("dialog", accept_confirm)
            try:
                self.click_field(self.page.locator(state["move"]))

                # The confirmation may be rendered after the click returns.
                def moved():
                    self.confirm_modal(("确定", "确认", "继续"))
                    current = self.dom("color_spec_order")
                    return current and (
                        current["first"]
                        or current["before"] < state["before"]
                        or current["top"] < state["top"] - 2
                    )

                self.wait(moved, "颜色规格上移后顺序尚未改变", timeout=6)
            finally:
                self.page.remove_listener("dialog", accept_confirm)
        raise ListingError("颜色规格尚未移动到第一项。")

    def set_color_photos(self, analysis):
        self.ensure_color_first()
        _, initial = self.specs("颜色分类")
        for color in list(analysis.colors):
            if color not in {r["name"] for r in initial}:
                self.log(f"跳过颜色 {color}：网页已无该项")
                analysis.colors.pop(color)
                if color not in analysis.skipped_colors:
                    analysis.skipped_colors.append(color)
        for color in [r["name"] for r in initial if r["name"] in analysis.colors]:
            path = analysis.colors[color]
            _, records = self.specs("颜色分类")
            record = next((row for row in records if row["name"] == color), None)
            if not record:
                self.log(f"跳过颜色 {color}：页面已变化，继续处理其他颜色")
                analysis.colors.pop(color, None)
                analysis.skipped_colors.append(color)
                continue
            try:
                field = record.get("photo") or record["row"]
                self.clear(field)
                # Deleting the old thumbnail may remount the row. Bind again
                # by the displayed colour, never reuse an old upload tile.
                _, refreshed = self.specs("颜色分类")
                records = [r for r in refreshed if r["name"] == color]
                if len(records) != 1:
                    raise ListingError("清除旧图后颜色行已变化，跳过该色")
                record = records[0]
                field = record.get("photo") or record["row"]
                trigger = self.page.locator(field) if record.get("photo") else None
                if trigger is None and not self.dom("file_inputs", selector=field):
                    photo = self.dom("spec_photo_trigger", selector=record["row"])
                    trigger = (
                        self.page.locator(photo)
                        if photo
                        else self.control(
                            f"定位颜色 {color} 这一行备注右侧的图片框，悬停后选择本地上传；不要选颜色下拉框或删除按钮",
                            self.page.locator(record["row"]),
                        )
                    )
                self.upload(field, [path], trigger=trigger)
                self.log(f"已按当前颜色行上传：{color} → {path.name}")
            except Cancelled:
                raise
            except Exception as exc:
                self.log(f"跳过颜色 {color} 上传：{exc}")
                analysis.colors.pop(color, None)
                if color not in analysis.skipped_colors:
                    analysis.skipped_colors.append(color)
                self.dismiss_optional_dialogs()

    def set_sizes(self, sizes):
        for attempt in range(3):
            try:
                return self._set_sizes(sizes)
            except Cancelled:
                raise
            except Exception as exc:
                if attempt == 2 or not any(
                    word in str(exc)
                    for word in ("Timeout", "detached", "预览", "已变化")
                ):
                    raise
                self.log("尺码控件受遮挡或页面重绘，重新读取尺码后继续")
                self.page.mouse.move(0, 0)
                self.page.wait_for_timeout(200)

    def _set_sizes(self, sizes):
        desired = {
            normalize_size(row["name"]): row["weight"] for row in sizes if row["weight"]
        }
        _, records = self.specs("尺码大小", allow_empty=True)
        existing = {normalize_size(row["name"]) for row in records}
        for name in desired.keys() - existing:
            self.log(f"跳过体重备注 {name}：网页尺码区域没有该项")
        for name in existing - desired.keys():
            self.log(f"保留网页尺码 {name}：图片无对应数据，不修改备注")
        desired = {name: weight for name, weight in desired.items() if name in existing}
        for name, weight in desired.items():
            # Re-read after every write: controlled inputs may rebuild the row.
            _, current = self.specs("尺码大小", allow_empty=True)
            matches = [r for r in current if normalize_size(r["name"]) == name]
            if len(matches) != 1:
                raise ListingError(f"无法唯一定位尺码 {name} 右侧的备注框。")
            note = self.page.locator(matches[0]["note"])
            if note.input_value() != weight:
                note.evaluate(
                    """(e,value)=>{
                    if(!/^(INPUT|TEXTAREA)$/.test(e.tagName)||e.disabled||e.readOnly||
                       !/备注/.test([e.placeholder,e.getAttribute('aria-label')].join(' ')))
                        throw new Error('不是可编辑的尺码备注框');
                    const proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
                    Object.getOwnPropertyDescriptor(proto,'value').set.call(e,value);
                    e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));
                    e.dispatchEvent(new Event('change',{bubbles:true}));
                    e.dispatchEvent(new FocusEvent('blur',{bubbles:true}));
                }""",
                    weight,
                )
            _, current = self.specs("尺码大小", allow_empty=True)
            matches = [r for r in current if normalize_size(r["name"]) == name]
            if (
                len(matches) != 1
                or self.page.locator(matches[0]["note"]).input_value() != weight
            ):
                raise ListingError(f"尺码 {name} 体重备注写入失败。")
            self.log(f"{name} 体重备注：{weight or '留空（无明确数据）'}")

    def try_parse_size_chart(self, paths, temp):
        """The website helper is optional; verified size/weight notes are not."""
        before = self.page.get_by_role("dialog").count()
        try:
            self.parse_size_chart(paths, temp)
            return True
        except Cancelled:
            raise
        except Exception as exc:
            self.log(f"尺码图辅助上传未完成，保留已填写的尺码与体重备注，继续：{exc}")
            # Only dismiss the dialog opened by this optional attempt.
            dialogs = self.page.get_by_role("dialog")
            for i in range(before, dialogs.count()):
                dialog = dialogs.nth(i)
                if not dialog.is_visible():
                    continue
                close = dialog.get_by_role(
                    "button", name=re.compile(r"^(取消|关闭|返回|Close)$", re.I)
                )
                if close.count() == 1:
                    close.click(timeout=2000)
                else:
                    dialog.press("Escape", timeout=2000)
                if dialog.is_visible():
                    raise ListingError(
                        "网站尺码识别窗口无法关闭，请关闭该窗口后重试。"
                    ) from exc
            return False

    def parse_size_chart(self, paths, temp):
        if not paths:
            raise ListingError("尺码文件夹没有可上传图片")
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
        target = self.visible_control(
            re.compile(r"(?:AI\s*)?(?:解析|识别)尺码(?:图|表|图片)")
        )
        if target is None:
            target = self.control(
                "定位尺码表区域的AI解析尺码图上传入口，上传尺码文件夹图片，不是跳转其他网站",
                allow_guidance=False,
            )
        choosers = []

        def capture_chooser(chooser):
            choosers.append(chooser)

        self.page.on("filechooser", capture_chooser)
        try:
            target.click(timeout=4000)
            deadline = time.monotonic() + 3
            dialog = None
            while time.monotonic() < deadline:
                self.check()
                if choosers:
                    choosers[0].set_files([str(p) for p in paths])
                    self.wait(
                        lambda: not self.dom("busy"), "尺码图尚未上传完成", timeout=60
                    )
                    self.log("尺码文件夹图片已上传到AI解析尺码图入口")
                    return
                visible = [
                    d for d in self.page.get_by_role("dialog").all() if d.is_visible()
                ]
                if visible:
                    dialog = visible[-1]
                    break
                self.page.wait_for_timeout(100)
            if dialog is None:
                selector = self.section("尺码表")
                self.upload(selector, paths, trigger=target)
                self.log("尺码文件夹图片已上传到尺码表区域")
                return
        finally:
            self.page.remove_listener("filechooser", capture_chooser)
        selector = dialog.evaluate(
            '(e)=>{e.dataset.glSize="active";return "[data-gl-size=active]"}'
        )
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
            timeout=60,
        )
        if dialog.is_visible():
            self.confirm_modal(("使用识别结果", "确认填入", "应用", "确定"))
        self.wait(lambda: not dialog.is_visible(), "网站尺码识别结果尚未应用")

    def price_inventory_fields(self):
        fields = self.dom("batch_price_stock")
        if fields:
            return fields
        raise ListingError(
            "未找到同一批量设置栏中的价格与现货库存输入框；请展开价格库存区域。未点击导航标签或其他字段"
        )

    def batch_values(self, price, inventory):
        self.batch_applied = False
        fields = self.price_inventory_fields()
        selector = fields["selector"]
        scope = self.page.locator(selector)
        price_input = (
            self.page.locator(fields["price"])
            if fields.get("price")
            else scope.get_by_placeholder("价格", exact=True)
        )
        stock_input = (
            self.page.locator(fields["stock"])
            if fields.get("stock")
            else scope.get_by_placeholder(re.compile("现货库存|请输入库存|库存"))
        )
        if price_input.count() != 1 or stock_input.count() != 1:
            raise ListingError("未识别到唯一的价格/现货库存批量输入框。")
        stock_selector = stock_input.evaluate(
            '(e)=>{e.dataset.glStock="batch";return "[data-gl-stock=batch]"}'
        )
        mode = self.dom("stock_mode", selector=stock_selector)
        amounts = set()
        if mode:
            # Older pages use stock deltas. Read available rows only to compute
            # that delta; virtualized or missing rows must not block the batch.
            try:
                amounts = {
                    float(row["stock"])
                    for row in (self.dom("sku_rows", selector=selector) or [])
                }
            except (ValueError, TypeError, KeyError):
                pass
        price_input.fill(str(price))
        if mode and len(amounts) == 1:
            current = next(iter(amounts))
            delta = inventory - current
            if delta:
                self.click("增" if delta > 0 else "减", self.page.locator(mode))
                stock_input.fill(str(int(abs(delta))))
            else:
                stock_input.fill("")
        else:
            stock_input.fill(str(inventory))
        price_input.blur()
        stock_input.blur()
        current = self.dom("batch_price_stock")
        if current:

            def batch_ready():
                refreshed = self.dom("batch_price_stock")
                return bool(
                    refreshed and self.page.locator(refreshed["button"]).is_enabled()
                )

            self.wait(
                batch_ready, "价格或库存尚未通过校验，批量设置按钮未启用", timeout=10
            )
            current = self.dom("batch_price_stock")
            self.click_field(self.page.locator(current["button"]))
        else:
            self.click("批量设置", scope)
        self.batch_applied = True
        self.log("已点击批量设置，继续下一步")
        return True

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
        matched = {r["name"]: r for r in colors}
        if not set(analysis.colors).issubset(matched) or any(
            matched[name]["photos"] != 1 for name in analysis.colors
        ):
            raise ListingError("已匹配颜色的规格图未确认。")
        _, sizes = self.specs("尺码大小")
        expected = {
            normalize_size(s["name"]): s["weight"]
            for s in analysis.sizes
            if s["weight"]
        }
        for row in sizes:
            if normalize_size(row["name"]) not in expected:
                continue
            if (
                self.page.locator(row["note"]).input_value()
                != expected[normalize_size(row["name"])]
            ):
                raise ListingError("体重备注校验失败。")
        if not self.batch_applied:
            raise ListingError("尚未成功点击批量设置。")
        radio = self.page.get_by_role("radio", name="48小时", exact=True)
        if radio.count() != 1 or not radio.is_checked():
            raise ListingError("发货时间未确认是 48 小时。")

    def select_recommended_title(self):
        self.tab("图文信息")
        # The editor can scroll inside a layout container rather than window.
        self.page.evaluate("""() => {
            window.scrollTo(0, 0);
            for (const e of document.querySelectorAll('main,[class*=content],[class*=Content]')) {
                if (e.scrollHeight > e.clientHeight) e.scrollTop = 0;
            }
        }""")
        fields = self.dom("title_recommendation")
        if not fields:
            self.tab("属性信息")
            fields = self.dom("title_recommendation")
        if not fields:
            raise ListingError("未找到商品标题旁的 AI 推荐按钮，未保存草稿。")
        self.click_field(self.page.locator(fields["button"]))
        self.wait(
            lambda: self.dom("first_recommended_title", selector=fields["input"]),
            "商品标题推荐未加载出可选标题，未保存草稿。",
            timeout=20,
        )
        first = self.dom("first_recommended_title", selector=fields["input"])
        previous = self.page.locator(fields["input"]).input_value().strip()

        def selected():
            current = self.dom("title_recommendation")
            if not current:
                return False
            value = self.page.locator(current["input"]).input_value().strip()
            same_title = re.sub(r"\s+", "", first["text"]).endswith(
                re.sub(r"\s+", "", value)
            )
            return (
                bool(value)
                and (value != previous or same_title)
                and not self.dom("first_recommended_title", selector=current["input"])
            )

        for attempt in range(3):
            if selected():
                break
            fields = self.dom("title_recommendation")
            current = self.dom("first_recommended_title", selector=fields["input"])
            if not current:
                self.click_field(self.page.locator(fields["button"]))
                self.wait(
                    lambda: self.dom(
                        "first_recommended_title", selector=fields["input"]
                    ),
                    "商品标题推荐未加载，未保存草稿。",
                    timeout=8,
                )
                current = self.dom("first_recommended_title", selector=fields["input"])
            first = current
            try:
                self.click_field(self.page.locator(first["selector"]))
            except Cancelled:
                raise
            except Exception:
                if selected():
                    break
                # Re-read the current first item after animation/React remount.
                fresh = self.dom("first_recommended_title", selector=fields["input"])
                if not fresh:
                    continue
                first = fresh
                target = self.page.locator(first["selector"])
                # Playwright's scroll helper also waits for stability. The item
                # is already visible; use native scrolling then verify hit area.
                target.evaluate(
                    "e=>e.scrollIntoView({block:'nearest',inline:'nearest'})"
                )
                guard = self.dom("interaction_guard", selector=first["selector"]) or {}
                if guard.get("close"):
                    self.page.locator(guard["close"]).click(timeout=1500)
                    continue
                if not guard.get("point"):
                    raise ListingError("推荐标题被其他浮层遮挡，未穿透点击。")
                self.log("推荐标题正在重绘，重新定位第一项并触发其点击")
                target.dispatch_event("click", timeout=1500)
            try:
                self.wait(selected, "第一个推荐标题尚未应用", timeout=3)
                break
            except Cancelled:
                raise
            except ListingError:
                if attempt == 2:
                    raise
        if not selected():
            raise ListingError("第一个推荐标题尚未应用，未保存草稿。")
        self.page.locator(self.dom("title_recommendation")["input"]).blur()
        self.log("已选择网站 AI 推荐的第一个商品标题，准备保存草稿")

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
