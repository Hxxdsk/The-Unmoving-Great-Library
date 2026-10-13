from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from suite.hub import Hub
from suite.registry import ROOT, TOOLS


@pytest.fixture
def hub(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Hub(logs=tmp_path / "logs")
    yield window
    window.timer.stop()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_four_independent_entry_cards(hub):
    assert set(hub.buttons) == {tool.key for tool in TOOLS}


def test_children_are_detached_and_closing_hub_does_not_stop_them(hub):
    first, second = MagicMock(), MagicMock()
    first.poll.return_value = second.poll.return_value = None
    with patch("suite.hub.subprocess.Popen", side_effect=[first, second]) as popen:
        hub.launch("matcher")
        hub.launch("joiner")
        assert [call.args[0][-1] for call in popen.call_args_list] == ["matcher", "joiner"]
        assert all(str(ROOT / "suite_entry.py") in call.args[0] for call in popen.call_args_list)
        assert hub.processes["matcher"] is first and hub.processes["joiner"] is second
        hub.close()
    first.terminate.assert_not_called()
    second.terminate.assert_not_called()


def test_runtime_failure_disables_entries(hub):
    hub.environment_failed("缺少依赖")
    assert all(not button.isEnabled() for button in hub.buttons.values())
    assert "重新运行" in hub.environment_status.text()
