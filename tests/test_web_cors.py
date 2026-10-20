"""CORS response-header policy, independent of POST execution authority."""

import threading
from http.client import HTTPConnection

import pytest

from goodteam_os import web


@pytest.mark.parametrize("port", [80, 443, 8788])
@pytest.mark.parametrize("scheme,default_port", [("http", 80), ("https", 443)])
@pytest.mark.parametrize("hostname", ["localhost", "127.0.0.1", "[::1]"])
def test_cors_omitted_port_matches_explicit_scheme_default(port, scheme, default_port, hostname):
    implicit = f"{scheme}://{hostname}"
    explicit = f"{implicit}:{default_port}"
    # HTTPS keeps the existing 443 policy; HTTP matches the listener port.
    allowed = scheme == "https" or port == default_port
    assert web._allowed_cors_origin(implicit, host="127.0.0.1", port=port) == (implicit if allowed else "")
    assert web._allowed_cors_origin(explicit, host="127.0.0.1", port=port) == (explicit if allowed else "")


@pytest.mark.parametrize("origin,allowed", [
    ("http://localhost:8788", True),
    ("https://localhost:443", True),
    ("http://localhost:443", False),
    ("https://localhost:8788", False),
    ("http://localhost:0", False),
    ("https://localhost:0", False),
    ("http://localhost:65535", False),
    ("https://localhost:444", False),
    ("http://untrusted.invalid:8788", False),
    ("ftp://localhost:8788", False),
    ("null", False),
    ("", False),
])
def test_cors_explicit_port_and_origin_policy_is_unchanged(origin, allowed):
    assert web._allowed_cors_origin(origin, host="127.0.0.1", port=8788) == (origin if allowed else "")


@pytest.mark.parametrize("host,origin,allowed", [
    ("dashboard.internal", "http://dashboard.internal:8788", True),
    ("192.168.1.10", "http://192.168.1.10:8788", True),
    ("0.0.0.0", "http://0.0.0.0:8788", False),
    ("::", "http://[::]:8788", False),
    ("0.0.0.0", "http://localhost:8788", True),
    ("::", "http://[::1]:8788", True),
])
def test_cors_configured_host_allowlist_is_unchanged(host, origin, allowed):
    assert web._allowed_cors_origin(origin, host=host, port=8788) == (origin if allowed else "")


def test_http_response_does_not_allow_omitted_default_port_on_nondefault_listener(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "context_estimate_for_request", lambda workspace, body: calls.append(body) or {"ok": True})
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        assert server.server_port != 80
        origins = [None, "http://127.0.0.1", "http://127.0.0.1:80", f"http://127.0.0.1:{server.server_port}"]
        for origin in origins:
            connection = HTTPConnection(*server.server_address, timeout=5)
            try:
                headers = {"Content-Type": "application/json"}
                if origin is not None:
                    headers["Origin"] = origin
                connection.request("POST", "/api/context-estimate", "{}", headers)
                response = connection.getresponse()
                assert response.status == 200  # CORS headers do not grant POST authority.
                expected = origin if origin == origins[-1] else None
                assert response.getheader("Access-Control-Allow-Origin") == expected
                assert response.getheader("Vary") == ("Origin" if expected else None)
                response.read()
            finally:
                connection.close()
        assert calls == [{}, {}, {}, {}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
