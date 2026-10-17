"""Full-image previews which do not take focus away from image selection."""

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QFrame, QVBoxLayout, QSizePolicy

from suite.widgets import ClickOnlyComboBox


class FitImage(QLabel):
    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        if path:
            self.set_image(path)

    def sizeHint(self):
        return QSize(300, 300)

    def set_image(self, path):
        self.source = QPixmap(str(path))
        self.fit()

    def fit(self):
        if self.source.isNull():
            self.clear()
            self.setText("无法预览此图片")
        else:
            self.setPixmap(
                self.source.scaled(
                    self.contentsRect().size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class ImageChoiceCombo(ClickOnlyComboBox):
    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.hover_preview = QFrame(self, Qt.WindowType.ToolTip)
        self.hover_preview.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.hover_preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hover_preview.setStyleSheet(
            "QFrame {background:white;border:1px solid #b69ace;border-radius:6px;}"
        )
        layout = QVBoxLayout(self.hover_preview)
        self.preview_image = FitImage()
        layout.addWidget(self.preview_image, 1)
        self.preview_name = QLabel()
        self.preview_name.setWordWrap(True)
        layout.addWidget(self.preview_name)
        self.hover_preview.resize(300, 320)
        self.view().setMouseTracking(True)
        self.view().entered.connect(lambda index: self.preview_index(index.row()))
        self.highlighted.connect(self.preview_index)

    def preview_index(self, index):
        filename = self.itemData(index)
        if not filename or not self.view().isVisible():
            self.hover_preview.hide()
            return
        path = self.folder / filename
        self.preview_image.set_image(path)
        self.preview_name.setText(filename)
        popup = self.view().window().frameGeometry()
        available = self.screen().availableGeometry()
        width = min(300, available.width())
        height = min(320, available.height())
        self.hover_preview.resize(width, height)
        x = min(popup.right() + 8, available.right() - width + 1)
        y = max(available.top(), min(popup.top(), available.bottom() - height + 1))
        self.hover_preview.move(max(available.left(), x), y)
        self.hover_preview.show()
        self.preview_image.fit()

    def hidePopup(self):
        self.hover_preview.hide()
        super().hidePopup()

    def hideEvent(self, event):
        self.hover_preview.hide()
        super().hideEvent(event)
