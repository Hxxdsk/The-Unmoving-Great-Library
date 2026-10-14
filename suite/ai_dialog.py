"""主页唯一的 API 列表与模型选择入口。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)
from color_tool.gemini import GeminiClient
from color_tool.ui import Job
from .widgets import ClickOnlyComboBox


class AISettingsDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.job = None
        self.setWindowTitle("通用 AI · API 与模型")
        self.resize(700, 510)
        self.setMinimumSize(600, 450)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        title = QLabel("新增 API → 检测模型 → 选择通用模型")
        title.setObjectName("section")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("备注（选填）")
        self.name.setMaximumWidth(155)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("粘贴 Gemini API Key")
        self.add_button = QPushButton("新增 API")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.add_key)
        row.addWidget(self.name)
        row.addWidget(self.key)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self.entries = QListWidget()
        self.entries.currentItemChanged.connect(self.selected)
        layout.addWidget(self.entries, 1)
        row = QHBoxLayout()
        self.detect_button = QPushButton("检测所选 API 模型")
        self.detect_button.clicked.connect(self.detect)
        self.delete_button = QPushButton("删除所选 API")
        self.delete_button.clicked.connect(self.remove)
        row.addWidget(self.detect_button)
        row.addWidget(self.delete_button)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("通用模型"))
        self.models = ClickOnlyComboBox()
        self.models.setPlaceholderText("先检测 API，再选择通用模型")
        self.models.setMinimumWidth(360)
        self.models.currentIndexChanged.connect(self.model_changed)
        row.addWidget(self.models, 1)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("hint")
        layout.addWidget(self.status)
        note = QLabel(
            "429 时按列表轮询支持同一模型的 API；限额 API 暂时冷却。相同项目的 Key 可能共享额度。"
        )
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)
        done = QPushButton("完成")
        done.clicked.connect(self.accept)
        layout.addWidget(done, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def refresh(self):
        snapshot = self.manager.snapshot()
        self.entries.blockSignals(True)
        self.entries.clear()
        for entry in snapshot["entries"]:
            count = len(entry["models"])
            state = f"{count} 个模型" if count else "待检测"
            suffix = " · 首选" if entry["id"] == snapshot["preferred"] else ""
            item = QListWidgetItem(
                f"{entry['name']}  ·  …{entry['key'][-4:]}  ·  {state}{suffix}"
            )
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.entries.addItem(item)
            if entry["id"] == snapshot["preferred"]:
                self.entries.setCurrentItem(item)
        if self.entries.currentRow() < 0 and self.entries.count():
            self.entries.setCurrentRow(0)
        self.entries.blockSignals(False)
        self.refresh_models()

    def identifier(self):
        item = self.entries.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def selected(self, *_):
        identifier = self.identifier()
        if identifier:
            self.manager.select(identifier=identifier)
        self.refresh_models()

    def refresh_models(self):
        snapshot = self.manager.snapshot()
        entry = next(
            (e for e in snapshot["entries"] if e["id"] == self.identifier()), None
        )
        names = entry["models"] if entry else []
        self.models.blockSignals(True)
        self.models.clear()
        for name in names:
            count = sum(name in e["models"] for e in snapshot["entries"])
            self.models.addItem(
                f"{name.removeprefix('models/')}  ·  {count} 条 API 可用", name
            )
        self.models.setCurrentIndex(self.models.findData(snapshot["model"]))
        self.models.blockSignals(False)
        self.detect_button.setEnabled(bool(entry) and self.job is None)
        self.delete_button.setEnabled(bool(entry) and self.job is None)
        self.status.setText(
            self.manager.message or "选择 API 并检测模型；各工具共用此配置。"
        )

    def add_key(self):
        try:
            self.manager.add(self.key.text(), self.name.text())
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.key.clear()
        self.name.clear()
        self.refresh()
        self.detect()

    def remove(self):
        if self.identifier():
            self.manager.remove(self.identifier())
            self.refresh()

    def model_changed(self):
        model = self.models.currentData()
        if model:
            self.manager.select(model=model)
            self.status.setText("所有工具下次任务使用此模型；运行中的任务保持原模型。")

    def detect(self):
        if self.job:
            return
        identifier = self.identifier()
        entry = next(
            (e for e in self.manager.snapshot()["entries"] if e["id"] == identifier),
            None,
        )
        if not entry:
            return
        self.detect_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.add_button.setEnabled(False)
        self.entries.setEnabled(False)
        self.status.setText("正在检测所选 API…")
        self.job = Job(
            lambda cancel, *_: GeminiClient(entry["key"], cancel=cancel).list_models(),
            self,
        )

        def done(models):
            self.manager.detected(identifier, models)
            self.refresh()

        self.job.result.connect(done)
        self.job.failed.connect(self.status.setText)
        self.job.finished.connect(self.finished_job)
        self.job.start()

    def finished_job(self):
        job, self.job = self.job, None
        if job:
            job.deleteLater()
        self.detect_button.setEnabled(bool(self.identifier()))
        self.delete_button.setEnabled(bool(self.identifier()))
        self.add_button.setEnabled(True)
        self.entries.setEnabled(True)

    def done(self, result):
        if self.job and self.job.isRunning():
            self.status.setText("正在结束检测，请稍后关闭。")
            self.job.cancel_event.set()
            return
        super().done(result)
