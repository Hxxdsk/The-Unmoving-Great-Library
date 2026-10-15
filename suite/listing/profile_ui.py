from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QHBoxLayout,
    QLabel,
)


class WorkflowDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store, self.keys = store, []
        self.setWindowTitle("流程配置 · " + store.profile)
        self.resize(850, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("成功步骤会自动记忆；选中步骤可删除旧记忆，重新指导。"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["步骤", "来源", "成功 / 失效", "页面"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        remove = QPushButton("删除选中步骤")
        remove.clicked.connect(self.remove)
        row.addWidget(remove)
        row.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.keys = list(self.store.steps())
        self.table.setRowCount(len(self.keys))
        for index, key in enumerate(self.keys):
            rows = self.store.steps()[key]
            newest = max(rows, key=lambda r: r.get("updated", 0))
            values = (
                newest["goal"],
                newest.get("source", ""),
                f'{sum(r.get("successes",0) for r in rows)} / {sum(r.get("failures",0) for r in rows)}',
                newest["page"],
            )
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))

    def remove(self):
        indices = {i.row() for i in self.table.selectedIndexes()}
        for index in indices:
            self.store.steps().pop(self.keys[index], None)
        self.store.save()
        self.refresh()
