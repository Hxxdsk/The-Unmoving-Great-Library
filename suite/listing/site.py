from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageOps

from .models import ListingError, Cancelled, SaveUncertain
from .browser import ENTRY_URL
from .vision import normalize_size

DOM = Path(__file__).with_name("page.js").read_text(encoding="utf-8")


class DouyinPage:
    def __init__(self, session, cancel, log=lambda _: None):
        self.session, self.page, self.cancel, self.log = (
            session,
            session.page,
            cancel,
            log,
        )
        self.editor_url = ""

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
                return
            if self.cancel.wait(0.25):
                self.check()
        raise ListingError(message)

    def click(self, text, root=None):
        self.check()
        scope = root or self.page
        target = scope.get_by_role("button", name=text, exact=isinstance(text, str))
        if target.count() != 1:
            target = scope.get_by_text(text, exact=isinstance(text, str))
        visible = [
            target.nth(i) for i in range(target.count()) if target.nth(i).is_visible()
        ]
        if len(visible) != 1:
            raise ListingError(
                f"页面控件「{text}」未找到或有多个，停止当前货号以免误操作。"
            )
        if re.search("发布商品|立即发布|确认发布", visible[0].inner_text()):
            raise ListingError("工具只保存草稿，禁止点击发布。")
        visible[0].click(timeout=12000)

    def tab(self, text):
        target = self.page.get_by_text(text, exact=True)
        candidates = [
            target.nth(i) for i in range(target.count()) if target.nth(i).is_visible()
        ]
        if len(candidates) == 1:
            candidates[0].click()

    def section(self, *names):
        for name in names:
            selector = self.dom("section", name)
            if selector:
                self.page.locator(selector).scroll_into_view_if_needed()
                return selector
        raise ListingError("页面未识别到字段：" + "/".join(names))

    def photo_count(self, selector):
        return len(self.dom("photos", selector=selector) or [])

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
            target = scope.get_by_text(
                re.compile(r"^(上传图片|上传主图|上传辅助图|图片|本地上传)$")
            ).first
            if not target.count():
                trigger = self.dom("upload_trigger", selector=selector)
                if not trigger:
                    raise ListingError("未找到该字段的本地上传控件。")
                target = self.page.locator(trigger)
            target.hover()
            local = self.page.get_by_text("本地上传", exact=True)
            if local.count() and local.last.is_visible():
                target = local.last
            with self.page.expect_file_chooser(timeout=10000) as chooser:
                target.click()
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
                image.hover()
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

    def replace_images(self, names, paths):
        selector = self.section(*names)
        self.clear(selector)
        self.upload(selector, paths)
        if self.photo_count(selector) != len(paths):
            raise ListingError("字段图片数量与当前货号资料不符。")

    def create(self, product):
        self.session.goto(ENTRY_URL)
        self.click("新建商品")
        popups = []

        def capture(page):
            popups.append(page)

        self.page.on("popup", capture)
        self.click("单商品")
        self.wait(
            lambda: bool(popups)
            or self.page.get_by_text("本地上传", exact=True).count() > 0,
            "新建商品页面未打开",
            timeout=30,
        )
        self.page.remove_listener("popup", capture)
        if popups:
            if len(popups) != 1:
                raise ListingError("新建商品打开多个页面，无法确认当前货号页面。")
            self.page = self.session.page = popups[0]
            if hasattr(self.session, "own_page"):
                self.session.own_page(self.page)
        local = self.page.get_by_text("本地上传", exact=True).first
        local.wait_for(state="visible", timeout=20000)
        with self.page.expect_file_chooser() as chooser:
            local.click()
        chooser.value.set_files([str(path) for path in product.main])
        self.wait(
            lambda: not self.dom("busy")
            and self.page.get_by_role(
                "button", name=re.compile("下一步")
            ).first.is_enabled(),
            "商品主图上传未完成",
            timeout=90,
        )
        self.click(re.compile(r"^下一步.*"))
        self.wait(
            lambda: bool(self.dom("recommendations")),
            "网站没有返回可选择的 AI 推荐标题",
            timeout=180,
        )
        recommendation = self.dom("recommendations")[0]
        self.page.locator(recommendation["selector"]).click()
        self.click(re.compile(r"^确认信息.*继续生成$|^继续生成$"))
        self.wait(
            lambda: bool(self.dom("section", "主图3:4")),
            "网站尚未生成商品编辑页",
            timeout=180,
        )
        self.editor_url = self.page.url

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
            raise ListingError("货号输入框不唯一，停止以免串款。")
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
                raise ListingError("未找到新增尺码选择框。")
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
                raise ListingError(f"未找到唯一可选尺码 {name}，未自造规格。")
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
        button = self.page.get_by_role("button", name="保存草稿", exact=True)
        if button.count() != 1:
            raise ListingError("保存草稿按钮不唯一，未执行保存。")
        success = re.compile("草稿保存成功|保存成功|已保存至草稿箱|已保存到草稿箱")
        existing = self.page.get_by_text(success)
        if any(existing.nth(i).is_visible() for i in range(existing.count())):
            raise ListingError("页面残留旧的保存回执，请重新打开当前任务后重试。")
        try:
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
