from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QTextCursor
from PySide6.QtWidgets import QComboBox, QPlainTextEdit


class ClickOnlyComboBox(QComboBox):
    """滚轮向父滚动区传递，仅列表确认改变选项。"""
    def wheelEvent(self, event):
        event.ignore()


class FollowLog(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.follow_latest = True
        self.setReadOnly(True)
        self.setMaximumBlockCount(1500)

    def appendPlainText(self, text):
        bar = self.verticalScrollBar()
        previous = bar.value()
        super().appendPlainText(text)
        if self.follow_latest:
            self.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())
        else:
            bar.setValue(previous)


def readable_selection(widget):
    from PySide6.QtGui import QColor
    palette = widget.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Highlight, QColor("#eae2f5"))
        palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor("#302341"))
    widget.setPalette(palette)
