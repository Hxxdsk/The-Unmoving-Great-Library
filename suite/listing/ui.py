from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QStandardPaths, QTimer
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QCheckBox,
    QProgressBar,
    QFileDialog,
    QDoubleSpinBox,
    QSpinBox,
    QMessageBox,
    QInputDialog,
)

from color_tool.ui import STYLESHEET, Job, card, label
from suite.branding import mascot, identify_component
from suite.registry import ROOT
from suite.shared_ai import shared_client
from suite.widgets import FollowLog, ClickOnlyComboBox
from .models import scan_products, Journal
from .browser import EdgeSession
from .bridge import EdgeBridge, EXTENSION_DIR
from .runner import BatchRunner
from .workflow import WorkflowStore
from .profile_ui import WorkflowDialog


class Worker(QThread):
    row = Signal(str, str, str, int)
    message = Signal(str)
    outcome = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = threading.Event()

    def run(self):
        try:
            self.outcome.emit(
                self.function(self.cancel, self.row.emit, self.message.emit)
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class ListingWindow(QMainWindow):
    def __init__(self, force_exit=None):
        super().__init__()
        self.setWindowTitle("抖店批量上架 · 保存商品草稿")
        self.setWindowIcon(QIcon(str(ROOT / "assets/logo-listing.ico")))
        self.resize(1320, 900)
        self.setMinimumSize(1080, 740)
        self.force_exit = force_exit or os._exit
        self.closing = False
        self.products = []
        self.worker = None
        self.check_job = None
        self.config_path = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppConfigLocation
                )
            )
            / "listing-settings.json"
        )
        self.bridge = EdgeBridge(
            self.config_path.with_name("listing-edge-pairing.json")
        )
        try:
            self.preferences = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.preferences = {}
        if not isinstance(self.preferences, dict):
            self.preferences = {}
        self.preferences = {
            key: value
            for key, value in self.preferences.items()
            if isinstance(value, str)
        }
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        header, layout = card()
        heading = QHBoxLayout()
        title = QVBoxLayout()
        title.addWidget(label("抖店批量上架", "title"))
        title.addWidget(label("帕秋莉会在底边陪你，把每一款稳稳收进草稿箱。", "hint"))
        heading.addLayout(title)
        heading.addStretch()
        heading.addWidget(mascot(70, "listing"))
        layout.addLayout(heading)
        outer.addWidget(header)
        settings, content = card()
        grid = QGridLayout()
        self.directory = QLineEdit(self.preferences.get("directory", ""))
        self.directory.setPlaceholderText("选择包含多个货号文件夹的目录")
        self.choose = QPushButton("选择目录")
        self.choose.clicked.connect(self.choose_root)
        self.scan = QPushButton("读取货号")
        self.scan.clicked.connect(self.scan_root)
        grid.addWidget(QLabel("资料目录"), 0, 0)
        grid.addWidget(self.directory, 0, 1, 1, 3)
        grid.addWidget(self.choose, 0, 4)
        grid.addWidget(self.scan, 0, 5)
        self.login = QPushButton("连接 Edge")
        self.login.clicked.connect(self.check_login)
        self.install_extension = QPushButton("首次安装扩展")
        self.install_extension.clicked.connect(self.open_extension)
        self.connection_status = label("正在恢复 Edge 配对…", "hint")
        self.connection_status.setWordWrap(True)
        browser_row = QHBoxLayout()
        browser_row.addWidget(self.login)
        browser_row.addWidget(self.install_extension)
        browser_row.addWidget(self.connection_status)
        browser_row.addStretch()
        grid.addLayout(browser_row, 1, 0, 1, 6)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 1)
        content.addLayout(grid)
        options = QHBoxLayout()
        self.ai = QCheckBox("启用 AI 识图与异常页面辅助")
        self.ai.setChecked(True)
        self.ai.setToolTip(
            "已确认的页面步骤优先使用流程记忆和当前控件；识别颜色、尺码体重或页面适配失效时才使用 AI。"
        )
        options.addWidget(self.ai)
        options.addStretch()
        options.addWidget(QLabel("售价"))
        self.price = QDoubleSpinBox()
        self.price.setRange(0.01, 999999)
        self.price.setValue(399)
        self.price.setDecimals(2)
        options.addWidget(self.price)
        options.addWidget(QLabel("现货库存"))
        self.inventory = QSpinBox()
        self.inventory.setRange(0, 9999999)
        self.inventory.setValue(1000)
        options.addWidget(self.inventory)
        options.addWidget(label("48 小时发货 · 只保存草稿", "hint"))
        content.addLayout(options)
        parallel = QHBoxLayout()
        parallel.addWidget(QLabel("同时进行任务数"))
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 999999)
        try:
            self.concurrency.setValue(int(self.preferences.get("concurrency", "1")))
        except ValueError:
            self.concurrency.setValue(1)
        parallel.addWidget(self.concurrency)
        self.all_parallel = QCheckBox("全部并行")
        self.all_parallel.setChecked(self.preferences.get("all_parallel") == "true")
        self.concurrency.setEnabled(not self.all_parallel.isChecked())
        self.all_parallel.toggled.connect(
            lambda checked: self.concurrency.setEnabled(not checked)
        )
        self.all_parallel.setToolTip(
            "本次勾选且可执行的所有货号同时进行，每款使用独立标签页。"
        )
        parallel.addWidget(self.all_parallel)
        parallel.addWidget(label("完成页保留 · 新任务在同一窗口新开标签页", "hint"))
        parallel.addStretch()
        content.addLayout(parallel)
        profiles = QHBoxLayout()
        profiles.addWidget(QLabel("流程配置"))
        self.workflow_profile = ClickOnlyComboBox()
        self.workflow_profile.setMinimumWidth(160)
        self.workflow_profile.setMaximumWidth(320)
        self.workflow_path = self.config_path.parent / "listing-workflows.json"
        chosen = self.preferences.get("workflow_profile", "默认配置")
        try:
            store = WorkflowStore(self.workflow_path, chosen)
            for name in store.profiles():
                self.workflow_profile.addItem(name)
            self.workflow_profile.setCurrentText(chosen)
        except Exception as exc:
            self.workflow_profile.addItem(chosen)
        profiles.addWidget(self.workflow_profile)
        self.profile_new = QPushButton("新增配置")
        self.profile_new.clicked.connect(self.new_workflow_profile)
        self.profile_edit = QPushButton("流程记忆")
        self.profile_edit.clicked.connect(self.edit_workflow)
        profiles.addWidget(self.profile_new)
        profiles.addWidget(self.profile_edit)
        profiles.addStretch()
        content.addLayout(profiles)
        content.addWidget(
            label(
                "选择流程配置 → 连接 Edge → 开始任务。控件变化时自动重新定位；任务使用独立普通窗口，其他窗口可正常使用。",
                "hint",
            )
        )
        outer.addWidget(settings)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "选择",
                "货号",
                "商品标题（必填）",
                "800 / 3:4",
                "详情",
                "白底 / 水洗标",
                "颜色 / 尺码",
                "状态",
                "进度",
                "当前步骤 / 错误",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            9, QHeaderView.ResizeMode.Stretch
        )
        for col in range(9):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        outer.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        outer.addWidget(self.progress)
        self.log = FollowLog()
        self.log.setMaximumHeight(150)
        outer.addWidget(self.log)
        actions = QHBoxLayout()
        self.reset = QPushButton("重置选中任务记录")
        self.reset.clicked.connect(self.reset_selected)
        actions.addWidget(self.reset)
        actions.addStretch()
        self.stop = QPushButton("停止")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_work)
        self.start = QPushButton("开始保存草稿")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.start_work)
        actions.addWidget(self.stop)
        actions.addWidget(self.start)
        outer.addLayout(actions)
        self.start.setEnabled(False)
        self.connection_timer = QTimer(self)
        self.connection_timer.timeout.connect(self.update_connection_status)
        self.connection_timer.start(3000)
        QTimer.singleShot(0, self.restore_connection)
        self.workflow_profile.currentTextChanged.connect(
            lambda _: self.save_preferences()
        )

    def restore_connection(self):
        if self.closing:
            return
        try:
            self.bridge.start()
            self.update_connection_status()
        except Exception as exc:
            self.log.appendPlainText("自动连接暂未启动：" + str(exc))

    def update_connection_status(self):
        if self.bridge.connected.is_set():
            text = "Edge 已自动连接 · 配对已记住"
        elif self.bridge.failure:
            text = "自动连接服务暂不可用：" + self.bridge.failure
        elif self.bridge.pairing.removed():
            text = "已检测到连接扩展被删除，请重新安装并连接"
        elif self.bridge.pairing.data.get("paired"):
            text = "配对已记住 · 等待 Edge 扩展自动上线"
        else:
            text = "首次使用请连接 Edge，此后自动连接"
        self.connection_status.setText(text)

    def save_preferences(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "directory": self.directory.text(),
                    "workflow_profile": self.workflow_profile.currentText(),
                    "concurrency": str(self.concurrency.value()),
                    "all_parallel": (
                        "true" if self.all_parallel.isChecked() else "false"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def new_workflow_profile(self):
        name, ok = QInputDialog.getText(self, "新增流程配置", "配置名称")
        if ok and name.strip():
            name = name.strip()[:60]
            store = WorkflowStore(self.workflow_path, name)
            store.save()
            if self.workflow_profile.findText(name) < 0:
                self.workflow_profile.addItem(name)
            self.workflow_profile.setCurrentText(name)
            self.save_preferences()

    def edit_workflow(self):
        try:
            WorkflowDialog(
                WorkflowStore(self.workflow_path, self.workflow_profile.currentText()),
                self,
            ).exec()
        except Exception as exc:
            self.error(str(exc))

    def open_extension(self):
        QApplication.clipboard().setText(str(EXTENSION_DIR.resolve()))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(EXTENSION_DIR.resolve())))
        self.log.appendPlainText(
            "首次安装：在 Edge 打开 edge://extensions → 开启开发人员模式 → 加载解压缩的扩展。"
            "扩展目录已复制，粘贴到目录选择框即可；完成后点“连接 Edge”。"
        )

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择资料根目录", self.directory.text()
        )
        if path:
            self.directory.setText(path)
            self.scan_root()

    def browser_config(self):
        if self.bridge.failure:
            raise RuntimeError("自动连接服务暂不可用：" + self.bridge.failure)
        if not self.bridge.connected.is_set():
            if self.bridge.pairing.removed():
                message = "已检测到连接扩展被删除，请重新安装扩展并连接。"
            elif self.bridge.pairing.data.get("paired"):
                message = "配对已记住，正在等待扩展自动上线；请打开原 Edge 用户配置并启用扩展，无需再次连接。"
            else:
                message = "首次使用请点击连接 Edge 并安装扩展；配对一次后会自动记住。"
            raise RuntimeError(message)
        return self.bridge.start()

    def scan_root(self):
        try:
            self.products = scan_products(Path(self.directory.text()))
            journal = Journal(self.directory.text())
        except Exception as exc:
            self.error(str(exc))
            return
        try:
            titles = json.loads(
                (Path(self.directory.text()) / "_抖店商品标题.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            titles = {}
        if not isinstance(titles, dict):
            titles = {}
        self.table.setRowCount(len(self.products))
        for row, product in enumerate(self.products):
            can_run, reason = journal.resumable(product)
            check = QCheckBox()
            check.setChecked(not product.issues and can_run)
            self.table.setCellWidget(row, 0, check)
            product.title = str(titles.get(product.sku, ""))
            title = QLineEdit(product.title)
            title.setMinimumWidth(250)
            title.setMaxLength(60)
            title.setPlaceholderText("输入本款商品标题，不使用AI推荐")
            title.textChanged.connect(
                lambda text, p=product: self.remember_title(p, text)
            )
            self.table.setCellWidget(row, 2, title)
            values = [
                product.sku,
                f'{min(5,len(product.images["800"]))} / {min(5,len(product.images["3.4"]))}',
                str(len(product.images["详情"])),
                f'{len(product.images["白底"])} / {len(product.images["水洗标"])}',
                f'{len(product.images["颜色"])} / AI识别',
                "资料缺失" if product.issues else reason or "待处理",
                "0%",
                "；".join(product.issues),
            ]
            for column, value in enumerate(values, 1):
                self.table.setItem(
                    row, column if column == 1 else column + 1, QTableWidgetItem(value)
                )
            if not product.images["颜色"]:
                self.log.appendPlainText(
                    f"{product.sku}：无颜色主图，跳过颜色配图，继续其他步骤。"
                )
            if len(product.images["800"]) > 5 or len(product.images["3.4"]) > 5:
                self.log.appendPlainText(
                    f"{product.sku}：主图/3:4 只使用自然排序前五张。"
                )
        self.start.setEnabled(True)
        self.save_preferences()

    def remember_title(self, product, text):
        product.title = text
        path = product.path.parent / "_抖店商品标题.json"
        try:
            titles = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
            if not isinstance(titles, dict):
                titles = {}
            titles[product.sku] = text
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
        except (OSError, ValueError) as exc:
            self.log.appendPlainText(f"标题记忆失败：{exc}")

    def selected(self):
        return [
            p
            for row, p in enumerate(self.products)
            if self.table.cellWidget(row, 0).isChecked()
        ]

    def busy(self, value):
        for widget in (
            self.choose,
            self.scan,
            self.start,
            self.login,
            self.directory,
            self.reset,
            self.price,
            self.inventory,
            self.concurrency,
            self.all_parallel,
            self.ai,
            self.workflow_profile,
            self.profile_new,
            self.profile_edit,
        ):
            widget.setEnabled(not value)
        self.concurrency.setEnabled(not value and not self.all_parallel.isChecked())
        self.install_extension.setEnabled(self.worker is None)
        self.stop.setEnabled(value)
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, 0).setEnabled(not value)
            self.table.cellWidget(row, 2).setEnabled(not value)

    def start_work(self):
        products = self.selected()
        if not products:
            self.error("请勾选待处理货号。")
            return
        if not self.ai.isChecked():
            self.error("此工具需要 AI 识别颜色与尺码，请开启 AI。")
            return
        try:
            client = shared_client(vision_fallback=True)
            config = self.browser_config()
            memory = WorkflowStore(
                self.workflow_path, self.workflow_profile.currentText()
            )
            self.save_preferences()
        except Exception as exc:
            self.error(str(exc))
            return
        root = Path(self.directory.text())
        price = self.price.value()
        stock = self.inventory.value()
        concurrency = self.concurrency.value()
        all_parallel = self.all_parallel.isChecked()

        def run(cancel, report, log):
            client.cancel = cancel
            client.log = log
            return BatchRunner(
                root,
                products,
                config,
                client,
                cancel,
                price,
                stock,
                report,
                log,
                memory=memory,
                concurrency=concurrency,
                all_parallel=all_parallel,
            ).run()

        self.worker = Worker(run, self)
        self.worker.row.connect(self.update_row)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.outcome.connect(
            lambda result: self.log.appendPlainText(
                f'本次结束：{sum(status=="已保存" for _,status in result)} 个草稿已确认保存。'
            )
        )
        self.worker.failed.connect(self.error)
        self.worker.finished.connect(self.work_finished)
        self.busy(True)
        self.worker.start()

    def update_row(self, sku, status, message, percent):
        for row, product in enumerate(self.products):
            if product.sku == sku:
                for column, value in ((7, status), (8, f"{percent}%"), (9, message)):
                    self.table.setItem(row, column, QTableWidgetItem(value))
                self.table.scrollToItem(self.table.item(row, 1))
                break
        self.progress.setValue(percent)
        self.log.appendPlainText(f"[{sku}] {status} · {message}")

    def stop_work(self):
        if self.check_job:
            self.check_job.cancel_event.set()
        if self.worker:
            self.worker.cancel.set()
            self.log.appendPlainText("正在停止，已确认保存的草稿保留…")

    def work_finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.busy(False)

    def check_login(self):
        if self.check_job:
            return
        try:
            config = self.bridge.start()
            if not self.bridge.connected.is_set() and (
                not self.bridge.pairing.data.get("paired")
                or self.bridge.pairing.removed()
            ):
                self.bridge.open_edge()
            self.save_preferences()
        except Exception as exc:
            self.error(str(exc))
            return
        self.log.appendPlainText(
            "首次配对请完成连接页操作；已配对时自动等待 Edge 上线，无需重复连接。"
        )

        def run(cancel, *_):
            import time

            deadline = time.monotonic() + 30
            while not self.bridge.connected.is_set():
                if cancel.wait(0.2):
                    return "已取消连接，Edge 窗口保留。"
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Edge 扩展暂未上线，配对记录保留，后台会继续自动等待。请确认已打开原 Edge 用户配置，扩展已启用。首次使用请按连接页说明安装扩展。"
                    )
            with EdgeSession(config, cancel) as session:
                return session.open_login()

        self.check_job = Job(run, self)
        self.check_job.result.connect(self.log.appendPlainText)
        self.check_job.failed.connect(self.error)

        def finish():
            job, self.check_job = self.check_job, None
            job.deleteLater()
            self.busy(False)

        self.check_job.finished.connect(finish)
        self.busy(True)
        self.check_job.start()

    def reset_selected(self):
        products = self.selected()
        if not products:
            return
        if (
            QMessageBox.question(
                self,
                "重置任务记录",
                "请先确认草稿箱中这些货号的状态。重置后会新建商品，可能产生重复草稿。\n确认重置选中货号？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            Journal(self.directory.text()).reset(products)
            self.scan_root()
        except Exception as exc:
            self.error(str(exc))

    def error(self, message):
        self.log.appendPlainText("错误：" + message)

    def closeEvent(self, event):
        if self.closing:
            event.accept()
            return
        self.closing = True
        active = self.worker is not None or self.check_job is not None
        if self.worker:
            self.worker.cancel.set()
        if self.check_job:
            self.check_job.cancel_event.set()
        self.bridge.close()
        event.accept()
        if active:
            # Each toolbox component is a separate process. Exiting ends all its
            # stuck Python threads and closes the Playwright driver pipe/CDP relay.
            # Ordinary Edge and other toolbox components stay alive.
            self.force_exit(0)


def main():
    import sys

    identify_component("listing")
    app = QApplication(sys.argv)
    app.setOrganizationName("GreatLibrary")
    app.setApplicationName("ProductToolbox")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = ListingWindow()
    window.show()
    raise SystemExit(app.exec())
