"""A reserved, nonmodal bottom shelf; no idle animation or background AI calls."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QEvent, QSize
from PySide6.QtGui import QPixmap, QImageReader
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
)

from .guide_content import GuideSession, read_state

ASSET = Path(__file__).resolve().parent.parent / "assets/guide-patchouli.png"


@lru_cache(maxsize=1)
def pet_pixmap():
    path = ASSET if ASSET.exists() else ASSET.with_name("patchouli.png")
    reader = QImageReader(str(path))
    reader.setScaledSize(
        reader.size().scaled(QSize(76, 76), Qt.AspectRatioMode.KeepAspectRatio)
    )
    return QPixmap.fromImage(reader.read())


class Pet(QLabel):
    def __init__(self, shelf):
        super().__init__(shelf)
        self.shelf = shelf
        self.setPixmap(pet_pixmap())
        self.setFixedSize(80, 80)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("点击翻小提示；在底边拖动我，靠边就会本次静音。")
        self.press = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = event.globalPosition().toPoint()
            self.start_x = self.shelf.pet_x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.shelf.animation.stop()
            self.shelf.bob = 0
            event.accept()

    def mouseMoveEvent(self, event):
        if self.press is not None:
            dx = event.globalPosition().toPoint().x() - self.press.x()
            self.shelf.pet_x = max(0, min(self.shelf.width() - 80, self.start_x + dx))
            self.shelf.arrange()
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.press is None:
            return
        moved = (event.globalPosition().toPoint() - self.press).manhattanLength() > 6
        self.press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if moved:
            if self.shelf.pet_x < 28 or self.shelf.pet_x > self.shelf.width() - 108:
                self.shelf.set_quiet(True)
        elif self.shelf.session.muted:
            self.shelf.set_quiet(False)
        else:
            self.shelf.turn(1)
        event.accept()


class GuideShelf(QWidget):
    def __init__(self, owner, component, action=None):
        super().__init__(owner)
        self.owner, self.component, self.action = owner, component, action
        self.session = GuideSession(component)
        self.state, self.note = {}, None
        self.pet_x, self.bob = 14, 0
        self.compact = False
        self.force_expanded = False
        self.setObjectName("patchouliShelf")
        self.setStyleSheet(
            "#patchouliShelf {background:#f4eff9;border-top:1px solid #e1d7ed;} QFrame#patchouliBubble {background:#fffafd;border:1px solid #e1d7ed;border-radius:10px;}"
        )
        self.setFixedHeight(86)
        self.pet = Pet(self)
        self.bubble = QFrame(self)
        self.bubble.setObjectName("patchouliBubble")
        layout = QVBoxLayout(self.bubble)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(2)
        row = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setStyleSheet("color:#76549b;font-size:12px;font-weight:600")
        row.addWidget(self.heading, 1)
        for caption, callback in [
            ("‹", lambda: self.turn(-1)),
            ("翻一页 ›", lambda: self.turn(1)),
            ("趴一会儿", lambda: self.set_quiet(True)),
        ]:
            button = QPushButton(caption)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setStyleSheet("padding:1px 6px;min-height:18px;font-size:11px")
            button.clicked.connect(callback)
            row.addWidget(button)
        self.action_button = QPushButton()
        self.action_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.action_button.setStyleSheet(
            "padding:1px 6px;min-height:18px;font-size:11px"
        )
        self.action_button.clicked.connect(self.run_action)
        row.addWidget(self.action_button)
        layout.addLayout(row)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("font-size:12px;color:#554365;")
        layout.addWidget(self.message, 1)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(420)
        self.animation.setStartValue(0.0)
        self.animation.setKeyValueAt(0.35, -5.0)
        self.animation.setEndValue(0.0)
        self.animation.valueChanged.connect(self.animate)
        self.animation.finished.connect(lambda: self.animate(0))
        self.timer = QTimer(self)
        self.timer.setInterval(1800)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        owner.installEventFilter(self)
        self.update_compact()

    def eventFilter(self, watched, event):
        if watched is self.owner:
            if event.type() == QEvent.Type.Resize:
                self.update_compact()
            elif event.type() == QEvent.Type.WindowStateChange:
                if self.owner.isMinimized():
                    self.timer.stop()
                    self.animation.stop()
                elif not self.session.muted and self.isVisible():
                    self.refresh()
                    self.timer.start()
        return super().eventFilter(watched, event)

    def update_compact(self):
        compact = self.owner.height() < 820 and not self.force_expanded
        if compact != self.compact:
            self.compact = compact
            self.message.setVisible(not compact)
            if not self.session.muted:
                self.setFixedHeight(44 if compact else 86)
            self.arrange()

    def run_action(self):
        if self.action and self.note:
            self.action(self.note.key)

    def animate(self, value):
        self.bob = int(value)
        self.arrange()

    def refresh(self):
        if self.session.muted:
            return
        self.state = read_state(self.owner, self.component)
        note = self.session.choose(self.state)
        self.show_note(note)

    def show_note(self, note):
        if note != self.note:
            self.note = note
            self.heading.setText("帕秋莉 · " + note.title)
            self.message.setText(note.text)
            self.action_button.setText(note.action)
            self.action_button.setVisible(bool(note.action and self.action))
        self.arrange()

    def turn(self, delta):
        if self.session.muted:
            return
        if self.compact:
            self.force_expanded = True
            self.update_compact()
            note = self.session.choose(self.state)
        else:
            note = self.session.turn(self.state, delta)
        self.show_note(note)
        self.animation.stop()
        self.animation.start()

    def set_quiet(self, quiet):
        self.session.muted = quiet
        self.animation.stop()
        self.bob = 0
        self.setFixedHeight(32 if quiet else 44 if self.compact else 86)
        self.bubble.setVisible(not quiet)
        if quiet:
            self.timer.stop()
            source = pet_pixmap()
            self.pet.setPixmap(
                source.copy(0, 18, source.width(), min(34, source.height() - 18))
            )
            self.pet.setFixedSize(80, 32)
            self.pet_x = 0 if self.pet_x < self.width() / 2 else self.width() - 80
            self.pet.setToolTip(
                "帕秋莉趴在书架边休息，本次不再提醒。点点脑袋可以叫醒她。"
            )
        else:
            self.pet.setPixmap(pet_pixmap())
            self.pet.setFixedSize(80, 80)
            if self.isVisible() and not self.owner.isMinimized():
                self.timer.start()
            self.refresh()
        self.arrange()

    def arrange(self):
        self.pet_x = max(0, min(max(0, self.width() - 80), self.pet_x))
        self.pet.move(self.pet_x, 2 + self.bob if not self.session.muted else 0)
        width = max(200, min(820, self.width() - 106))
        x = (
            self.pet_x + 86
            if self.pet_x + 86 + width <= self.width() - 4
            else self.pet_x - width - 6
        )
        x = max(4, min(self.width() - width - 4, x))
        # If the pet is dragged through the middle, put the bubble on the other
        # side or temporarily hide it; it never escapes the reserved shelf.
        overlaps = x < self.pet_x + 80 and x + width > self.pet_x
        self.bubble.setVisible(not self.session.muted and not overlaps)
        self.bubble.setGeometry(x, 3, width, 36 if self.compact else 78)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "pet"):
            self.arrange()

    def hideEvent(self, event):
        self.timer.stop()
        self.animation.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.session.muted:
            self.timer.start()

    def closeEvent(self, event):
        self.timer.stop()
        self.animation.stop()
        super().closeEvent(event)


def install_qt_guide(owner, component, action=None):
    shelf = GuideShelf(owner, component, action)
    owner.centralWidget().layout().addWidget(shelf)
    owner.patchouli_guide = shelf
    return shelf
