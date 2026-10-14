import io
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from color_tool.credentials import KeyStore
from color_tool.gemini import GeminiError, GeminiHTTPError, retry_delay
from suite.shared_ai import (
    PoolStore,
    PoolManager,
    PoolBroker,
    RemotePool,
    shared_client,
)

MODEL = "models/gemini-3.5-flash-lite"
OTHER = "models/gemini-pro"


def codec():
    values = {}

    def convert(data, decrypt=False):
        if decrypt:
            return values[data]
        token = b"protected-" + str(len(values)).encode()
        values[token] = data
        return token

    return convert


@pytest.fixture
def pool(tmp_path):
    manager = PoolManager(PoolStore(tmp_path / "gemini-pool.dpapi", codec()))
    for key in ("private-key-one", "private-key-two"):
        identifier = manager.add(key)
        manager.detected(identifier, [{"name": MODEL}])
    manager.select(identifier=manager.snapshot()["entries"][0]["id"], model=MODEL)
    return manager


def http_error(status=429, retry=120):
    import urllib.error

    return urllib.error.HTTPError(
        "https://example.test",
        status,
        "Failed",
        {"Retry-After": str(retry)},
        io.BytesIO(json.dumps({"error": {"message": "quota exceeded"}}).encode()),
    )


def success(result=None):
    return io.BytesIO(
        json.dumps(
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"text": json.dumps(result or {"ok": True})}]
                        },
                    }
                ]
            }
        ).encode()
    )


def test_429_retries_identical_payload_with_next_key_and_retains_model(pool):
    client = shared_client(pool=pool)
    log = []
    client.log = log.append
    schema = {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}}
    parts = [
        {"text": "identify one image"},
        {"inlineData": {"mimeType": "image/png", "data": "same-image"}},
    ]
    with patch(
        "urllib.request.urlopen", side_effect=[http_error(), success()]
    ) as request:
        assert client.generate(parts, schema) == {"ok": True}
    first, second = [call.args[0] for call in request.call_args_list]
    assert first.get_header("X-goog-api-key") == "private-key-one"
    assert second.get_header("X-goog-api-key") == "private-key-two"
    assert first.full_url == second.full_url and MODEL in first.full_url
    assert first.data == second.data
    assert "private-key" not in "\n".join(log)
    assert pool.acquire(MODEL, [])["key"] == "private-key-two"


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_parameter_permission_errors_do_not_rotate(pool, status):
    with patch("urllib.request.urlopen", side_effect=http_error(status)) as request:
        with pytest.raises(GeminiHTTPError) as error:
            shared_client(pool=pool)._request(
                "POST", "/" + MODEL + ":generateContent", {"same": "payload"}
            )
    assert error.value.status == status and request.call_count == 1
    assert not pool.cooldowns


def test_exhausted_keys_only_attempted_once_then_shared_cooldown(pool):
    now = [1000.0]
    pool.clock = lambda: now[0]
    client = shared_client(pool=pool)
    with patch(
        "urllib.request.urlopen",
        side_effect=[http_error(retry=120), http_error(retry=120)],
    ) as request:
        with pytest.raises(GeminiError, match="均已限额"):
            client._request("POST", "/generate", {"same": True})
        assert request.call_count == 2
        with pytest.raises(GeminiError, match="均已限额"):
            shared_client(pool=pool)._request("POST", "/generate", {"same": True})
        assert request.call_count == 2
    now[0] += 121
    assert pool.acquire(MODEL, [])["key"] == "private-key-one"


def test_incompatible_key_skipped_and_global_model_change_does_not_change_job(pool):
    snapshot = pool.snapshot()
    first, second = snapshot["entries"]
    third = pool.add("private-key-three")
    pool.detected(third, [{"name": MODEL}, {"name": OTHER}])
    pool.detected(second["id"], [{"name": OTHER}])
    pool.select(identifier=first["id"], model=MODEL)
    client = shared_client(pool=pool)
    pool.select(model=OTHER)
    with patch(
        "urllib.request.urlopen", side_effect=[http_error(), success()]
    ) as request:
        assert client.generate([{"text": "same"}], {"type": "OBJECT"}) == {"ok": True}
    calls = [call.args[0] for call in request.call_args_list]
    assert [c.get_header("X-goog-api-key") for c in calls] == [
        "private-key-one",
        "private-key-three",
    ]
    assert all(MODEL in c.full_url for c in calls)
    assert shared_client(pool=pool).model == OTHER


def test_cancel_after_rate_limit_does_not_send_next_request(pool):
    cancel = threading.Event()
    client = shared_client(cancel, pool=pool, log=lambda _: cancel.set())
    with patch("urllib.request.urlopen", side_effect=http_error()) as request:
        with pytest.raises(GeminiError, match="取消"):
            client._request("POST", "/generate", {})
    assert request.call_count == 1


def test_invalid_response_does_not_rotate_or_return_partial_result(pool):
    invalid = io.BytesIO(b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}')
    with patch("urllib.request.urlopen", return_value=invalid) as request:
        with pytest.raises(GeminiError, match="未完整"):
            shared_client(pool=pool).generate([{"text": "same"}], {"type": "OBJECT"})
    assert request.call_count == 1 and not pool.cooldowns


def test_encrypted_pool_migrates_legacy_and_remembers_global_selection(tmp_path):
    convert = codec()
    KeyStore(tmp_path / "gemini-key.dpapi", convert).save("legacy-private-key", MODEL)
    (tmp_path / "preferences.json").write_text(json.dumps({"model": MODEL}))
    store = PoolStore(tmp_path / "gemini-pool.dpapi", convert)
    manager = PoolManager(store)
    assert manager.snapshot()["model"] == MODEL
    assert manager.snapshot()["entries"][0]["key"] == "legacy-private-key"
    manager.select(model=MODEL)
    assert b"legacy-private-key" not in store.path.read_bytes()
    assert not (tmp_path / "gemini-key.dpapi").exists()
    restored = PoolManager(store)
    assert restored.snapshot() == manager.snapshot()
    restored.remove(restored.snapshot()["entries"][0]["id"])
    assert PoolManager(store).snapshot()["entries"] == []


def test_broker_shares_live_configuration_and_cooldown_with_child_process(pool):
    broker = PoolBroker(pool)
    try:
        remote = RemotePool(broker.url, broker.token)
        first = remote.acquire(MODEL, [])
        env = os.environ.copy()
        env.update(broker.environment())
        script = (
            "from suite.shared_ai import shared_pool; p=shared_pool(); s=p.snapshot(); "
            'e=p.acquire(s["model"], []); p.cooldown(e["id"],120,s["model"]); '
            'print(s["model"], len(s["entries"]))'
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == MODEL + " 2"
        assert remote.acquire(MODEL, [])["id"] != first["id"]
        third = pool.add("private-key-three")
        pool.detected(third, [{"name": MODEL}])
        assert len(remote.snapshot()["entries"]) == 3
        with pytest.raises(GeminiError, match="无法读取"):
            RemotePool(broker.url, "wrong-token").snapshot()
    finally:
        broker.close()


def test_threaded_requests_do_not_mix_keys_payloads_or_models(pool):
    client = shared_client(pool=pool)
    sent = []
    lock = threading.Lock()

    def transport(request, timeout):
        body = json.loads(request.data)
        with lock:
            sent.append((request.full_url, request.get_header("X-goog-api-key"), body))
        text = body["contents"][0]["parts"][0]["text"]
        return success({"id": text})

    with patch("urllib.request.urlopen", side_effect=transport), ThreadPoolExecutor(
        max_workers=2
    ) as executor:
        results = list(
            executor.map(
                lambda text: client.generate([{"text": text}], {"type": "OBJECT"}),
                ["style-A", "style-B"],
            )
        )
    assert results == [{"id": "style-A"}, {"id": "style-B"}]
    assert {call[2]["contents"][0]["parts"][0]["text"] for call in sent} == {
        "style-A",
        "style-B",
    }
    assert all(call[1] == "private-key-one" and MODEL in call[0] for call in sent)


def test_retry_after_header_and_google_retry_info():
    assert (
        retry_delay(
            {}, {"details": [{"@type": "google.rpc.RetryInfo", "retryDelay": "3s"}]}
        )
        == 3
    )
    assert retry_delay({"Retry-After": "120"}, {}) == 120
    assert (
        retry_delay(
            {},
            {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "180s",
                    }
                ]
            },
        )
        == 180
    )
    assert retry_delay({"Retry-After": "nan"}, {}) == 60


def test_grounded_standard_lookup_rotates_without_losing_citations(pool):
    payload = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [{"text": json.dumps({"standard": "GB/T 00000"})}]
                },
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://openstd.samr.gov.cn/evidence"}}
                    ]
                },
            }
        ]
    }
    with patch(
        "urllib.request.urlopen",
        side_effect=[http_error(), io.BytesIO(json.dumps(payload).encode())],
    ) as request:
        result, citations = shared_client(pool=pool).grounded(
            "verify the latest garment standard"
        )
    assert result == {"standard": "GB/T 00000"}
    assert citations == payload["candidates"][0]["groundingMetadata"]
    first, second = [call.args[0] for call in request.call_args_list]
    assert first.data == second.data and first.full_url == second.full_url
    assert json.loads(second.data)["tools"] == [{"googleSearch": {}}]


def test_tk_tools_have_only_ai_switch_and_use_shared_client():
    import tkinter as tk
    import gc
    from tkinter import ttk
    from suite.ai_controls import GeminiPanel

    root = tk.Tk()
    root.withdraw()
    try:
        panel = GeminiPanel(root, component="generator")
        assert not any(
            isinstance(w, (ttk.Entry, ttk.Combobox)) for w in panel.winfo_children()
        )
        assert any(isinstance(w, ttk.Checkbutton) for w in panel.winfo_children())
        with patch(
            "suite.ai_controls.shared_client", return_value="shared-client"
        ) as factory:
            assert panel.client() == "shared-client"
            panel.enabled.set(False)
            assert panel.client() is None
            assert factory.call_count == 1
    finally:
        root.destroy()
        del panel
        gc.collect()
