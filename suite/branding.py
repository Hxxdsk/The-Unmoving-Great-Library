from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def mascot(size=72):
    widget = QLabel()
    widget.setPixmap(QPixmap(str(ASSETS / "patchouli.png")).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    widget.setFixedSize(size, size)
    widget.setToolTip("帕秋莉的小书房 · 月亮、书页与七曜魔法")
    return widget
