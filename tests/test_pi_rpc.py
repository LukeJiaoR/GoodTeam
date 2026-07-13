import json
import threading
from pathlib import Path

import pytest

from goodteam_os.pi_rpc import PiRpcError, PiRpcProcess, PiRpcTimeout


FAKE_PI = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"


def start_client(tmp_path: Path, monkeypatch, **env: str) -> PiRpcProcess:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client = PiRpcProcess(
        [str(FAKE_PI), "--mode", "rpc", "--session-dir", str(tmp_path / "sessions")],
        cwd=tmp_path,
        stderr_path=tmp_path / "stderr.txt",
    )
    client.start()
    return client


def test_rpc_correlates_responses_and_uses_strict_jsonl(tmp_path, monkeypatch):
    client = start_client(tmp_path, monkeypatch)
    try:
        state = client.request({"type": "get_state"}, timeout=2)
        assert state["data"]["sessionId"] == "fake-session-1"
        assert state["id"].startswith("gt-")
    finally:
        client.close()


def test_rpc_serializes_concurrent_writes(tmp_path, monkeypatch):
    client = start_client(tmp_path, monkeypatch)
    responses: list[dict[str, object]] = []

    def request() -> None:
        responses.append(client.request({"type": "get_state"}, timeout=2))

    threads = [threading.Thread(target=request) for _ in range(12)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(responses) == 12
        assert len({response["id"] for response in responses}) == 12
    finally:
        client.close()


def test_rpc_malformed_json_fails_and_preserves_bounded_error(tmp_path, monkeypatch):
    client = start_client(tmp_path, monkeypatch, FAKE_PI_MALFORMED="1")
    try:
        with pytest.raises(PiRpcError, match="Malformed Pi RPC JSON"):
            client.request({"type": "get_state"}, timeout=2)
    finally:
        client.close()


def test_rpc_unknown_response_id_is_protocol_error(tmp_path, monkeypatch):
    client = start_client(tmp_path, monkeypatch, FAKE_PI_UNKNOWN_ID="1")
    try:
        with pytest.raises(PiRpcError, match="Unknown Pi RPC response id"):
            client.request({"type": "get_state"}, timeout=2)
    finally:
        client.close()


def test_rpc_request_timeout_is_bounded(tmp_path, monkeypatch):
    client = start_client(tmp_path, monkeypatch, FAKE_PI_TIMEOUT_COMMAND="get_state")
    try:
        with pytest.raises(PiRpcTimeout):
            client.request({"type": "get_state"}, timeout=0.05)
    finally:
        client.close()


def test_rpc_stderr_is_redacted(tmp_path, monkeypatch):
    script = tmp_path / "stderr_pi.py"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\nprint('API_KEY=super-secret-value', file=sys.stderr)\nfor line in sys.stdin: pass\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    client = PiRpcProcess([str(script)], cwd=tmp_path, stderr_path=tmp_path / "stderr.txt")
    client.start()
    client.close()
    evidence = (tmp_path / "stderr.txt").read_text(encoding="utf-8")
    assert "super-secret-value" not in evidence
    assert "<redacted>" in evidence


def test_protocol_records_are_json_serializable(tmp_path, monkeypatch):
    records: list[dict[str, object]] = []
    client = PiRpcProcess(
        [str(FAKE_PI), "--mode", "rpc", "--session-dir", str(tmp_path / "sessions")],
        cwd=tmp_path,
        stderr_path=tmp_path / "stderr.txt",
        on_record=records.append,
    )
    client.start()
    try:
        client.request({"type": "get_state"}, timeout=2)
        assert json.loads(json.dumps(records[-1]))["type"] == "response"
    finally:
        client.close()
