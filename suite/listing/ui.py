from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QStandardPaths
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
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QCheckBox,
    QProgressBar,
    QFileDialog,
    QDoubleSpinBox,
    QSpinBox,
    QMessageBox,
    QGroupBox,
)

from color_tool.ui import STYLESHEET, Job, card, label
from suite.branding import mascot, identify_component
from suite.registry import ROOT
from suite.shared_ai import shared_client
from suite.widgets import FollowLog, ClickOnlyComboBox
from .models import scan_products, Journal
from .browser import BrowserConfig, EdgeSession, edge_data_path, edge_profiles
from .runner import BatchRunner


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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("抖店批量上架 · 保存商品草稿")
        self.setWindowIcon(QIcon(str(ROOT / "assets/logo-listing.ico")))
        self.resize(1320, 900)
        self.setMinimumSize(1080, 740)
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
        title.addWidget(
            label("选择目录 → 打开 Edge 操作窗口 → 逐货号填写 → 保存草稿", "hint")
        )
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
        self.user_data = QLineEdit(
            self.preferences.get("user_data", str(edge_data_path()))
        )
        self.profile = ClickOnlyComboBox()
        self.profile.setMinimumWidth(160)
        self.profile_choose = QPushButton("资料目录")
        self.profile_choose.clicked.connect(self.choose_profile_path)
        self.login = QPushButton("打开 Edge / 检查登录")
        self.login.clicked.connect(self.check_login)
        grid.addWidget(QLabel("Edge 资料"), 1, 0)
        grid.addWidget(self.user_data, 1, 1, 1, 2)
        grid.addWidget(self.profile, 1, 3)
        grid.addWidget(self.profile_choose, 1, 4)
        grid.addWidget(self.login, 1, 5)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 1)
        content.addLayout(grid)
        options = QHBoxLayout()
        self.ai = QCheckBox("启用 AI 配色与尺码识别")
        self.ai.setChecked(True)
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
        self.advanced = QGroupBox("可选：连接已启用调试的 Edge / 指定浏览器程序")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(
            bool(self.preferences.get("endpoint") or self.preferences.get("executable"))
        )
        advanced = QHBoxLayout(self.advanced)
        self.endpoint = QLineEdit(self.preferences.get("endpoint", ""))
        self.endpoint.setPlaceholderText(
            "本机调试地址，如 http://127.0.0.1:9222（可留空）"
        )
        self.executable = QLineEdit(self.preferences.get("executable", ""))
        self.executable.setPlaceholderText("msedge.exe 路径（默认自动查找）")
        advanced.addWidget(self.endpoint)
        advanced.addWidget(self.executable)
        content.addWidget(self.advanced)
        content.addWidget(
            label(
                "使用可见的 Edge 操作窗口，保留登录与窗口供下次复用；操作期间请勿手动切换页面。API / 模型在工具箱主页配置。",
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
        self.user_data.editingFinished.connect(self.refresh_profiles)
        self.refresh_profiles()
        self.start.setEnabled(False)

    def refresh_profiles(self):
        profiles, preferred = edge_profiles(self.user_data.text())
        selected = self.profile.currentData() or self.preferences.get(
            "profile", preferred
        )
        self.profile.clear()
        for key, name in profiles:
            self.profile.addItem(f"{name} · {key}", key)
        self.profile.setCurrentIndex(max(0, self.profile.findData(selected)))

    def save_preferences(self):
        data = {
            "directory": self.directory.text(),
            "user_data": self.user_data.text(),
            "profile": self.profile.currentData() or "Default",
            "endpoint": self.endpoint.text(),
            "executable": self.executable.text(),
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择资料根目录", self.directory.text()
        )
        if path:
            self.directory.setText(path)
            self.scan_root()

    def choose_profile_path(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择 Edge User Data 目录", self.user_data.text()
        )
        if path:
            self.user_data.setText(path)
            self.refresh_profiles()

    def browser_config(self):
        return BrowserConfig(
            self.user_data.text(),
            self.profile.currentData() or "Default",
            self.executable.text().strip() if self.advanced.isChecked() else "",
            self.endpoint.text().strip() if self.advanced.isChecked() else "",
        )

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
            self.profile_choose,
            self.directory,
            self.user_data,
            self.profile,
            self.reset,
            self.price,
            self.inventory,
            self.advanced,
            self.ai,
        ):
            widget.setEnabled(not value)
        self.stop.setEnabled(value and self.worker is not None)
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
                root, products, config, client, cancel, price, stock, report, log
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
                for column, value in ((6, status), (7, f"{percent}%"), (8, message)):
                    self.table.setItem(row, column, QTableWidgetItem(value))
                self.table.scrollToItem(self.table.item(row, 1))
                break
        self.progress.setValue(percent)
        self.log.appendPlainText(f"[{sku}] {status} · {message}")

    def stop_work(self):
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
        config = self.browser_config()
        self.save_preferences()
        self.log.appendPlainText("正在打开或连接 Edge 操作窗口，复用所选资料…")

        def run(cancel, *_):
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
        if self.worker or self.check_job:
            if self.worker:
                self.worker.cancel.set()
            if self.check_job:
                self.check_job.cancel_event.set()
            self.log.appendPlainText("正在结束当前步骤，请稍后关闭。")
            event.ignore()
        else:
            event.accept()


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
