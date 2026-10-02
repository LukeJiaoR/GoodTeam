"""Isolated, model-free owned relay/HTTP evidence. No production admission.

Reuses explicit loopback test authority/server; never a native/provider witness.
Needs development test dependencies. Only its own temporary roots/child are
cleaned. Run with PYTHONPATH=src and an explicit disposable workspace root.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_codex_bridge import bridge, request, upstream  # noqa: E402
from test_codex_relay import call, owned_relay  # noqa: E402


def probe(workspace):
    evidence = {"schema_version": "goodteam.codex-bridge-probe.v1", "verification_kind": "contract_fixture",
                "native_codex_invocations": 0, "external_model_calls": 0, "host_credentials_read": False,
                "docker_or_network_changes": False, "production_admission": False}
    with tempfile.TemporaryDirectory(prefix="codex-bridge-probe-", dir=workspace) as temporary:
        root = Path(temporary).resolve()
        mock = upstream.__wrapped__()
        server = next(mock)
        try:
            target = bridge(root / "delivery", server)
            with owned_relay(target) as (port, _, _):
                status, _, body = call(port)
                assert status == 200 and body == server.response
                again, _, denied = call(port)
                assert again == 403 and b"bridge_budget_exhausted" in denied
            assert len(server.requests) == 1
            evidence["delivery"] = {"real_local_http_pipe_path": True, "mock_upstream_requests_observed": 1,
                                    "second_request_rejected": True, "boundaries": target.boundaries()}
            server.requests.clear()
            server.accepted.clear()
            server.remote_completed.clear()
            server.hold = True
            target = bridge(root / "cancellation", server)
            with owned_relay(target) as (port, _, _):
                client = socket.create_connection(("127.0.0.1", port), timeout=2)
                body = request()
                client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nContent-Length: "
                               + str(len(body)).encode() + b"\r\n\r\n" + body)
                assert server.accepted.wait(2)
                started = time.monotonic()
                client.shutdown(socket.SHUT_RDWR)
                client.close()
                expires = time.monotonic() + 2
                while not target._retired and time.monotonic() < expires:
                    time.sleep(0.01)
                assert target._retired
                stop = target.cancel()
                assert stop.local_transport_stopped and not stop.remote_retirement_confirmed
                assert not server.remote_completed.is_set()
                elapsed = round((time.monotonic() - started) * 1000, 1)
                server.release.set()
                assert server.remote_completed.wait(2)
                evidence["cancellation"] = {"worker_disconnect_propagated": True, "stop": asdict(stop),
                    "elapsed_ms": elapsed, "mock_remote_completed_after_local_stop": True,
                    "mock_upstream_requests_observed": len(server.requests), "boundaries": target.boundaries()}
        finally:
            mock.close()
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("workspace must be an existing explicitly authorized directory")
    evidence = probe(args.workspace.resolve())
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    args.output.chmod(0o600)
    print("Model-free bridge evidence written to " + str(args.output))


if __name__ == "__main__":
    main()
