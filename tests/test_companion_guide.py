"""Offline pet regressions: frames, transitions, drag, layout and lifecycle."""

import gc
import os
from types import SimpleNamespace
import pytest
from PIL import ImageChops
from PySide6.QtCore import Qt, QPoint, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLineEdit
from suite.guide_support import PetAnimator, sprite, SIZE, ART, PEEK, nearest_edge
from suite.guide_qt import install_qt_guide


def test_pet_has_no_tutorial_or_api_code():
    from pathlib import Path
    import ast

    root = Path(__file__).resolve().parents[1]
    for filename in ("guide_qt.py", "guide_tk.py", "guide_support.py"):
        tree = ast.parse((root / "suite" / filename).read_text())
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        assert not any(
            "shared_ai" in name or "guide_content" in name for name in imports
        )
    assert not (root / "suite/guide_content.py").exists()


def test_atlas_frames_have_padding_and_peeking_has_no_detached_row():
    assert len(ART) + len(PEEK) >= 24
    for name in [*ART, *PEEK, "right", "right-blink"]:
        image = sprite(name)
        assert image.size == SIZE and image.mode == "RGBA"
        assert image.getchannel("A").crop((0, SIZE[1] - 5, *SIZE)).getbbox() is None
    for name in PEEK:
        alpha = sprite(name).getchannel("A").point([0] * 32 + [255] * 224)
        left, top, right, bottom = alpha.getbbox()
        # The reported extra fragment was separated from the head by empty rows.
        assert all(
            alpha.crop((0, y, SIZE[0], y + 1)).getbbox() for y in range(top, bottom)
        )


def test_pickup_dangling_landing_and_idle_use_different_frames():
    engine = PetAnimator(0)
    engine.frame(0)
    engine.pickup(1)
    assert engine.frame(1).pose == "lift-start"
    engine.drag(180, 0.05)
    frames = {engine.frame(1 + i * 0.04).pose for i in range(35)}
    assert {"lift", "lift-left", "lift-right", "lift-blink"} <= frames
    assert abs(engine.velocity) > 0
    engine.release(now=3)
    assert engine.frame(3).pose == "lower"
    assert engine.frame(3.2).pose == "land"
    assert engine.frame(3.6).pose == "idle"
    assert engine.frame(54).pose == "drowsy"
    assert engine.frame(54.3).pose == "sleep"


def test_changes_have_intermediate_frames_and_pause_preserves_time():
    engine = PetAnimator(0)
    before = engine.frame(0).image
    engine.click(1)
    start = engine.frame(1).image
    middle = engine.frame(1.05).image
    end = engine.frame(1.11).image
    assert ImageChops.difference(middle, start).getbbox()
    assert ImageChops.difference(middle, end).getbbox()
    engine.set_paused(True, 1.12)
    held = engine.frame(50).image
    assert ImageChops.difference(held, end).getbbox() is None
    engine.set_paused(False, 50)
    assert engine.mode == "wave"
    assert engine.frame(50.01).pose == "greet"


@pytest.fixture
def pet_window():
    app = QApplication.instance() or QApplication([])
    owner = QMainWindow()
    owner.resize(1080, 900)
    owner.setCentralWidget(QWidget())
    field = QLineEdit("原输入")
    layout = QVBoxLayout(owner.centralWidget())
    layout.addWidget(field)
    callback = []
    guide = install_qt_guide(owner, "hub", lambda *_: callback.append(True))
    owner.show()
    app.processEvents()
    yield app, owner, field, guide, callback
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_qt_pet_only_adds_character_and_leaves_inputs_and_callbacks_alone(pet_window):
    app, owner, field, guide, callback = pet_window
    assert owner.centralWidget().layout().count() == 1
    assert not hasattr(guide, "bubble") and not hasattr(guide, "chat")
    assert owner.findChildren(QLineEdit) == [field]
    field.setFocus()
    QTest.mouseClick(guide.pet, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
    assert field.hasFocus() and field.text() == "原输入"
    assert guide.engine.mode == "wave" and not callback
    assert guide.timer.interval() == 33


def test_qt_dragging_from_dock_clears_dock_and_uses_lift(pet_window):
    app, owner, field, guide, callback = pet_window
    for side, point in [
        ("left", QPoint(0, 240)),
        ("right", QPoint(952, 240)),
        ("top", QPoint(400, 0)),
        ("bottom", QPoint(400, 756)),
    ]:
        guide.move_pet(point)
        guide.finish_drag()
        assert guide.engine.mode == "dock" and guide.engine.side == side
        QTest.mousePress(guide.pet, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
        assert guide.engine.mode == "drag" and guide.engine.side is None
        guide.move_pet(QPoint(400, 300))
        guide.pet.moved = True
        QTest.mouseRelease(guide.pet, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
        assert guide.engine.mode == "land"
        assert guide.pet.pos() == QPoint(400, 300)
        guide.engine.frame(guide.engine.started + 0.6)
        guide.tick()
        assert guide.pet.image.size().width() == SIZE[0]
        assert guide.pet.mask().boundingRect().height() <= SIZE[1]


def test_qt_hidden_and_minimized_pet_stops_and_manual_pause_persists(pet_window):
    app, owner, field, guide, callback = pet_window
    owner.showMinimized()
    app.processEvents()
    assert not guide.timer.isActive() and guide.engine.paused
    owner.showNormal()
    app.processEvents()
    assert guide.timer.isActive() and not guide.engine.paused
    guide.engine.set_paused(True)
    guide.tick()
    owner.hide()
    app.processEvents()
    owner.show()
    app.processEvents()
    assert guide.engine.paused and not guide.timer.isActive()


@pytest.mark.parametrize("component", ["matcher", "generator", "joiner"])
def test_tk_pet_only_uses_one_canvas_and_stops_on_close(component):
    if not os.environ.get("DISPLAY"):
        pytest.skip("Tk requires a display")
    import tkinter as tk
    from suite.legacy import load_original, apply_palette
    from suite.registry import get_tool
    from suite.guide_tk import install_tk_guide

    root = tk.Tk()
    try:
        module = load_original(component)
        apply_palette(module, component)
        owner = getattr(module, get_tool(component).app_class)(root)
        guide = install_tk_guide(owner, component)
        root.update()
        assert not hasattr(guide, "bubble") and not hasattr(guide, "chat")
        assert guide.canvas.winfo_manager() == "place"
        guide.engine.release("right")
        guide.arrange()
        guide.tick()
        guide.press(SimpleNamespace(x_root=900, y_root=200))
        guide.drag(SimpleNamespace(x_root=600, y_root=300))
        assert guide.engine.mode == "drag" and guide.engine.side is None
        guide.release(SimpleNamespace())
        root.update()
        assert len(guide.canvas.find_all()) == 1
        assert guide.tick_id is not None
        root.withdraw()
        root.update()
        assert guide.tick_id is None and guide.engine.paused
        root.deiconify()
        root.update()
        assert guide.tick_id is not None and not guide.engine.paused
    finally:
        root.destroy()
        del owner, guide
        gc.collect()


def test_snap_has_eased_intermediate_positions_and_can_be_interrupted():
    from suite.guide_support import PositionTween

    tween = PositionTween()
    tween.begin((0, 100), (50, 100), 0)
    start, active = tween.sample(0)
    middle, active = tween.sample(0.09)
    assert start == (0, 100) and active
    assert 0 < middle[0] < 50
    end, active = tween.sample(0.18)
    assert end == (50, 100) and not active
    tween.begin((50, 100), (0, 100), 1)
    tween.cancel()
    assert tween.started is None


def test_qt_moving_repaints_the_old_pet_region_without_residue(pet_window):
    from PySide6.QtCore import QRect

    app, owner, field, guide, callback = pet_window
    guide.pet.hide()
    app.processEvents()
    baseline = owner.centralWidget().grab().toImage()
    guide.pet.show()
    guide.move_pet(QPoint(250, 280))
    guide.tick()
    app.processEvents()
    old = QRect(250, 280, *SIZE)
    guide.move_pet(QPoint(600, 300))
    guide.tick()
    app.processEvents()
    current = owner.centralWidget().grab().toImage()
    assert baseline.copy(old) == current.copy(old)
