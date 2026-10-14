import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QPalette, QWheelEvent
from PySide6.QtWidgets import QApplication

from color_tool.credentials import KeyStore, _dpapi
from color_tool.data import Style
from color_tool.gemini import GeminiClient
from color_tool.settings import Settings
from color_tool.ui import Window, STYLESHEET
from suite.widgets import ClickOnlyComboBox, FollowLog
from runtime_probe import source_hash, validate_report


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    yield app
    for window in app.topLevelWidgets():
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def fake_codec():
    values = {}
    def codec(data, decrypt=False):
        if decrypt:
            return values[data]
        token = b"opaque-protected-value-" + str(len(values)).encode()
        values[token] = data
        return token
    return codec


def test_saved_key_and_validated_model_restore_without_plaintext(app, tmp_path):
    settings = Settings(tmp_path / "preferences.json")
    settings.set("model", "models/gemini-test")
    store = KeyStore(tmp_path / "key.dpapi", fake_codec())
    store.save("fixture-gemini-key", "models/gemini-test")
    window = Window(settings, store)
    assert window.api_key.text() == "fixture-gemini-key"
    assert window._models_verified
    window.api_key.clear()
    window.persist_key()
    assert store.load() == "fixture-gemini-key"  # 临时清空不会忘记之前保存的 Key。
    window.forget_key()
    assert not store.path.exists()
    assert "fixture-gemini-key" not in settings.path.read_text()
    window.close()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 需要 Windows")
def test_windows_dpapi_round_trip_and_no_plaintext(tmp_path):
    store = KeyStore(tmp_path / "protected.dpapi")
    store.save("fake-api-key-for-windows-test", "models/gemini-test")
    assert b"fake-api-key-for-windows-test" not in store.path.read_bytes()
    assert store.load() == "fake-api-key-for-windows-test"
    assert store.validated_model == "models/gemini-test"
    store.forget()


def test_wheel_cannot_change_image_selection(app):
    combo = ClickOnlyComboBox()
    combo.addItems(["1.jpg", "2.jpg", "3.jpg"])
    combo.setCurrentIndex(1)
    combo.show()
    combo.setFocus()
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(combo, wheel)
    assert combo.currentIndex() == 1
    combo.close()


def test_layout_prioritizes_seven_color_rows_and_readable_selection(app, tmp_path):
    window = Window(Settings(tmp_path / "prefs.json"))
    window.resize(1400, 880)
    window.show()
    app.processEvents()
    assert window.gallery.isHidden() and window.log.isHidden()
    assert window.table.viewport().height() >= 7 * 46
    assert window.table.palette().color(QPalette.ColorRole.HighlightedText).name() == "#302341"
    window.resize(1366, 768)
    window.setup_toggle.setChecked(True)
    app.processEvents()
    assert window.table.viewport().height() >= 7 * 46
    window.close()


def test_log_follows_latest_and_can_pause(app):
    log = FollowLog()
    log.resize(400, 100)
    log.show()
    for n in range(120):
        log.appendPlainText(f"line {n}")
    app.processEvents()
    assert log.verticalScrollBar().value() == log.verticalScrollBar().maximum()
    log.follow_latest = False
    log.verticalScrollBar().setValue(0)
    log.appendPlainText("new line")
    assert log.verticalScrollBar().value() == 0
    log.close()


def test_black_candidate_is_marked_for_review_in_one_request(tmp_path):
    chart, image = tmp_path / "颜色.png", tmp_path / "2.jpg"
    Image.new("RGB", (40, 40), "white").save(chart)
    Image.new("RGB", (40, 40), "black").save(image)
    style = Style("A", colors={"黑色": ["sku"]}, folder=tmp_path, chart=chart, images=[image])
    first = {"filename": "2.jpg", "color_label": "黑色", "main_color": "黑色", "confidence": .82,
             "needs_review": True, "reason": "反光造成不确定"}
    reply = {"colors": [{"label": "黑色", "description": "黑色衣服"}], "images": [first], "alternatives": []}
    client = GeminiClient("test-key", "models/gemini-test")
    with patch.object(client, "generate", return_value=reply) as generate:
        client.analyze_style(style, force=True)
    assert generate.call_count == 1
    assert style.selections == {"黑色": "2.jpg"}
    assert "待核对" in style.review_notes["黑色"]
    assert "黑色" in style.suggested_colors


def test_uncertain_review_does_not_force_black_match(tmp_path):
    chart, image = tmp_path / "颜色.png", tmp_path / "2.jpg"
    Image.new("RGB", (40, 40), "white").save(chart)
    Image.new("RGB", (40, 40), "#303041").save(image)
    style = Style("A", colors={"黑色": ["sku"]}, folder=tmp_path, chart=chart, images=[image])
    uncertain = {"filename": "2.jpg", "color_label": "黑色", "main_color": "深蓝或黑色", "confidence": .6,
                 "needs_review": True, "reason": "黑色和深蓝不能确认"}
    with patch.object(GeminiClient, "generate", return_value={
            "colors": [{"label": "黑色", "description": "黑"}], "images": [uncertain], "alternatives": []}):
        GeminiClient("test-key", "models/gemini-test").analyze_style(style, force=True)
    assert style.suggested_colors == {"黑色"}
    assert style.matches["2.jpg"].needs_review
    assert "待核对" in style.review_notes["黑色"]


def test_recent_validation_only_reused_for_same_source_and_python():
    import sys
    report = {"ready": True, "python": sys.executable, "checked_at": time.time(),
              "source_hash": source_hash(), "checks": [{"ok": True}]}
    assert validate_report(report)
    assert not validate_report(dict(report, checked_at=time.time() - 60))
    assert not validate_report(dict(report, source_hash="old"))
    assert not validate_report(dict(report, checks=[]))


def test_duplicate_extension_registration_is_rejected():
    from suite.registry import TOOLS, Tool, register_tool
    with pytest.raises(ValueError, match="重复"):
        register_tool(Tool("color", "重复", "", ""))


def test_hub_reuses_fresh_native_validation_without_second_probe(app, tmp_path):
    import sys
    from suite.hub import Hub
    report = {"ready": True, "python": sys.executable, "checked_at": time.time(),
              "source_hash": source_hash(), "checks": [{"name": "环境", "ok": True}],
              "version": "3.14", "architecture": "test"}
    with patch.object(Hub, "check_environment") as probe:
        hub = Hub(tmp_path / "logs", report)
        app.processEvents()
        probe.assert_not_called()
        assert all(button.isEnabled() for button in hub.buttons.values())
        hub.close()


def test_shared_key_retains_independent_validated_models(tmp_path):
    store = KeyStore(tmp_path / "key.dpapi", fake_codec())
    store.save("first-key", "models/color-model")
    store.save("first-key", "models/generator-model")
    assert store.load() == "first-key"
    assert store.validated_models == {"models/color-model", "models/generator-model"}
    store.save("changed-key", "models/second-account")
    assert store.load() == "changed-key"
    assert store.validated_models == {"models/second-account"}
