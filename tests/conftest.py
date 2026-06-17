import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "telegram_transport_unit: allow a test to exercise the real _telegram_request wrapper with its own transport mock",
    )


@pytest.fixture(autouse=True)
def isolate_telegram_transport(monkeypatch, request):
    if request.node.get_closest_marker("telegram_transport_unit"):
        return

    def fake_telegram_request(token, method, params):
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 1}}
        if method == "editMessageText":
            return {"ok": True, "result": {"message_id": int(params.get("message_id", 1) or 1)}}
        if method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": {}}

    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)
