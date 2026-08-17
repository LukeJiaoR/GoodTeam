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

    monkeypatch.setenv("GOODTEAM_DISABLE_TG_NETWORK", "1")
    monkeypatch.delenv("GOODTEAM_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GOODTEAM_TG_CHAT_ID", raising=False)
    monkeypatch.delenv("GOODTEAM_TG_ALLOWED_USERS", raising=False)

    def fake_telegram_request(token, method, params):
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 1}}
        if method == "editMessageText":
            return {"ok": True, "result": {"message_id": int(params.get("message_id", 1) or 1)}}
        if method == "deleteMessage":
            return {"ok": True, "result": True}
        if method == "answerCallbackQuery":
            return {"ok": True, "result": True}
        if method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": {}}

    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)


@pytest.fixture(autouse=True)
def isolate_project_registry(monkeypatch, tmp_path_factory):
    registry_dir = tmp_path_factory.mktemp("registry")
    registry_file = registry_dir / "projects.json"
    monkeypatch.setenv("GOODTEAM_PROJECTS_REGISTRY", str(registry_file))
    monkeypatch.setenv("GOODTEAM_HOME", str(registry_dir))
