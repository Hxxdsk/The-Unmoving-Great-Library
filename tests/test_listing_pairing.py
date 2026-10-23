import json
import shutil
import subprocess
from pathlib import Path

import pytest
from websockets.sync.client import connect

from suite.listing.bridge import EdgeBridge, EXTENSION_DIR
from suite.listing.pairing import EdgePairing
from suite.listing.models import ListingError


def test_pairing_endpoint_survives_tool_shutdown_and_restart(tmp_path):
    path = tmp_path / "edge-pairing.json"
    first = EdgeBridge(path)
    endpoint = first.start().endpoint
    try:
        with connect(
            f"ws://127.0.0.1:{first.port}/extension/{first.token}",
            origin="chrome-extension://" + "a" * 32,
            proxy=None,
        ):
            assert first.connected.wait(2)
            assert first.pairing.data["paired"]
    finally:
        first.close()
    second = EdgeBridge(path)
    try:
        assert second.start().endpoint == endpoint
        assert second.pairing.data["paired"]
        assert not second.connected.is_set()
        with connect(endpoint, proxy=None) as waiting:
            with pytest.raises(Exception, match="无需重新连接"):
                waiting.recv()
        with connect(
            f"ws://127.0.0.1:{second.port}/extension/{second.token}", proxy=None
        ):
            assert second.connected.wait(2)
    finally:
        second.close()


def test_second_tool_instance_does_not_overwrite_pairing(tmp_path):
    path = tmp_path / "edge-pairing.json"
    first = EdgeBridge(path)
    first.start()
    before = path.read_bytes()
    second = EdgeBridge(path)
    try:
        with pytest.raises(ListingError, match="已有上架组件"):
            second.start()
        assert path.read_bytes() == before
    finally:
        second.close()
        first.close()


def test_only_positive_profile_evidence_reports_extension_removal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    profile = tmp_path / "Microsoft/Edge/User Data/Default/Secure Preferences"
    profile.parent.mkdir(parents=True)
    extension_id = "a" * 32
    profile.write_text(
        json.dumps({"extensions": {"settings": {extension_id: {"state": 0}}}})
    )
    pairing = EdgePairing()
    pairing.mark_connected("chrome-extension://" + extension_id)
    assert not pairing.removed()  # disabled is still installed
    profile.write_text("{partially written")
    assert not pairing.removed()
    profile.write_text(json.dumps({"extensions": {"settings": {}}}))
    assert pairing.removed()
    profile.unlink()
    assert not pairing.removed()  # unavailable profile is not proof of deletion


def test_extension_reconnects_after_server_or_worker_restart_without_focus():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node needed for extension test")
    result = subprocess.run(
        [
            node,
            str(Path(__file__).parent / "fixtures/listing_persistent_pairing.cjs"),
            str(EXTENSION_DIR / "background.js"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "persistent extension pairing OK" in result.stdout


def test_corrupt_pairing_is_reported_without_overwriting_or_starting_new_identity(
    tmp_path,
):
    path = tmp_path / "edge-pairing.json"
    path.write_text("{invalid")
    bridge = EdgeBridge(path)
    with pytest.raises(ListingError, match="原文件保留"):
        bridge.start()
    assert path.read_text() == "{invalid"
    assert bridge.thread is None


def test_offline_paired_ui_waits_instead_of_requesting_connection():
    from types import SimpleNamespace
    from suite.listing.ui import ListingWindow

    bridge = EdgeBridge()
    bridge.pairing.data["paired"] = True
    fake = SimpleNamespace(bridge=bridge)
    with pytest.raises(RuntimeError, match="无需再次连接"):
        ListingWindow.browser_config(fake)
    texts = []
    fake.connection_status = SimpleNamespace(setText=texts.append)
    ListingWindow.update_connection_status(fake)
    assert texts[-1] == "配对已记住 · 等待 Edge 扩展自动上线"


@pytest.mark.parametrize("online", [False, True])
def test_manual_connection_repairs_saved_pair_without_interrupting_live_pair(
    monkeypatch, online
):
    from types import SimpleNamespace
    import threading
    from suite.listing import ui

    opened = []
    connected = threading.Event()
    if online:
        connected.set()
    bridge = SimpleNamespace(
        connected=connected,
        pairing=SimpleNamespace(data={"paired": True}),
        start=lambda: object(),
        open_edge=lambda: opened.append(True),
    )
    signal = SimpleNamespace(connect=lambda callback: None)

    class FakeJob:
        def __init__(self, *args):
            self.result = self.failed = self.finished = signal

        def start(self):
            pass

    monkeypatch.setattr(ui, "Job", FakeJob)
    fake = SimpleNamespace(
        check_job=None,
        bridge=bridge,
        save_preferences=lambda: None,
        error=lambda message: pytest.fail(message),
        log=SimpleNamespace(appendPlainText=lambda message: None),
        busy=lambda active: None,
    )
    ui.ListingWindow.check_login(fake)
    assert opened == ([] if online else [True])
