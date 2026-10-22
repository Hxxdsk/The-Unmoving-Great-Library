from __future__ import annotations

import contextlib
import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
import tempfile
from pathlib import Path

from .models import Journal, ListingError, Cancelled, SaveUncertain
from .browser import EdgeSession, is_connection_failure
from .site import DouyinPage
from .vision import analyze, prepare_color_sources
from suite.cache import ResultCache


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
        concurrency=1,
        all_parallel=False,
    ):
        self.root, self.products, self.browser = Path(root), products, browser
        self.memory, self.guide = memory, guide
        self.concurrency = max(1, int(concurrency))
        self.all_parallel = all_parallel
        self.cache = ResultCache("listing-vision-v2", self.root / "_抖店识别缓存")
        self.client, self.cancel = client, cancel
        self.price, self.inventory = price, inventory
        self.report, self.log, self.session_factory = report, log, session_factory

    def run(self):
        with batch_lock(self.root):
            journal = Journal(self.root)
            limit = len(self.products) if self.all_parallel else self.concurrency
            limit = min(limit, len(self.products))
            if limit > 1:
                return self._parallel(journal, limit)
            with self.session_factory(self.browser, self.cancel) as session:
                self.log(session.check_login())
                return self._run_products(session, journal, self.products)

    def _parallel(self, journal, limit):
        from .parallel import BrowserRuntime

        results = []
        pending_products = iter(self.products)
        with BrowserRuntime(self.browser, self.cancel) as runtime:
            with runtime.session() as login:
                login.next_work_page()
                self.log(login.check_login())
                if hasattr(runtime, "remember_login"):
                    runtime.remember_login(login.page)

            def run_product(product):
                worker = copy.copy(self)
                # API fallback/model state belongs to this task, not its peers.
                worker.client = copy.copy(self.client)
                if hasattr(worker.client, "log"):
                    worker.client.log = lambda text: self.log(f"[{product.sku}] {text}")
                if hasattr(worker.client, "model_pauses"):
                    worker.client.model_pauses = dict(worker.client.model_pauses)
                with runtime.session() as session:
                    return worker._run_products(session, journal, [product])

            with ThreadPoolExecutor(max_workers=limit) as pool:
                futures = set()

                def submit():
                    while not self.cancel.is_set() and len(futures) < limit:
                        product = next(pending_products, None)
                        if product is None:
                            break
                        can_run, reason = journal.resumable(product)
                        if product.issues or not can_run:
                            self.report(
                                product.sku,
                                "跳过",
                                "；".join(product.issues) or reason,
                                0,
                            )
                            results.append((product.sku, "跳过"))
                            continue
                        futures.add(pool.submit(run_product, product))

                submit()
                while futures:
                    done, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        try:
                            results.extend(future.result())
                        except Exception:
                            self.cancel.set()
                            raise
                    submit()
        order = {p.sku: i for i, p in enumerate(self.products)}
        return sorted(results, key=lambda item: order[item[0]])

    def _run_products(self, session, journal, products):
        results = []
        for product in products:
            if self.cancel.is_set():
                break
            can_run, reason = journal.resumable(product)
            if product.issues or not can_run:
                status = "跳过"
                message = "；".join(product.issues) if product.issues else reason
                self.report(product.sku, status, message, 0)
                results.append((product.sku, status))
                continue
            stage = "创建商品"
            if hasattr(session, "next_work_page"):
                session.next_work_page()
            elif hasattr(session, "new_work_page"):
                session.new_work_page()
            page = DouyinPage(
                session,
                self.cancel,
                lambda text: self.log(f"[{product.sku}] {text}"),
                client=self.client,
                memory=self.memory,
                guide=self.guide,
            )

            driver_recoveries = 0

            def replace_field(names, paths):
                nonlocal driver_recoveries
                try:
                    return page.replace_images(names, paths)
                except Exception as exc:
                    if not is_connection_failure(exc):
                        raise
                    if driver_recoveries or not hasattr(
                        session, "reconnect_current_page"
                    ):
                        raise
                    driver_recoveries += 1
                    page.log(
                        "浏览器驱动连接已断开，尝试一次重连原编辑页；不重新创建商品"
                    )
                    try:
                        session.reconnect_current_page()
                    except Cancelled:
                        raise
                    except Exception as recovery:
                        raise ListingError("驱动重连未完成：" + str(recovery)) from exc
                    page.page = session.page
                    page.pending.clear()
                    page.last_plan = None
                    page.spec_cache.clear()
                    page.spec_layouts.clear()
                    page.popups = []
                    page.creation_pages = set(session.context.pages)
                    page.log("已重连原编辑页，重新读取并替换当前图片字段")
                return page.replace_images(names, paths)

            def advance(name, percent):
                nonlocal stage
                page.confirm_memory()
                stage = name
                page.phase = name
                journal.update(
                    product,
                    status="running",
                    stage=name,
                    editor_url=page.editor_url,
                )
                self.report(product.sku, "处理中", name, percent)
                if self.cancel.is_set():
                    raise Cancelled("已停止")

            try:
                with tempfile.TemporaryDirectory(
                    prefix="greatlibrary-listing-"
                ) as temp:
                    advance("从新页面上传主图并生成商品", 5)
                    page.create(product)
                    advance("按页面顺序填写图文信息", 25)
                    page.tab("图文信息")
                    fields = [
                        (("主图3:4", "主图3：4"), product.portrait),
                        (("白底图",), product.images["白底"]),
                    ]
                    if not page.details_prepared:
                        fields.append((("商品详情",), product.images["详情"]))
                    for names, paths in page.fields_in_page_order(fields):
                        try:
                            replace_field(names, paths)
                        except Cancelled:
                            raise
                        except Exception as exc:
                            if is_connection_failure(exc):
                                raise
                            page.discard_pending(str(exc))
                            page.log(f"跳过图片字段 {'/'.join(names)}：{exc}")
                            page.dismiss_optional_dialogs()
                    advance("水洗标与货号", 40)
                    page.tab("属性信息")
                    try:
                        replace_field(
                            ("水洗标/吊牌图", "水洗标/吊牌图片"),
                            product.images["水洗标"],
                        )
                    except Cancelled:
                        raise
                    except Exception as exc:
                        if is_connection_failure(exc):
                            raise
                        page.discard_pending(str(exc))
                        page.log(f"跳过水洗标图片：{exc}")
                        page.dismiss_optional_dialogs()
                    sku_control = page.fill_sku(product.sku)
                    advance("识别颜色正面图、尺码与体重", 50)
                    page.ship_48h()
                    page.ensure_color_first()
                    prepare_color_sources(
                        self.client, product, self.cancel, page.log, cache=self.cache
                    )
                    _, colors = page.specs("颜色分类")
                    analysis = analyze(
                        self.client,
                        product,
                        [r["name"] for r in colors],
                        self.cancel,
                        page.log,
                        cache=self.cache,
                    )
                    for note in analysis.notes:
                        page.log(note)
                    advance("按页面顺序上传颜色规格图", 60)
                    page.set_color_photos(analysis)
                    advance("填写尺码与体重备注", 70)
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
                    advance("选择网站推荐标题", 95)
                    page.select_recommended_title()
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
                    page.confirm_memory()
                    journal.update(
                        product,
                        status="saved",
                        stage="草稿已保存",
                        editor_url=page.editor_url,
                        result_url=url,
                        skipped_colors=analysis.skipped_colors,
                    )
                    message = "草稿已保存"
                    if analysis.skipped_colors:
                        message += "；跳过颜色：" + " / ".join(analysis.skipped_colors)
                    self.report(product.sku, "已保存", message, 100)
                    results.append((product.sku, "已保存"))
                    if hasattr(session, "park_saved_page"):
                        try:
                            session.park_saved_page()
                        except Exception as exc:
                            page.log(
                                "草稿已保存；当前扩展暂未启用完成页节能，页面保留："
                                + str(exc).splitlines()[0][:160]
                            )
            except SaveUncertain as exc:
                page.discard_pending(str(exc), penalize=False)
                journal.update(
                    product,
                    status="uncertain",
                    stage="保存结果待核对",
                    editor_url=page.editor_url,
                    error=str(exc),
                )
                self.report(product.sku, "待核对", str(exc), 97)
                results.append((product.sku, "待核对"))
                if is_connection_failure(exc):
                    self.cancel.set()
                    break
            except Exception as exc:
                disconnected = is_connection_failure(exc)
                page.discard_pending(
                    str(exc),
                    penalize=not disconnected and not isinstance(exc, Cancelled),
                )
                if disconnected:
                    exc = ListingError(
                        "浏览器驱动连接已失效，已保留编辑现场并停止本批次；请重新连接 Edge。原错误："
                        + str(exc)
                    )
                status = (
                    "cancelled"
                    if isinstance(exc, Cancelled) or self.cancel.is_set()
                    else "failed"
                )
                journal.update(
                    product,
                    status=status,
                    stage=stage,
                    editor_url=page.editor_url,
                    error=str(exc),
                )
                self.report(
                    product.sku,
                    "已停止" if status == "cancelled" else "失败",
                    f"{stage}：{exc}",
                    0,
                )
                results.append((product.sku, "失败"))
                if disconnected:
                    self.cancel.set()
                if self.cancel.is_set() or disconnected:
                    break
        return results
