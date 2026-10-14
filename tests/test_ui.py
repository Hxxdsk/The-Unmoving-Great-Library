from unittest.mock import patch

import pytest
from openpyxl import Workbook
from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from color_tool.data import read_project
from color_tool.settings import Settings
from color_tool.ui import STYLESHEET, Window


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    yield app
    # Qt 6.12 / Python 3.14 在解释器销毁前需先释放顶层窗口与回调循环。
    for window in app.topLevelWidgets():
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait_job(app, window):
    for _ in range(300):
        app.processEvents()
        if window.job is None:
            return
        QTest.qWait(10)
    raise AssertionError("background job did not complete")


def setup_window(tmp_path):
    folder = tmp_path / "CYMY9692"
    folder.mkdir()
    for name in ("颜色.png", "2.jpg"):
        Image.new("RGB", (100, 100), "black").save(folder / name)
    workbook = tmp_path / "table.xlsx"
    wb = Workbook()
    wb.active.append(["供应商款式编码", "颜色", "商品编码"])
    for i in range(1, 7):
        wb.active.append(["CYMY9692", "黑色", f"DKU264MWM1246-DY020{i}"])
    wb.save(workbook)
    window = Window(Settings(tmp_path / "prefs" / "settings.json"))
    window.set_directory(tmp_path)
    window.show()
    return window


def test_manual_ui_scan_and_export_six_copies(app, tmp_path):
    window = setup_window(tmp_path)
    window.scan_button.click()
    wait_job(app, window)
    assert window.table.rowCount() == 1
    assert not window.export_button.isEnabled()
    combo = window.table.cellWidget(0, 3)
    combo.setCurrentIndex(combo.findData("2.jpg"))
    assert window.export_button.isEnabled()
    assert window.table.item(0, 6).text() == "人工连接"
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        window.export_button.click()
        wait_job(app, window)
    assert len(list((window.last_output / "CYMY9692").glob("*.jpg"))) == 6
    assert window.open_button.isEnabled()
    assert not hasattr(window, "api_key") and not hasattr(window, "model_combo")
    window.close()


def test_shared_model_detection_and_selection_on_home_dialog(app, tmp_path):
    from suite.ai_dialog import AISettingsDialog
    from suite.shared_ai import PoolManager, PoolStore

    values = {}

    def codec(data, decrypt=False):
        if decrypt:
            return values[data]
        token = b"protected-" + str(len(values)).encode()
        values[token] = data
        return token

    manager = PoolManager(PoolStore(tmp_path / "pool.dpapi", codec))
    identifier = manager.add("test-key")
    models = [{"name": "models/gemini-flash", "display_name": "Flash"}, {"name": "models/gemini-pro", "display_name": "Pro"}]
    manager.detected(identifier, models)
    manager.select(model="models/gemini-pro")
    dialog = AISettingsDialog(manager)
    with patch("color_tool.gemini.GeminiClient.list_models", return_value=models):
        dialog.detect_button.click()
        wait_job(app, dialog)
    assert dialog.models.currentData() == "models/gemini-pro"
    dialog.models.setCurrentIndex(dialog.models.findData("models/gemini-flash"))
    assert manager.snapshot()["model"] == "models/gemini-flash"
    dialog.close()


def test_color_window_has_only_ai_switch_and_keeps_manual_workflow(app, tmp_path):
    window = setup_window(tmp_path)
    window.scan_button.click()
    wait_job(app, window)
    assert not hasattr(window, "model_combo") and not hasattr(window, "api_key")
    assert window.analyze_button.isEnabled()
    window.ai_enabled.setChecked(False)
    assert not window.analyze_button.isEnabled()
    combo = window.table.cellWidget(0, 3)
    combo.setCurrentIndex(combo.findData("2.jpg"))
    assert window.export_button.isEnabled()
    window.close()


def test_manual_output_button_without_color_chart_or_key(app, tmp_path):
    window = setup_window(tmp_path)
    (tmp_path / "CYMY9692" / "颜色.png").unlink()
    window.scan_button.click()
    wait_job(app, window)
    assert window.project.styles[0].ai_issues
    assert not window.analyze_button.isEnabled()
    combo = window.table.cellWidget(0, 3)
    combo.setCurrentIndex(combo.findData("2.jpg"))
    assert window.export_button.isEnabled()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        window.export_button.click()
        wait_job(app, window)
    assert len(list((window.last_output / "CYMY9692").glob("*.jpg"))) == 6
    window.close()
