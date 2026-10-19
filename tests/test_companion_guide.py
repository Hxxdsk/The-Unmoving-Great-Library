from types import SimpleNamespace
import threading

import pytest
from PySide6.QtCore import Qt, QPoint, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLineEdit

from suite.guide_content import GuideSession, current_note, notes_for, read_state
from suite.guide_qt import install_qt_guide, pet_pixmap


def test_setup_reminders_disappear_when_configuration_is_complete():
    assert current_note("hub", {"ai_ready": False}).key == "setup-ai"
    assert current_note("hub", {"ai_ready": True}).key == "welcome"
    assert current_note("listing", {"paired": False}).key == "setup-edge"
    assert current_note("listing", {"paired": True}).key == "files"
    assert not any(
        n.key.startswith("setup-") for n in notes_for("listing", {"paired": True})
    )


def test_guidance_tracks_workflow_without_changing_owner_state():
    assert current_note("matcher", {"step": 2}).key == "pid"
    assert current_note("matcher", {"step": 3}).key == "colors"
    assert current_note("color", {"loaded": True}).key == "pair"
    assert current_note("generator", {"input": True, "master": True}).key == "review"
    assert current_note("joiner", {"loaded": True}).key == "order"
    owner = SimpleNamespace(
        products=[],
        bridge=SimpleNamespace(
            connected=threading.Event(), pairing=SimpleNamespace(data={"paired": True})
        ),
    )
    assert read_state(owner, "listing")["paired"]  # Offline is not a reason to re-pair.
    session = GuideSession("matcher")
    session.choose({"step": 1})
    session.turn({"step": 1})
    assert session.choose({"step": 3}).key == "colors"
    session.muted = True
    assert not GuideSession("matcher").muted  # Quiet state is session-only.


@pytest.fixture
def shelf():
    app = QApplication.instance() or QApplication([])
    owner = QMainWindow()
    owner.resize(1080, 900)
    owner._guide_ai_ready = True
    owner.setCentralWidget(QWidget())
    layout = QVBoxLayout(owner.centralWidget())
    field = QLineEdit("原来的操作区域")
    layout.addWidget(field, 1)
    guide = install_qt_guide(owner, "hub")
    owner.show()
    app.processEvents()
    yield app, owner, field, guide
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_guide_reserves_space_and_never_covers_input(shelf):
    app, owner, field, guide = shelf
    assert not field.geometry().intersects(guide.geometry())
    assert guide.note.key == "welcome"
    assert pet_pixmap().width() <= 76 and pet_pixmap().height() <= 76
    field.setFocus()
    QTest.mouseClick(guide.pet, Qt.MouseButton.LeftButton)
    assert field.hasFocus()
    assert field.text() == "原来的操作区域"
    assert guide.animation.state() == guide.animation.State.Running


def test_drag_to_edge_peeks_and_stops_timers_until_clicked(shelf):
    app, owner, field, guide = shelf
    QTest.mousePress(guide.pet, Qt.MouseButton.LeftButton, pos=QPoint(30, 35))
    QTest.mouseMove(guide.pet, QPoint(-30, 35))
    QTest.mouseRelease(guide.pet, Qt.MouseButton.LeftButton, pos=QPoint(-30, 35))
    app.processEvents()
    assert guide.session.muted
    assert guide.height() == 32 and guide.pet.height() == 32
    assert not guide.timer.isActive()
    assert guide.animation.state() == guide.animation.State.Stopped
    assert not guide.bubble.isVisible()
    QTest.mouseClick(guide.pet, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not guide.session.muted and guide.timer.isActive()
    owner.hide()
    app.processEvents()
    assert not guide.timer.isActive()


def test_small_window_starts_compact_and_click_expands_current_note(shelf):
    app, owner, field, guide = shelf
    owner.resize(1080, 768)
    app.processEvents()
    assert guide.compact and guide.height() == 44
    key = guide.note.key
    QTest.mouseClick(guide.pet, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not guide.compact and guide.height() == 86
    assert guide.note.key == key


@pytest.mark.parametrize("component", ["matcher", "generator", "joiner"])
def test_tk_shelf_is_reserved_and_quiet_state_cancels_callbacks(component):
    import os
    from suite.legacy import load_original, apply_palette
    from suite.registry import get_tool
    from suite.guide_tk import install_tk_guide
    import tkinter as tk

    if not os.environ.get("DISPLAY"):
        pytest.skip("Tk guide interaction needs a display")
    root = tk.Tk()
    try:
        module = load_original(component)
        apply_palette(module, component)
        owner = getattr(module, get_tool(component).app_class)(root)
        shelf = install_tk_guide(owner, component)
        root.update_idletasks()
        root.update()
        assert shelf.body.width() <= 76 and shelf.body.height() <= 76
        top = shelf.winfo_y()
        for child in root.winfo_children():
            if (
                child is not shelf
                and child.winfo_manager() == "pack"
                and child.winfo_ismapped()
            ):
                assert child.winfo_y() + child.winfo_height() <= top
        assert shelf.tick_id is not None
        shelf.canvas.event_generate("<ButtonPress-1>", x=shelf.pet_x + 25, y=28)
        shelf.canvas.event_generate("<Motion>", x=0, y=28, state=0x100)
        shelf.canvas.event_generate("<ButtonRelease-1>", x=0, y=28)
        root.update()
        assert shelf.session.muted and shelf.winfo_height() == 32
        assert shelf.tick_id is None and shelf.bounce_id is None
        shelf.set_quiet(False)
        root.update()
        assert shelf.tick_id is not None
        root.withdraw()
        root.update()
        assert shelf.tick_id is None and shelf.bounce_id is None
    finally:
        root.destroy()


def test_minimized_window_does_not_keep_guide_polling(shelf):
    app, owner, field, guide = shelf
    assert guide.timer.isActive()
    owner.showMinimized()
    app.processEvents()
    assert not guide.timer.isActive()
    owner.showNormal()
    app.processEvents()
    assert guide.timer.isActive()
