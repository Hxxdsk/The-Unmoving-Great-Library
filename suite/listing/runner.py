from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from .models import Journal, ListingError, Cancelled, SaveUncertain
from .browser import EdgeSession
from .site import DouyinPage
from .vision import analyze


@contextlib.contextmanager
def batch_lock(root):
    stream = (Path(root) / "_抖店任务.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ListingError("此目录已有批量任务运行，请等待完成。") from None
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ListingError("此目录已有批量任务运行，请等待完成。") from None
        yield
    finally:
        stream.close()


class BatchRunner:
    def __init__(
        self,
        root,
        products,
        browser,
        client,
        cancel,
        price=399,
        inventory=1000,
        report=lambda *args: None,
        log=lambda _: None,
        session_factory=EdgeSession,
        memory=None,
        guide=None,
    ):
        self.root, self.products, self.browser = Path(root), products, browser
        self.memory, self.guide = memory, guide
        self.client, self.cancel = client, cancel
        self.price, self.inventory = price, inventory
        self.report, self.log, self.session_factory = report, log, session_factory

    def run(self):
        with batch_lock(self.root):
            journal = Journal(self.root)
            results = []
            with self.session_factory(self.browser, self.cancel) as session:
                self.log(session.check_login())
                for index, product in enumerate(self.products):
                    if self.cancel.is_set():
                        break
                    can_run, reason = journal.resumable(product)
                    if product.issues or not can_run:
                        status = "跳过"
                        message = (
                            "；".join(product.issues) if product.issues else reason
                        )
                        self.report(product.sku, status, message, 0)
                        results.append((product.sku, status))
                        continue
                    old = journal.get(product)
                    stage = "创建商品"
                    if hasattr(session, "new_work_page"):
                        session.new_work_page()
                    page = DouyinPage(
                        session,
                        self.cancel,
                        lambda text: self.log(f"[{product.sku}] {text}"),
                        client=self.client,
                        memory=self.memory,
                        guide=self.guide,
                    )

                    def advance(name, percent):
                        nonlocal stage
                        page.confirm_memory()
                        stage = name
                        journal.update(
                            product,
                            status="running",
                            stage=name,
                            editor_url=page.editor_url or old.get("editor_url", ""),
                        )
                        self.report(product.sku, "处理中", name, percent)
                        if self.cancel.is_set():
                            raise Cancelled("已停止")

                    try:
                        with tempfile.TemporaryDirectory(
                            prefix="greatlibrary-listing-"
                        ) as temp:
                            advance(
                                (
                                    "恢复编辑页"
                                    if old.get("editor_url")
                                    else "上传主图并生成商品"
                                ),
                                5,
                            )
                            if old.get("editor_url"):
                                page.resume(old["editor_url"])
                            else:
                                page.create(product)
                            page.tab("属性信息")
                            sku_control = page.fill_sku(
                                product.sku, require_same=bool(old.get("editor_url"))
                            )
                            advance("替换3:4、白底与详情图片", 25)
                            page.tab("图文信息")
                            page.replace_images(
                                ("主图3:4", "主图3：4"), product.portrait
                            )
                            page.replace_images(("白底图",), product.images["白底"])
                            page.replace_images(("商品详情",), product.images["详情"])
                            advance("水洗标与货号", 40)
                            page.tab("属性信息")
                            page.replace_images(
                                ("水洗标/吊牌图", "水洗标/吊牌图片"),
                                product.images["水洗标"],
                            )
                            sku_control = page.fill_sku(product.sku)
                            advance("识别颜色正面图、尺码与体重", 50)
                            page.ship_48h()
                            _, colors = page.specs("颜色分类")
                            analysis = analyze(
                                self.client,
                                product,
                                [r["name"] for r in colors],
                                self.cancel,
                                page.log,
                            )
                            for note in analysis.notes:
                                page.log(note)
                            advance("网站AI解析尺码表", 62)
                            page.parse_size_chart(product.images["尺码"], temp)
                            advance("设置颜色规格图、尺码与体重备注", 74)
                            page.set_color_photos(analysis)
                            page.set_sizes(analysis.sizes)
                            advance("批量价格与现货库存", 86)
                            page.batch_values(self.price, self.inventory)
                            advance("校验当前货号与规格", 93)
                            page.verify(
                                product,
                                analysis,
                                sku_control,
                                self.price,
                                self.inventory,
                            )
                            if self.cancel.is_set():
                                raise Cancelled("已停止，未点击保存草稿")
                            journal.update(
                                product,
                                status="saving",
                                stage="保存草稿",
                                editor_url=page.editor_url,
                            )
                            self.report(product.sku, "处理中", "保存草稿", 97)
                            url = page.save_draft()
                            journal.update(
                                product,
                                status="saved",
                                stage="草稿已保存",
                                editor_url=page.editor_url,
                                result_url=url,
                            )
                            self.report(product.sku, "已保存", "草稿已保存", 100)
                            results.append((product.sku, "已保存"))
                    except SaveUncertain as exc:
                        journal.update(
                            product,
                            status="uncertain",
                            stage="保存结果待核对",
                            editor_url=page.editor_url,
                            error=str(exc),
                        )
                        self.report(product.sku, "待核对", str(exc), 97)
                        results.append((product.sku, "待核对"))
                    except Exception as exc:
                        status = (
                            "cancelled"
                            if isinstance(exc, Cancelled) or self.cancel.is_set()
                            else "failed"
                        )
                        journal.update(
                            product,
                            status=status,
                            stage=stage,
                            editor_url=page.editor_url or old.get("editor_url", ""),
                            error=str(exc),
                        )
                        self.report(
                            product.sku,
                            "已停止" if status == "cancelled" else "失败",
                            f"{stage}：{exc}",
                            0,
                        )
                        results.append((product.sku, "失败"))
                        if self.cancel.is_set():
                            break
            return results
