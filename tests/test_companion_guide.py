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
    assert len(ART) + len(PEEK) >= 36
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
    assert engine.frame(1).pose == "pickup-1"
    engine.drag(180, 0.05, now=1)
    frames = {engine.frame(1 + i * 0.04).pose for i in range(35)}
    assert {"hang-center", "hang-left", "hang-right", "hang-return"} <= frames
    assert abs(engine.velocity) > 0
    engine.release(now=3)
    assert engine.frame(3).pose == "land-1"
    assert engine.frame(3.2).pose == "land-4"
    assert engine.frame(3.6).pose == "idle"
    assert engine.frame(53).pose == "idle"
    assert engine.frame(54.3).pose == "blink-hold"


def test_rendering_does_not_depend_on_previous_draws_or_reuse_old_pixels():
    from PIL import Image

    first, second = PetAnimator(0), PetAnimator(0)
    first.click(1)
    second.click(1)
    for i in range(20):
        first.frame(1 + i * 0.025)
    first.last_image = Image.new("RGBA", SIZE, (255, 0, 255, 255))
    # The old renderer used last_image as another crossfade source, creating
    # multi-generation trails. The same event timeline must now be pixel-equal.
    a, b = first.frame(1.52).image, second.frame(1.52).image
    assert a.tobytes() == b.tobytes()
    assert a.getpixel((0, 0))[3] == 0
    first.frame(2)
    assert first.mode == "idle"


def test_explicit_inbetweens_and_pause_preserve_action_time():
    from suite.guide_support import WAVE_FRAMES

    engine = PetAnimator(0)
    engine.click(1)
    frames = [engine.frame(1 + i * 0.075 + 0.001).pose for i in range(len(WAVE_FRAMES))]
    assert frames == list(WAVE_FRAMES)
    shown = engine.frame(1.11).image
    engine.set_paused(True, 1.12)
    assert engine.frame(50).image.tobytes() == shown.tobytes()
    engine.set_paused(False, 50)
    assert engine.frame(50.01).pose == "wave-2"


def test_atlas_extraction_excludes_detached_noise_across_nominal_cells():
    from PIL import Image, ImageDraw
    from suite.guide_support import _sprite_objects

    atlas = Image.new("RGBA", (320, 320))
    draw = ImageDraw.Draw(atlas)
    for row in range(4):
        for col in range(4):
            x, y = col * 80 + 15, row * 80 + 8
            draw.rectangle((x, y, x + 42, y + 60), fill=(130, 80, 180, 255))
            draw.rectangle(
                (col * 80 + 76, row * 80 + 71, col * 80 + 78, row * 80 + 73),
                fill=(255, 0, 255, 255),
            )
    frames = _sprite_objects(atlas, 16)
    assert len(frames) == 16
    for frame in frames:
        assert not any(
            r == 255 and b == 255 and alpha
            for r, g, b, alpha in frame.get_flattened_data()
        )


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
    assert guide.timer.interval() == 20


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
        assert guide.pet.mask().isEmpty()


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


def test_changing_to_smaller_pose_clears_old_body_pixels_in_place(pet_window):
    from PySide6.QtGui import QPainter

    app, owner, field, guide, callback = pet_window
    guide.pet.hide()
    app.processEvents()
    background = owner.centralWidget().grab().toImage()
    guide.pet.show()
    guide.move_pet(QPoint(300, 260))
    guide.tick()
    app.processEvents()
    guide.engine.release("bottom")
    guide.engine.started -= 1
    guide.tick()
    app.processEvents()
    rect = guide.pet.geometry()
    expected = background.copy(rect)
    painter = QPainter(expected)
    painter.drawImage(0, 0, guide.pet.image)
    painter.end()
    actual = owner.centralWidget().grab().toImage().copy(rect)
    assert expected == actual


def test_windows_tk_region_refresh_invalidates_parent_and_transfers_ownership(
    monkeypatch,
):
    import ctypes
    from ctypes import wintypes
    from unittest.mock import Mock
    from suite import guide_support

    gdi, user = Mock(), Mock()
    handles = iter(range(100, 10000))
    gdi.CreateRectRgn.side_effect = lambda *args: next(handles)
    user.SetWindowRgn.return_value = 1
    user.GetParent.return_value = 44

    def get_rect(hwnd, pointer):
        rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left = rect.top = 0
        rect.right, rect.bottom = SIZE
        return 1

    user.GetClientRect.side_effect = get_rect
    monkeypatch.setattr(guide_support.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **kw: gdi if name == "gdi32" else user,
        raising=False,
    )
    guide_support.shape_tk_pet(SimpleNamespace(winfo_id=lambda: 33), sprite("idle"))
    user.InvalidateRect.assert_called_once()
    assert user.InvalidateRect.call_args.args[0] == 44
    deleted = [call.args[0] for call in gdi.DeleteObject.call_args_list]
    assert 100 not in deleted  # Transferred to Windows, never delete twice.


def test_idle_subpixel_motion_is_not_quantized_to_pixel_jumps():
    engine = PetAnimator(0)
    images = [engine.frame(i * 0.02).image.tobytes() for i in range(15)]
    assert len(set(images)) >= 12


def test_incomplete_pet_assets_fall_back_without_breaking_the_tool(monkeypatch):
    from suite import guide_support

    def broken(filename):
        raise ValueError("incomplete atlas")

    guide_support.sprite.cache_clear()
    try:
        with monkeypatch.context() as local:
            local.setattr(guide_support, "_atlas_frames", broken)
            image = guide_support.sprite("idle")
            assert image.size == SIZE and image.getbbox()
    finally:
        guide_support.sprite.cache_clear()
