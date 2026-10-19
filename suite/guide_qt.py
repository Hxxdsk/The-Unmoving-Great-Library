"""Floating, nonmodal desktop pet. No layout space or idle API calls."""

from functools import lru_cache
import time
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint, QVariantAnimation
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QTransform
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLineEdit,
)
from .guide_content import GuideSession, read_state
from .guide_support import sprite, candidates, bubble_position, ChatRequest


@lru_cache(maxsize=12)
def pet_pixmap(name="idle"):
    im = sprite(name)
    return QPixmap.fromImage(
        QImage(im.tobytes(), im.width, im.height, QImage.Format.Format_RGBA8888).copy()
    )


class Pet(QLabel):
    def __init__(self, guide):
        super().__init__(guide.host)
        self.shelf = guide
        self.setFixedSize(112, 112)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动我到任意位置；靠边变成扒窗休息。点击互动，双击收起提示。")
        self.press = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press = e.globalPosition().toPoint()
            self.origin = self.pos()
            self.shelf.interact("wave")
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.press is not None:
            d = e.globalPosition().toPoint() - self.press
            self.shelf.animation.stop()
            self.shelf.move_pet(self.origin + d)
            self.shelf.bubble.hide()
            self.shelf.marker.hide()

    def mouseReleaseEvent(self, e):
        if self.press is None:
            return
        moved = (e.globalPosition().toPoint() - self.press).manhattanLength() > 6
        self.press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if moved:
            self.shelf.finish_drag()
        elif self.shelf.session.muted:
            self.shelf.set_quiet(False)
        else:
            self.shelf.interact("wave")
            self.shelf.bubble.setVisible(not self.shelf.bubble.isVisible())
            self.shelf.arrange()

    def mouseDoubleClickEvent(self, e):
        self.shelf.bubble.hide()
        self.shelf.marker.hide()


class Marker(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#b48bdd"), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)


class GuideShelf(QWidget):
    """Compatibility name; controller is invisible, pet/bubble are sibling overlays."""

    def __init__(self, owner, component, action=None):
        super().__init__(owner)
        self.hide()
        self.owner = owner
        self.host = owner.centralWidget()
        self.component = component
        self.action = action
        self.session = GuideSession(component)
        self.state = {}
        self.note = None
        self.dock = None
        self.chat = ChatRequest()
        self.pose = "idle"
        self.pose_until = 0
        self.last_touch = time.monotonic()
        self.ticks = 0
        self.started = False
        self.pet = Pet(self)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(420)
        self.animation.setStartValue(0)
        self.animation.setKeyValueAt(0.35, -6)
        self.animation.setEndValue(0)
        self.animation.valueChanged.connect(self.bounce)
        self.marker = Marker(self.host)
        self.bubble = QFrame(self.host)
        self.bubble.setObjectName("petBubble")
        self.bubble.setStyleSheet(
            "#petBubble{background:#fffafd;border:1px solid #bda4d8;border-radius:12px;} QLabel{color:#554365;} QPushButton{padding:3px 6px;min-height:18px;}"
        )
        layout = QVBoxLayout(self.bubble)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self.heading = QLabel()
        row.addWidget(self.heading, 1)
        for title, fn in [
            ("‹", lambda: self.turn(-1)),
            ("›", lambda: self.turn(1)),
            ("×", self.collapse),
        ]:
            b = QPushButton(title)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(fn)
            row.addWidget(b)
        layout.addLayout(row)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.action_button = QPushButton()
        self.action_button.clicked.connect(self.run_action)
        layout.addWidget(self.action_button)
        row = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setPlaceholderText("问帕秋莉…（按需调用已有 API）")
        self.question.setMaxLength(500)
        self.ask = QPushButton("问问")
        self.ask.clicked.connect(self.ask_ai)
        self.question.returnPressed.connect(self.ask_ai)
        row.addWidget(self.question, 1)
        row.addWidget(self.ask)
        layout.addLayout(row)
        self.chat_timer = QTimer(self)
        self.chat_timer.setInterval(120)
        self.chat_timer.timeout.connect(self.poll_chat)
        self.timer = QTimer(self)
        self.timer.setInterval(1400)
        self.timer.timeout.connect(self.refresh)
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(120)
        self.idle_timer.timeout.connect(self.animate)
        owner.installEventFilter(self)
        self.host.installEventFilter(self)
        QTimer.singleShot(0, self.start)

    def start(self):
        if not self.started:
            self.started = True
            self.move_pet(QPoint(self.host.width() - 126, self.host.height() - 300))
        self.resume()
        self.refresh()
        self.pet.show()
        self.pet.raise_()

    def eventFilter(self, obj, event):
        if obj in (self.owner, self.host):
            kind = event.type()
            if kind in (QEvent.Type.Resize, QEvent.Type.Move):
                self.arrange()
            elif kind in (QEvent.Type.Hide, QEvent.Type.Close):
                self.pause()
                if kind == QEvent.Type.Close:
                    self.chat.stop()
            elif kind in (QEvent.Type.Show, QEvent.Type.WindowStateChange):
                if self.owner.isMinimized():
                    self.pause()
                else:
                    self.resume()
        return False

    def resume(self):
        if self.owner.isVisible() and not self.owner.isMinimized():
            if not self.session.muted:
                self.timer.start()
            self.idle_timer.start()
            if self.chat.busy:
                self.chat_timer.start()

    def pause(self):
        self.animation.stop()
        self.timer.stop()
        self.idle_timer.stop()
        self.chat_timer.stop()

    def collapse(self):
        self.bubble.hide()
        self.marker.hide()

    def move_pet(self, p):
        self.pet.move(
            max(0, min(self.host.width() - 112, p.x())),
            max(0, min(self.host.height() - 112, p.y())),
        )

    def finish_drag(self):
        p = self.pet.pos()
        edges = {
            "left": p.x(),
            "right": self.host.width() - 112 - p.x(),
            "top": p.y(),
            "bottom": self.host.height() - 112 - p.y(),
        }
        self.dock = min(edges, key=edges.get) if min(edges.values()) < 30 else None
        self.set_quiet(bool(self.dock))

    def set_quiet(self, quiet):
        self.animation.stop()
        self.session.muted = quiet
        if quiet:
            self.dock = self.dock or "right"
            self.timer.stop()
            self.collapse()
        else:
            self.dock = None
            self.move_pet(
                self.pet.pos()
                + QPoint(-40 if self.pet.x() > self.host.width() / 2 else 40, 0)
            )
            self.timer.start()
            self.refresh()
            self.bubble.show()
        self.arrange()
        self.animate()

    def interact(self, pose):
        self.last_touch = time.monotonic()
        self.pose = pose
        self.pose_until = time.monotonic() + 1.2
        self.animation.stop()
        self.bounce_anchor = self.pet.pos()
        if not self.session.muted and self.pet.press is None:
            self.animation.start()
        self.animate()

    def bounce(self, value):
        if not self.session.muted and self.pet.press is None:
            self.pet.move(self.bounce_anchor + QPoint(0, int(value)))

    def animate(self):
        if not self.owner.isVisible() or self.owner.isMinimized():
            return
        self.ticks += 1
        if self.session.muted:
            name = "side" if self.dock in ("left", "right") else "peek"
            if self.ticks % 44 in (0, 1):
                name += "-blink"
        elif time.monotonic() < self.pose_until:
            name = self.pose
        elif self.state.get("busy"):
            name = "read" if self.ticks % 40 < 34 else "think"
        elif time.monotonic() - self.last_touch > 45:
            name = "sleep"
        else:
            name = "blink" if self.ticks % 44 in (0, 1) else "idle"
        pix = pet_pixmap(name)
        if self.dock == "right":
            pix = pix.transformed(QTransform().scale(-1, 1))
        self.pet.setPixmap(pix)

    def target(self):
        for w in candidates(self.owner, self.component, self.note.key):
            if isinstance(w, QWidget) and w.isVisible():
                return w
        return None

    def refresh(self):
        if self.session.muted:
            return
        self.state = read_state(self.owner, self.component)
        note = self.session.choose(self.state)
        changed = note != self.note
        if changed:
            self.note = note
            self.heading.setText("帕秋莉 · " + note.title)
            self.message.setText(note.text)
            self.action_button.setText(note.action)
            self.action_button.setVisible(bool(note.action and self.action))
            self.bubble.show()
            self.interact("think" if note.key.startswith("setup") else "wave")
        self.arrange()

    def turn(self, delta):
        self.note = self.session.turn(self.state, delta)
        self.heading.setText("帕秋莉 · " + self.note.title)
        self.message.setText(self.note.text)
        self.action_button.setText(self.note.action)
        self.action_button.setVisible(bool(self.note.action and self.action))
        self.bubble.show()
        self.interact("read")
        self.arrange()

    def arrange(self):
        if not hasattr(self, "pet") or not self.note:
            return
        self.move_pet(self.pet.pos())
        if self.dock:
            p = self.pet.pos()
            if self.dock == "right":
                p.setX(self.host.width() - 64)
            elif self.dock == "left":
                p.setX(-48)
            elif self.dock == "top":
                p.setY(-38)
            else:
                p.setY(self.host.height() - 72)
            self.pet.move(p)
        if self.session.muted:
            return
        width = min(348, max(230, self.host.width() - 24))
        self.bubble.setFixedWidth(width)
        self.message.setFixedHeight(
            max(
                self.message.fontMetrics().height() * 2,
                self.message.heightForWidth(width - 26),
            )
        )
        self.bubble.layout().activate()
        self.bubble.adjustSize()
        target = self.target()
        rect = None
        if target:
            point = target.mapTo(self.host, QPoint())
            rect = (point.x(), point.y(), target.width(), target.height())
        x, y = bubble_position(
            (self.host.width(), self.host.height()),
            rect,
            (width, self.bubble.height()),
            (self.pet.x(), self.pet.y()),
        )
        self.bubble.move(x, y)
        if target and self.bubble.isVisible():
            self.marker.setGeometry(*rect)
            self.marker.show()
            self.marker.raise_()
        else:
            self.marker.hide()
        self.bubble.raise_()
        self.pet.raise_()

    def run_action(self):
        if self.action and self.note:
            self.action(self.note.key)

    def ask_ai(self):
        if self.note and self.chat.start(
            self.question.text(), self.component, self.note, self.state
        ):
            self.asked_key = self.note.key
            self.ask.setEnabled(False)
            self.message.setText("唔，让我翻翻魔法书…")
            self.interact("read")
            self.chat_timer.start()

    def poll_chat(self):
        message = self.chat.poll()
        if message is not None:
            self.chat_timer.stop()
            self.ask.setEnabled(True)
            self.message.setText(
                message if self.note.key == self.asked_key else self.note.text
            )
            self.question.clear()
            self.interact("wave")
            self.arrange()


def install_qt_guide(owner, component, action=None):
    guide = GuideShelf(owner, component, action)
    owner.patchouli_guide = guide
    return guide
