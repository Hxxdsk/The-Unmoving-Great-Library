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
from .guidance import UserGuide
from .profile_ui import WorkflowDialog


class Worker(QThread):
    row = Signal(str, str, str, int)
    message = Signal(str)
    outcome = Signal(object)
    failed = Signal(str)
    guidance = Signal(str, str)
    guided = Signal()

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
        self.guide = None
        self.bridge = EdgeBridge()
        self.config_path = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppConfigLocation
                )
            )
            / "listing-settings.json"
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
        title.addWidget(label("选择目录 → 连接 Edge → 逐货号填写 → 保存草稿", "hint"))
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
        self.connection_status = label("尚未连接 Edge", "hint")
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
        self.ai = QCheckBox("启用 AI 页面操作、配色与尺码识别")
        self.ai.setChecked(True)
        self.ai.setToolTip(
            "页面控件难以定位时，使用主页 API / 模型识别当前工作页截图并辅助悬停、点击。"
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
        profiles = QHBoxLayout()
        profiles.addWidget(QLabel("用户流程配置"))
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
        self.manual_enabled = QCheckBox("卡住时由我指导")
        self.manual_enabled.setChecked(
            self.preferences.get("manual_guidance", "on") != "off"
        )
        profiles.addWidget(self.manual_enabled)
        profiles.addStretch()
        content.addLayout(profiles)
        content.addWidget(
            label(
                "选择用户流程配置 → 连接 Edge → 开始任务。出现指导提示时在工作页点击正确位置；其他标签页可正常使用。",
                "hint",
            )
        )
        outer.addWidget(settings)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "选择",
                "货号",
                "800 / 3:4",
                "详情",
                "白底 / 水洗标",
                "尺码图",
                "状态",
                "进度",
                "当前步骤 / 错误",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            8, QHeaderView.ResizeMode.Stretch
        )
        for col in range(8):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        outer.addWidget(self.table, 1)
        self.manual_panel = QWidget()
        manual = QHBoxLayout(self.manual_panel)
        manual.setContentsMargins(0, 0, 0, 0)
        self.manual_text = QLabel()
        self.manual_text.setWordWrap(True)
        manual.addWidget(self.manual_text, 1)
        self.manual_resume = QPushButton("检查当前页面并继续")
        self.manual_resume.clicked.connect(
            lambda: self.guide and self.guide.resume.set()
        )
        self.manual_skip = QPushButton("跳过当前款")
        self.manual_skip.clicked.connect(lambda: self.guide and self.guide.skip.set())
        manual.addWidget(self.manual_resume)
        manual.addWidget(self.manual_skip)
        outer.addWidget(self.manual_panel)
        self.manual_panel.hide()
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
        self.connection_timer.timeout.connect(
            lambda: self.connection_status.setText(
                "Edge 已连接" if self.bridge.connected.is_set() else "尚未连接 Edge"
            )
        )
        self.connection_timer.start(1000)
        self.workflow_profile.currentTextChanged.connect(
            lambda _: self.save_preferences()
        )

    def save_preferences(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "directory": self.directory.text(),
                    "workflow_profile": self.workflow_profile.currentText(),
                    "manual_guidance": (
                        "on" if self.manual_enabled.isChecked() else "off"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def new_workflow_profile(self):
        name, ok = QInputDialog.getText(self, "新增用户流程配置", "配置名称")
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

    def show_guidance(self, goal, mode):
        prefix = (
            "在工作页点击正确控件（仅定位，由程序执行）："
            if mode == "pick"
            else "在工作页悬停或点击，程序会观察结果继续；也可点检查继续："
        )
        self.manual_text.setText(prefix + goal)
        self.manual_resume.setVisible(mode == "record")
        self.manual_panel.show()
        self.log.appendPlainText("等待用户指导：" + goal)

    def hide_guidance(self):
        self.manual_panel.hide()

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
        if not self.bridge.connected.is_set():
            raise RuntimeError(
                "请先点击“连接 Edge”；首次使用只需加载一次连接扩展，无需关闭 Edge。"
            )
        return self.bridge.start()

    def scan_root(self):
        try:
            self.products = scan_products(Path(self.directory.text()))
            journal = Journal(self.directory.text())
        except Exception as exc:
            self.error(str(exc))
            return
        self.table.setRowCount(len(self.products))
        for row, product in enumerate(self.products):
            can_run, reason = journal.resumable(product)
            check = QCheckBox()
            check.setChecked(not product.issues and can_run)
            self.table.setCellWidget(row, 0, check)
            values = [
                product.sku,
                f'{min(5,len(product.images["800"]))} / {min(5,len(product.images["3.4"]))}',
                str(len(product.images["详情"])),
                f'{len(product.images["白底"])} / {len(product.images["水洗标"])}',
                str(len(product.images["尺码"])),
                "资料缺失" if product.issues else reason or "待处理",
                "0%",
                "；".join(product.issues),
            ]
            for column, value in enumerate(values, 1):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if len(product.images["800"]) > 5 or len(product.images["3.4"]) > 5:
                self.log.appendPlainText(
                    f"{product.sku}：主图/3:4 只使用自然排序前五张。"
                )
        self.start.setEnabled(True)
        self.save_preferences()

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
            self.ai,
            self.workflow_profile,
            self.profile_new,
            self.profile_edit,
            self.manual_enabled,
        ):
            widget.setEnabled(not value)
        self.install_extension.setEnabled(self.worker is None)
        self.stop.setEnabled(value)
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, 0).setEnabled(not value)

    def start_work(self):
        products = self.selected()
        if not products:
            self.error("请勾选待处理货号。")
            return
        if not self.ai.isChecked():
            self.error("此工具需要 AI 识别颜色与尺码，请开启 AI。")
            return
        try:
            client = shared_client()
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
                guide=self.guide,
            ).run()

        self.worker = Worker(run, self)
        self.guide = (
            UserGuide(
                self.worker.cancel, self.worker.guidance.emit, self.worker.guided.emit
            )
            if self.manual_enabled.isChecked()
            else None
        )
        self.worker.guidance.connect(self.show_guidance)
        self.worker.guided.connect(self.hide_guidance)
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
                for column, value in ((6, status), (7, f"{percent}%"), (8, message)):
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
        self.guide = None
        self.hide_guidance()
        self.busy(False)

    def check_login(self):
        if self.check_job:
            return
        try:
            config = self.bridge.start()
            if not self.bridge.connected.is_set():
                self.bridge.open_edge()
            self.save_preferences()
        except Exception as exc:
            self.error(str(exc))
            return
        self.log.appendPlainText(
            "已在当前 Edge 打开连接页；首次使用请加载扩展，已安装时自动连接。"
        )

        def run(cancel, *_):
            import time

            deadline = time.monotonic() + 30
            while not self.bridge.connected.is_set():
                if cancel.wait(0.2):
                    return "已取消连接，Edge 窗口保留。"
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "尚未收到 Edge 扩展连接。请按连接页说明首次加载扩展，再点“连接 Edge”。"
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
