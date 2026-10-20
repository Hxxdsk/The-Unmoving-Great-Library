"""Qt desktop pet only: no tutorials, workflow inspection or API access."""

from PySide6.QtCore import Qt, QTimer, QEvent, QPoint
from PySide6.QtGui import QImage, QPainter, QBitmap, QRegion
from PySide6.QtWidgets import QWidget
import time
from .guide_support import (
    PetAnimator,
    PositionTween,
    SIZE,
    FRAME_MS,
    clamp_position,
    nearest_edge,
    dock_position,
)


class Pet(QWidget):
    def __init__(self, controller):
        super().__init__(controller.host)
        self.controller = controller
        self.press = None
        self.moved = False
        self.setFixedSize(*SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动移动，靠边吸附；点击互动，双击睡觉；右键暂停或继续动画。")
        self.image = QImage()

    def paintEvent(self, event):
        # QWidget repaints the parent background first. Never accumulate sprites
        # in a label or reuse a canvas containing a previous pose.
        painter = QPainter(self)
        painter.drawImage(0, 0, self.image)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            engine = self.controller.engine
            engine.set_paused(not engine.paused)
            self.controller.tick()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.controller.snap.cancel()
            self.press = event.globalPosition().toPoint()
            self.origin = self.pos()
            self.moved = False
            self.last_move = time.monotonic()
            self.last_x = self.press.x()
            self.controller.engine.pickup()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.controller.tick()

    def mouseMoveEvent(self, event):
        if self.press is None:
            return
        current = event.globalPosition().toPoint()
        delta = current - self.press
        self.moved |= delta.manhattanLength() > 5
        now = time.monotonic()
        self.controller.engine.drag(current.x() - self.last_x, now - self.last_move)
        self.last_x = current.x()
        self.last_move = now
        self.controller.move_pet(self.origin + delta)

    def mouseReleaseEvent(self, event):
        if self.press is None:
            return
        self.press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self.moved:
            self.controller.finish_drag()
        else:
            self.controller.move_pet(self.pos())
            self.controller.engine.click()
        self.controller.tick()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = None
            self.controller.engine.toggle_sleep()
            self.controller.tick()


class DesktopPet(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.hide()
        self.owner = owner
        self.host = owner.centralWidget()
        self.engine = PetAnimator()
        self.snap = PositionTween()
        self.pet = Pet(self)
        self.started = False
        self.suspended = False
        self.was_paused = False
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        owner.installEventFilter(self)
        self.host.installEventFilter(self)
        QTimer.singleShot(0, self.start)

    def start(self):
        if not self.started:
            self.started = True
            self.move_pet(
                QPoint(self.host.width() - SIZE[0] - 18, self.host.height() - 320)
            )
        self.pet.show()
        self.pet.raise_()
        self.tick()

    def eventFilter(self, obj, event):
        if obj in (self.owner, self.host):
            kind = event.type()
            if kind == QEvent.Type.Resize:
                self.arrange()
            elif kind in (QEvent.Type.Hide, QEvent.Type.Close):
                self.suspend()
            elif kind in (QEvent.Type.Show, QEvent.Type.WindowStateChange):
                if self.owner.isMinimized():
                    self.suspend()
                else:
                    self.resume()
        return False

    def suspend(self):
        if not self.suspended:
            self.suspended = True
            self.was_paused = self.engine.paused
            self.engine.set_paused(True)
        self.timer.stop()

    def resume(self):
        if self.suspended:
            self.suspended = False
            self.engine.set_paused(self.was_paused)
        self.tick()

    def move_pet(self, point):
        self.snap.cancel()
        old = self.pet.geometry()
        x, y = clamp_position(
            (point.x(), point.y()), (self.host.width(), self.host.height())
        )
        self.pet.move(x, y)
        self.host.update(old.united(self.pet.geometry()))

    def finish_drag(self):
        point = (self.pet.x(), self.pet.y())
        area = (self.host.width(), self.host.height())
        side = nearest_edge(point, area)
        self.engine.release(side)
        self.arrange(smooth=bool(side))

    def arrange(self, smooth=False):
        old = self.pet.geometry()
        area = (self.host.width(), self.host.height())
        point = (self.pet.x(), self.pet.y())
        x, y = (
            dock_position(point, area, self.engine.side)
            if self.engine.mode == "dock"
            else clamp_position(point, area)
        )
        if smooth:
            self.snap.begin(point, (x, y))
        else:
            self.snap.cancel()
            self.pet.move(x, y)
            self.host.update(old.united(self.pet.geometry()))

    def refresh(self):
        """Compatibility hook from the home window; no workflow state is read."""
        self.arrange()

    def tick(self):
        if not self.owner.isVisible() or self.owner.isMinimized():
            self.timer.stop()
            return
        moving = False
        if self.snap.started is not None and not self.engine.paused:
            old = self.pet.geometry()
            point, moving = self.snap.sample()
            self.pet.move(*point)
            self.host.update(old.united(self.pet.geometry()))
        frame = self.engine.frame()
        image = frame.image
        self.pet.image = QImage(
            image.tobytes(), image.width, image.height, QImage.Format.Format_RGBA8888
        ).copy()
        self.pet.setMask(QRegion(QBitmap.fromImage(self.pet.image.createAlphaMask())))
        self.host.update(self.pet.geometry())
        self.pet.update()
        if self.engine.paused:
            self.timer.stop()
        else:
            self.timer.start(FRAME_MS if frame.active or moving else 66)


def install_qt_guide(owner, component=None, action=None):
    # Keep old integration signatures so business components need no changes.
    owner.patchouli_guide = DesktopPet(owner)
    return owner.patchouli_guide
