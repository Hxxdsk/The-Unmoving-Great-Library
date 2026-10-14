from __future__ import annotations

import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QCheckBox,
)

from .data import Project, Style, export_project, list_workbooks, make_plan, read_project, color_candidates, color_key
from suite.widgets import ClickOnlyComboBox, FollowLog, readable_selection
from .gemini import GeminiError
from suite.shared_ai import shared_client
from .settings import Settings

STYLESHEET = """
QMainWindow, QDialog { background: #f5f2fa; }
QWidget { font-family: 'Microsoft YaHei UI', 'Noto Sans CJK SC', sans-serif; font-size: 13px; color: #302341; }
QFrame#card { background: white; border: 1px solid #e4ddee; border-radius: 12px; }
QLabel#title { font-size: 21px; font-weight: 700; color: #302341; }
QFrame#toolHeader { background: #302341; border-radius: 12px; }
QFrame#toolHeader QLabel#title { color: white; }
QFrame#toolHeader QLabel#subtitle, QFrame#toolHeader QLabel#hint { color: #c4cee2; }
QLabel#subtitle { color: #6d7a8f; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#number { font-size: 19px; font-weight: 700; color: #8365bb; }
QLabel#hint { color: #69768c; font-size: 12px; }
QPushButton { background: white; border: 1px solid #d7deea; border-radius: 7px; padding: 8px 14px; }
QPushButton:hover, QPushButton:focus { background: #efe8f7; color: #302341; border-color: #b69ace; }
QPushButton#primary { background: #8365bb; color: white; border: 0; font-weight: 600; }
QPushButton#primary:hover, QPushButton#primary:focus { background: #6e51a3; color: white; }
QPushButton#export { background: #168b78; color: white; border: 0; font-weight: 600; }
QPushButton:disabled { background: #eef0f5; color: #a4adbd; border-color: #e1e6ef; }
QPushButton#primary:disabled, QPushButton#export:disabled { background: #e6eaf3; color: #9aa6bc; }
QLineEdit, QComboBox { background: white; border: 1px solid #d7deea; border-radius: 6px; padding: 7px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus { border-color: #496be8; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: white; color: #302341; selection-background-color: #eae2f5; selection-color: #302341; outline: 0; }
QComboBox QAbstractItemView::item:selected { background: #eae2f5; color: #302341; }
QListWidget { background: #fff; border: 0; outline: none; }
QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #edf0f6; }
QListWidget::item:selected { background: #eae2f5; color: #302341; border-left: 3px solid #8365bb; }
QTableWidget { background: white; color: #302341; border: 1px solid #e4ddee; gridline-color: #f0ebf6; selection-background-color: #eae2f5; selection-color: #302341; }
QTableWidget::item:hover { background: #f5f0fa; color: #302341; }
QTableWidget::item:selected, QTableWidget::item:selected:hover { background: #eae2f5; color: #302341; }
QHeaderView::section { background: #f5f7fb; padding: 9px 5px; border: 0; border-bottom: 1px solid #e2e7f0; font-weight: 600; }
QTableWidget QComboBox { margin: 3px; }
QPlainTextEdit { background: #f8faff; border: 1px solid #e2e7f0; border-radius: 6px; font-size: 12px; }
QProgressBar { border: 0; background: #e6ebf4; border-radius: 4px; height: 7px; }
QProgressBar::chunk { background: #8365bb; border-radius: 4px; }
QScrollArea { background: transparent; border: 0; }
"""


class Job(QThread):
    result = Signal(object)
    failed = Signal(str)
    message = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self.fn(self.cancel_event, self.message.emit, self.progress.emit)
            self.result.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    return frame, layout


def label(value, name=""):
    widget = QLabel(value)
    if name:
        widget.setObjectName(name)
    return widget


def thumbnail(path: Path, width=110, height=80):
    return QPixmap(str(path)).scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)


class Window(QMainWindow):
    def __init__(self, settings: Settings | None = None, ai_provider=None):
        super().__init__()
        self.setWindowTitle("商品图片 · 颜色配对助手")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "logo-color.ico")))
        self.resize(1310, 920)
        self.setMinimumSize(1060, 740)
        self.settings = settings or Settings(Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation)) / "preferences.json")
        self.ai_provider = ai_provider or shared_client
        self.project: Project | None = None
        self.job: Job | None = None
        self.last_output: Path | None = None
        self._build()
        saved = self.settings.get("directory")
        if saved and Path(saved).is_dir():
            self.set_directory(Path(saved))

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)
        header = QFrame()
        header.setObjectName("toolHeader")
        title_row = QHBoxLayout(header)
        title_row.setContentsMargins(16, 8, 16, 8)
        titles = QVBoxLayout()
        titles.addWidget(label("商品图片 · 颜色配对助手", "title"))
        titles.addWidget(label("读取表格  →  AI 或手动配对  →  核对并输出", "subtitle"))
        title_row.addLayout(titles)
        title_row.addStretch()
        mascot = label("")
        mascot.setPixmap(thumbnail(Path(__file__).resolve().parent.parent / "assets" / "logo-color.png", 52, 52))
        mascot.setToolTip("帕秋莉的魔法书 · 图片与资料整理")
        title_row.addWidget(mascot)
        self.setup_toggle = QPushButton("收起设置 ▴")
        self.setup_toggle.setCheckable(True)
        self.setup_toggle.toggled.connect(self.toggle_setup)
        title_row.addWidget(self.setup_toggle)
        outer.addWidget(header)

        setup_card, setup = card()
        self.setup_card = setup_card
        setup.setContentsMargins(12, 8, 12, 8)
        setup.setSpacing(6)
        grid = QGridLayout()
        grid.setVerticalSpacing(4)
        self.directory = QLineEdit()
        self.directory.setReadOnly(True)
        self.directory.setPlaceholderText("选择包含商品资料表格和款号文件夹的目录")
        self.choose_button = QPushButton("选择目录")
        self.choose_button.clicked.connect(self.choose_directory)
        self.workbook_combo = QComboBox()
        self.workbook_combo.setMinimumWidth(240)
        self.scan_button = QPushButton("读取表格")
        self.scan_button.clicked.connect(self.scan)
        self.scan_button.setEnabled(False)
        grid.addWidget(label("资料目录"), 0, 0)
        grid.addWidget(self.directory, 0, 1)
        grid.addWidget(self.choose_button, 0, 2)
        grid.addWidget(self.workbook_combo, 0, 3)
        grid.addWidget(self.scan_button, 0, 4)
        self.workbook_combo.setPlaceholderText("选择商品表格")
        self.workbook_combo.setToolTip("目录内的商品资料表格；多个表格时在这里选择。")
        self.ai_enabled = QCheckBox("启用 AI")
        self.ai_enabled.setChecked(True)
        self.ai_enabled.toggled.connect(self.update_totals)
        grid.addWidget(self.ai_enabled, 1, 0)
        grid.addWidget(label("API / 模型在工具箱首页统一设置", "hint"), 1, 1, 1, 4)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 2)
        setup.addLayout(grid)
        privacy_row = QHBoxLayout()
        privacy_row.addWidget(label("无 Key 也可手动配对后输出。", "hint"))
        privacy_row.addStretch()
        setup.addLayout(privacy_row)
        outer.addWidget(setup_card)

        stats = QHBoxLayout()
        self.stats = []
        for heading in ("款号", "商品图片输出数", "已配对颜色 / 总颜色"):
            frame = QFrame()
            frame.setObjectName("card")
            frame.setFixedHeight(40)
            layout = QHBoxLayout(frame)
            layout.setContentsMargins(12, 4, 12, 4)
            number = label("—", "number")
            self.stats.append(number)
            layout.addWidget(number)
            layout.addWidget(label(heading, "hint"))
            layout.addStretch()
            stats.addWidget(frame)
        outer.addLayout(stats)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_card, left = card()
        left.addWidget(label("款号列表", "section"))
        self.style_filter = QLineEdit()
        self.style_filter.setPlaceholderText("搜索款号…")
        self.style_filter.textChanged.connect(self.filter_styles)
        left.addWidget(self.style_filter)
        self.style_list = QListWidget()
        self.style_list.currentRowChanged.connect(self.render_style)
        readable_selection(self.style_list)
        left.addWidget(self.style_list)
        self.style_summary = label("读取表格后显示款号与处理状态。", "hint")
        self.style_summary.setWordWrap(True)
        left.addWidget(self.style_summary)
        left_card.setMinimumWidth(190)
        splitter.addWidget(left_card)
        right_card, right = card()
        right.setContentsMargins(14, 12, 14, 12)
        right.setSpacing(6)
        top = QHBoxLayout()
        description = QVBoxLayout()
        self.style_heading = label("颜色配对工作台", "section")
        self.style_hint = label("点击列表选择图片；双击行放大。", "hint")
        self.style_hint.setWordWrap(True)
        description.addWidget(self.style_heading)
        description.addWidget(self.style_hint)
        self.chart_button = QPushButton("查看颜色图")
        self.chart_button.clicked.connect(self.show_chart)
        self.chart_button.setEnabled(False)
        top.addLayout(description, 1)
        top.addWidget(self.chart_button)
        right.addLayout(top)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["表格颜色", "商品数", "颜色图名称", "使用的原图", "图片预览", "可信度¹", "配对状态"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        readable_selection(self.table)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for column, width in ((0, 94), (1, 56), (2, 108), (4, 84), (5, 66), (6, 132)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self.preview_row)
        right.addWidget(self.table, 1)
        gallery_row = QHBoxLayout()
        gallery_row.addWidget(label("双击行查看原图；名称不同的颜色由你连接。", "hint"))
        gallery_row.addStretch()
        self.gallery_toggle = QPushButton("展开原图 ▾")
        self.gallery_toggle.setCheckable(True)
        self.gallery_toggle.toggled.connect(self.toggle_gallery)
        gallery_row.addWidget(self.gallery_toggle)
        right.addLayout(gallery_row)
        self.gallery = QScrollArea()
        self.gallery.setWidgetResizable(True)
        self.gallery.setFixedHeight(122)
        self.gallery.hide()
        right.addWidget(self.gallery)
        self.table.horizontalHeaderItem(5).setToolTip("可信度为模型自评，仅供参考；近似颜色请核对原图。")
        splitter.addWidget(right_card)
        splitter.setSizes([205, 1040])
        outer.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.analyze_button = QPushButton("AI 识别全部款号")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.clicked.connect(self.analyze)
        self.analyze_button.setEnabled(False)
        self.retry_button = QPushButton("重识别当前款号")
        self.retry_button.clicked.connect(lambda: self.analyze(current_only=True))
        self.retry_button.setEnabled(False)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.export_button = QPushButton("确认配对并输出图片")
        self.export_button.setObjectName("export")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        self.open_button = QPushButton("打开输出目录")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        for widget in (self.analyze_button, self.retry_button, self.cancel_button):
            bottom.addWidget(widget)
        bottom.addStretch()
        self.partial_output = QCheckBox("仅输出已配对")
        self.partial_output.toggled.connect(self.update_totals)
        bottom.addWidget(self.partial_output)
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.export_button)
        outer.addLayout(bottom)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        outer.addWidget(self.progress_bar)
        log_row = QHBoxLayout()
        self.log_toggle = QPushButton("处理日志 ▾")
        self.log_toggle.setCheckable(True)
        log_row.addWidget(self.log_toggle)
        self.latest_log = label("等待处理…", "hint")
        self.latest_log.setMinimumWidth(0)
        self.latest_log.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        log_row.addWidget(self.latest_log, 1)
        follow = QCheckBox("跟随最新")
        follow.setChecked(True)
        log_row.addWidget(follow)
        outer.addLayout(log_row)
        self.log = FollowLog()
        self.log.setFixedHeight(100)
        self.log.setPlaceholderText("处理日志：此处会显示扫描、识别与输出结果。")
        self.log.hide()
        self.log_toggle.toggled.connect(self.log.setVisible)
        follow.toggled.connect(lambda enabled: setattr(self.log, "follow_latest", enabled))
        self.log.textChanged.connect(self.update_latest_log)
        outer.addWidget(self.log)
        self.busy_widgets = [setup_card, splitter, self.analyze_button, self.retry_button,
                             self.export_button, self.open_button, self.partial_output]

    def save_setting(self, key, value):
        try:
            self.settings.set(key, value)
        except OSError as exc:
            self.log.appendPlainText(f"偏好保存失败：{exc}。本次仍可继续使用。")

    def choose_directory(self):
        selected = QFileDialog.getExistingDirectory(self, "选择资料目录", self.directory.text())
        if selected:
            self.set_directory(Path(selected))

    def set_directory(self, path: Path):
        self.project = None
        self.last_output = None
        self.style_list.clear()
        self.directory.setText(str(path))
        self.workbook_combo.clear()
        try:
            files = list_workbooks(path)
        except OSError as exc:
            self.error(str(exc))
            return
        for file in files:
            self.workbook_combo.addItem(file.name, str(file))
        old = self.settings.get("workbook")
        index = self.workbook_combo.findText(old)
        if files:
            self.workbook_combo.setCurrentIndex(max(index, 0))
        self.save_setting("directory", str(path))
        self.scan_button.setEnabled(bool(files))
        self.render_style()
        self.update_totals()
        if not files:
            self.log.appendPlainText("目录内没有 .xlsx / .xlsm 表格；旧版 .xls 请先另存为 .xlsx。")

    def scan(self):
        value = self.workbook_combo.currentData()
        if not value:
            return
        self.save_setting("workbook", self.workbook_combo.currentText())
        root, workbook = Path(self.directory.text()), Path(value)
        self.run_job(lambda *_: read_project(root, workbook), self.scanned)

    def scanned(self, project):
        self.project = project
        self.last_output = None
        self.refresh_list()
        self.style_list.setCurrentRow(0)
        self.render_style()
        self.log.appendPlainText(f"已读取 {len(project.styles)} 个款号、{project.sku_count} 个商品编码。")
        for warning in project.warnings:
            self.log.appendPlainText(warning)
        for style in project.styles:
            if style.issues:
                self.log.appendPlainText(f"{style.code}：{'；'.join(style.issues)}")
        self.update_totals()

    def toggle_gallery(self, visible):
        self.gallery.setVisible(visible)
        self.gallery_toggle.setText("收起原图 ▴" if visible else "展开原图 ▾")

    def toggle_setup(self, collapsed):
        self.setup_card.setVisible(not collapsed)
        self.setup_toggle.setText("资料设置 ▾" if collapsed else "收起设置 ▴")

    def update_latest_log(self):
        lines = self.log.toPlainText().splitlines()
        if lines:
            self.latest_log.setText(lines[-1][:110])
            self.latest_log.setToolTip(lines[-1])

    def filter_styles(self):
        query = self.style_filter.text().strip().casefold()
        for row in range(self.style_list.count()):
            item = self.style_list.item(row)
            item.setHidden(query not in item.text().casefold())

    def current_style(self) -> Style | None:
        index = self.style_list.currentRow()
        if self.project and 0 <= index < len(self.project.styles):
            return self.project.styles[index]
        return None

    def analyze(self, checked=False, current_only=False):
        if not self.project:
            return
        if not self.ai_enabled.isChecked():
            return
        try:
            client = self.ai_provider()
        except GeminiError as exc:
            self.error(str(exc))
            return
        styles = [self.current_style()] if current_only else self.project.styles
        styles = [style for style in styles if style and not style.issues and not style.ai_issues and style.chart]
        if not styles:
            self.error("没有可识别的款号，请检查日志中的文件夹或颜色图问题。")
            return

        def work(cancel, log, progress):
            client.cancel = cancel
            client.log = log
            succeeded = 0

            def analyze_one(style):
                try:
                    client.analyze_style(style, log, force=current_only)
                    log(f"{style.code}：已配对 {len(style.selections)}/{len(style.colors)}。")
                    return 1
                except Exception as exc:
                    style.analysis_error = str(exc)
                    log(f"{style.code}：{exc}")
                    return 0

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(analyze_one, style) for style in styles]
                for index, future in enumerate(as_completed(futures), 1):
                    succeeded += future.result()
                    progress(index, len(styles))
            return succeeded, len(styles), cancel.is_set()

        def done(result):
            self.refresh_list()
            self.render_style()
            self.update_totals()
            passed, total, cancelled = result
            self.log.appendPlainText(f"识别{'已取消' if cancelled else '结束'}：{passed}/{total} 款成功。请核对配对表；未匹配颜色需手动选择。")

        self.run_job(work, done)

    def refresh_list(self):
        old = self.style_list.currentRow()
        self.style_list.blockSignals(True)
        self.style_list.clear()
        if self.project:
            for style in self.project.styles:
                status = "文件待检查" if style.issues else "识别失败" if style.analysis_error else f"已配对 {len(style.selections)}/{len(style.colors)}"
                self.style_list.addItem(f"{style.code}\n{style.sku_count} 个商品  ·  {status}")
        self.style_list.setCurrentRow(max(old, 0) if self.project else -1)
        self.style_list.blockSignals(False)
        self.filter_styles()

    def render_style(self, *_):
        style = self.current_style()
        self.table.setRowCount(0)
        gallery_widget = QWidget()
        gallery_layout = QHBoxLayout(gallery_widget)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        self.gallery.setWidget(gallery_widget)
        self.chart_button.setEnabled(bool(style and style.chart))
        if not style:
            self.style_heading.setText("颜色配对工作台")
            self.style_summary.setText("读取表格后显示款号与处理状态。")
            return
        self.style_heading.setText(f"{style.code}  ·  {len(style.colors)} 个颜色")
        self.style_hint.setText("；".join(style.issues + style.ai_issues) or ("AI 失败，可手动配对。" if style.analysis_error
                                                         else "核对建议；名称不一致的行请重点检查。"))
        self.style_summary.setText(f"{len(style.images)} 张原图 · {style.sku_count} 张输出")
        self.table.setRowCount(len(style.colors))
        for row, (color, skus) in enumerate(style.colors.items()):
            self.table.setRowHeight(row, 46)
            self.table.setItem(row, 0, QTableWidgetItem(color))
            count = QTableWidgetItem(str(len(skus)))
            count.setToolTip("\n".join(skus))
            self.table.setItem(row, 1, count)
            combo = ClickOnlyComboBox()
            readable_selection(combo.view())
            combo.setMaxVisibleItems(12)
            combo.addItem("请选择对应图片…", "")
            for image in style.images:
                match = style.matches.get(image.name)
                info = f"{image.name} · {match.color_label or match.main_color}" if match else image.name
                combo.addItem(QIcon(thumbnail(image, 56, 42)), info, image.name)
            combo.setCurrentIndex(max(combo.findData(style.selections.get(color, "")), 0))
            combo.currentIndexChanged.connect(lambda _, s=style, c=color, r=row, cb=combo: self.select_image(s, c, r, cb.currentData()))
            self.table.setCellWidget(row, 3, combo)
            self.update_row(style, color, row)
        for image in style.images:
            frame = QFrame()
            frame.setMinimumWidth(105)
            frame.setMaximumWidth(135)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(4, 0, 4, 0)
            button = QPushButton()
            button.setIcon(QIcon(thumbnail(image, 112, 74)))
            from PySide6.QtCore import QSize
            button.setIconSize(QSize(112, 74))
            button.clicked.connect(lambda _, p=image: self.show_image(p))
            layout.addWidget(button)
            match = style.matches.get(image.name)
            text = image.name + (" · " + (match.color_label or "待复核") if match else "")
            caption = label(text, "hint")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            layout.addWidget(caption)
            if match:
                frame.setToolTip(f"主体颜色：{match.main_color}\n模型自评：{match.confidence:.0%}\n{match.reason}")
            gallery_layout.addWidget(frame)
        gallery_layout.addStretch()

    def select_image(self, style: Style, color: str, row: int, filename: str):
        other = next((name for name, selected in style.selections.items() if selected == filename and name != color), None) if filename else None
        if other:
            QMessageBox.warning(self, "图片已使用", f"这张图片已配给“{other}”，请先解除该配对。")
            combo = self.table.cellWidget(row, 3)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(combo.findData(style.selections.get(color, "")), 0))
            combo.blockSignals(False)
            return
        style.manual_colors.add(color)
        style.suggested_colors.discard(color)
        style.review_notes.pop(color, None)
        style.name_mismatches.pop(color, None)
        style.pair_confidences.pop(color, None)
        if filename:
            style.selections[color] = filename
        else:
            style.selections.pop(color, None)
        self.update_row(style, color, row)
        self.refresh_list()
        self.update_totals()

    def update_row(self, style: Style, color: str, row: int):
        chosen = style.selections.get(color, "")
        match = style.matches.get(chosen)
        candidates = color_candidates(style, color)
        pending = candidates[0] if not chosen and candidates else None
        visible_match = match or pending
        label_text = visible_match.color_label if visible_match else "—"
        self.table.setItem(row, 2, QTableWidgetItem(label_text or "未确定"))
        confidence = style.pair_confidences.get(color, visible_match.confidence if visible_match else 0)
        self.table.setItem(row, 5, QTableWidgetItem(f"{confidence:.0%}" if visible_match else "—"))
        status = "人工连接" if chosen and color in style.manual_colors else "自动配对 · 请核对" if chosen else "待人工连接"
        if chosen and color in style.suggested_colors:
            status = "名称不一致 · 请核对" if color in style.name_mismatches else "AI建议 · 请核对"
        if pending:
            status = "已识别 · 待复核"
        item = QTableWidgetItem(status)
        item.setForeground(QColor("#9b641c") if color in style.suggested_colors or not chosen else QColor("#168b78"))
        detail = style.review_notes.get(color, "") or (visible_match.reason if visible_match else "可直接选择任意原图。")
        if pending:
            detail = f"候选 {pending.filename}，自评 {pending.confidence:.0%}，复核标记 {pending.needs_review}。\n" + detail
            self.table.cellWidget(row, 3).setToolTip(detail)
        item.setToolTip(detail)
        self.table.setItem(row, 6, item)
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if chosen and style.folder:
            preview.setPixmap(thumbnail(style.folder / chosen, 94, 46))
        self.table.setCellWidget(row, 4, preview)

    def update_totals(self):
        if self.project:
            total = sum(len(s.colors) for s in self.project.styles)
            paired = sum(len(s.selections) for s in self.project.styles)
            self.stats[0].setText(str(len(self.project.styles)))
            self.stats[1].setText(str(self.project.sku_count))
            self.stats[2].setText(f"{paired} / {total}")
            ready = paired == total and all(not s.issues for s in self.project.styles)
            if self.partial_output.isChecked():
                ready = any(s.selections and not s.issues for s in self.project.styles)
        else:
            for widget in self.stats:
                widget.setText("—")
            ready = False
        if self.job:
            return
        ai_ready = bool(
            self.project
            and any(
                s.chart and not s.issues and not s.ai_issues
                for s in self.project.styles
            )
            and self.ai_enabled.isChecked()
        )
        self.analyze_button.setEnabled(ai_ready)
        current = self.current_style()
        self.retry_button.setEnabled(bool(ai_ready and current and current.chart and not current.ai_issues and not current.issues))
        self.export_button.setEnabled(ready)
        self.open_button.setEnabled(self.last_output is not None)

    def export(self):
        if not self.project:
            return
        try:
            plan = make_plan(self.project, partial=self.partial_output.isChecked())
        except Exception as exc:
            self.error(str(exc))
            return
        manual = sum(len(s.manual_colors & s.selections.keys()) for s in self.project.styles)
        suggested = sum(len(s.suggested_colors) for s in self.project.styles)
        prompt = f"按当前配对输出 {len(plan)} 张图片？\n人工配对 {manual} 色，AI 待核对建议 {suggested} 色。\n原图保留。"
        if self.partial_output.isChecked():
            prompt += f"\n本次跳过未配对或文件待检查的 {self.project.sku_count-len(plan)} 张。"
        if QMessageBox.question(self, "确认输出", prompt) != QMessageBox.StandardButton.Yes:
            return
        project = self.project
        self.run_job(lambda cancel, log, progress: export_project(project, plan, cancel, progress), self.exported)

    def exported(self, path):
        self.last_output = path
        self.log.appendPlainText(f"输出完成：{path}\n每个颜色的商品编码共用同一张原图；配对记录也已保存。")
        self.update_totals()

    def run_job(self, fn, on_result):
        if self.job:
            return
        self.job = Job(fn, self)
        self.job.message.connect(self.log.appendPlainText)
        self.job.progress.connect(self.set_progress)
        self.job.result.connect(on_result)
        self.job.failed.connect(self.error)
        self.job.finished.connect(self.job_finished)
        for widget in self.busy_widgets:
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self.job.start()

    def job_finished(self):
        job = self.job
        self.job = None
        if job:
            job.deleteLater()
        for widget in self.busy_widgets:
            widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.scan_button.setEnabled(self.workbook_combo.count() > 0)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.update_totals()

    def set_progress(self, current, total):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

    def cancel(self):
        if self.job:
            self.job.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.log.appendPlainText("正在取消；已发出的网络请求会在返回或超时后停止。")

    def error(self, message):
        self.log.appendPlainText(message)
        QMessageBox.warning(self, "需要检查", message)

    def show_chart(self):
        style = self.current_style()
        if style and style.chart:
            self.show_image(style.chart)

    def preview_row(self, row, _):
        style = self.current_style()
        if style and style.folder:
            filename = style.selections.get(list(style.colors)[row])
            if filename:
                self.show_image(style.folder / filename)

    def show_image(self, path: Path):
        dialog = QDialog(self)
        dialog.setWindowTitle(path.name)
        dialog.resize(840, 780)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        image = QLabel()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            image.setText("无法预览此图片；请检查文件内容或格式。")
        else:
            image.setPixmap(pixmap.scaled(1100, 1700, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        scroll.setWidget(image)
        layout.addWidget(scroll)
        layout.addWidget(label(str(path), "hint"))
        dialog.exec()

    def open_output(self):
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output)))

    def closeEvent(self, event):
        if self.job and self.job.isRunning():
            self.cancel()
            self.log.appendPlainText("任务仍在结束中，请稍后再关闭窗口。")
            event.ignore()
        else:
            event.accept()


def main():
    from suite.branding import identify_component
    identify_component("color")
    app = QApplication(sys.argv)
    app.setOrganizationName("GreatLibrary")
    app.setApplicationName("ColorImageAssistant")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = Window()
    window.show()
    sys.exit(app.exec())
