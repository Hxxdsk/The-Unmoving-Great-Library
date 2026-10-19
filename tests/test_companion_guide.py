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


def test_pet_floats_without_reserving_layout_space_and_keeps_focus(shelf):
    app, owner, field, guide = shelf
    assert owner.centralWidget().layout().count() == 1
    assert guide.pet.parent() is owner.centralWidget()
    assert pet_pixmap().size().width() == 112
    field.setFocus()
    QTest.mouseClick(guide.pet, Qt.MouseButton.LeftButton)
    assert field.hasFocus() and field.text() == "原来的操作区域"
    assert guide.pose == "wave"
    guide.move_pet(QPoint(300, 230))
    assert guide.pet.pos() == QPoint(300, 230)


def test_four_edges_use_new_peeking_art_and_resume_on_click(shelf):
    app, owner, field, guide = shelf
    for edge, point in [
        ("left", QPoint(0, 250)),
        ("right", QPoint(968, 250)),
        ("top", QPoint(400, 0)),
        ("bottom", QPoint(400, 788)),
    ]:
        guide.move_pet(point)
        guide.finish_drag()
        assert guide.session.muted and guide.dock == edge
        assert not guide.timer.isActive() and not guide.bubble.isVisible()
        assert guide.idle_timer.isActive()  # Occasional peeking blink, no AI calls.
        QTest.mouseClick(guide.pet, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert not guide.session.muted and guide.timer.isActive()
    assert pet_pixmap("peek").cacheKey() != pet_pixmap("idle").cacheKey()


def test_minimized_or_hidden_windows_pause_animation_and_polling(shelf):
    app, owner, field, guide = shelf
    owner.showMinimized()
    app.processEvents()
    assert not guide.timer.isActive() and not guide.idle_timer.isActive()
    owner.showNormal()
    app.processEvents()
    assert guide.timer.isActive() and guide.idle_timer.isActive()
    owner.hide()
    app.processEvents()
    assert not guide.timer.isActive() and not guide.idle_timer.isActive()


def test_tutorial_targets_configuration_and_does_not_cover_it(shelf):
    from PySide6.QtWidgets import QPushButton

    app, owner, field, guide = shelf
    button = QPushButton("API", owner.centralWidget())
    button.setGeometry(400, 250, 140, 40)
    button.show()
    owner.configure = button
    owner._guide_ai_ready = False
    guide.refresh()
    app.processEvents()
    assert guide.target() is button
    assert guide.marker.geometry() == button.geometry()
    assert not guide.bubble.geometry().intersects(button.geometry())


@pytest.mark.parametrize("component", ["matcher", "generator", "joiner"])
def test_tk_pet_floats_and_stops_when_hidden(component):
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
        guide = install_tk_guide(owner, component)
        root.update()
        assert guide.canvas.winfo_manager() == "place"
        assert guide.tick_id is not None
        guide.pet_x = 0
        guide.pet_y = 200
        guide.dock = "left"
        guide.set_quiet(True)
        root.update()
        assert guide.session.muted and not guide.bubble.winfo_manager()
        assert guide.tick_id is None
        guide.set_quiet(False)
        root.update()
        assert guide.tick_id is not None
        root.withdraw()
        root.update()
        assert guide.tick_id is None and guide.bounce_id is None
    finally:
        root.destroy()
        del owner, guide
        import gc

        gc.collect()  # Dispose Tk image/variable cycles on the UI thread.


def test_ai_chat_is_opt_in_async_and_sends_no_private_paths(monkeypatch):
    import time
    from suite.guide_support import ChatRequest
    from suite.guide_content import Note
    from suite import shared_ai

    prompts = []

    class Client:
        def generate(self, parts, schema):
            prompts.append(parts[0]["text"])
            return {"reply": "先选择主数据表哦。"}

    monkeypatch.setattr(shared_ai, "shared_client", lambda **kwargs: Client())
    chat = ChatRequest()
    assert not prompts and not chat.busy
    assert chat.start(
        "下一步？",
        "generator",
        Note("files", "准备", "选择资料"),
        {"master": False, "path": "/secret/file.xlsx"},
    )
    for _ in range(100):
        reply = chat.poll()
        if reply is not None:
            break
        time.sleep(0.005)
    assert reply == "先选择主数据表哦。" and not chat.busy
    assert "/secret" not in prompts[0]


def test_missing_master_has_specific_guidance():
    note = current_note("generator", {"input": True, "master": False})
    assert "还需要选择主数据表" in note.text
