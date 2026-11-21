"""Standalone local provider connections (Codex & Claude) with 0600 key storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from goodteam.config import providers_path


PROVIDER_KEY_ENV = "GOODTEAM_CODEX_PROVIDER_API_KEY"
SUPPORTED_AGENTS = ("codex", "claude")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


class ProviderError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _clean_text(value: Any, label: str, maximum: int, *, optional: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ProviderError("invalid_provider", f"Invalid {label}.")
    cleaned = value.strip()
    if not cleaned and not optional:
        raise ProviderError("invalid_provider", f"{label} is required.")
    return cleaned


def normalize_provider_input(data: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    prev = previous or {}
    agent = _clean_text(data.get("agent", prev.get("agent", "codex")), "Agent", 32).lower()
    if agent not in SUPPORTED_AGENTS:
        raise ProviderError("unsupported_agent", "Agent must be codex or claude.")
    name = _clean_text(data.get("name", prev.get("name", "")), "Provider name", 100)
    base_url = _clean_text(data.get("base_url", prev.get("base_url", "")), "Base URL", 2048)
    try:
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c.isspace() for c in base_url)):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise ProviderError("invalid_url", "Use a valid http(s) Base URL without credentials or query.") from None
    path = parsed.path.rstrip("/")
    if agent == "codex":
        path = path or "/v1"
        if path.endswith(("/responses", "/chat/completions", "/models", "/embeddings")):
            raise ProviderError("invalid_url", "Provide the API root Base URL (e.g. https://api.openai.com/v1).")
    else:
        for suffix in ("/v1/messages", "/messages", "/models"):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
    model = _clean_text(data.get("model", prev.get("model", "")), "Model", 200, optional=True)
    if model and not _MODEL_RE.fullmatch(model):
        raise ProviderError("invalid_model", "Invalid model identifier.")
    raw_key = data["api_key"] if data.get("api_key") else prev.get("api_key", "")
    api_key = _clean_text(raw_key, "API Key", 8192, optional=True)
    return {
        "agent": agent, "name": name,
        "base_url": urlunsplit((parsed.scheme, parsed.netloc, path, "", "")),
        "model": model, "api_key": api_key,
    }


def public_provider(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"], "agent": item["agent"], "name": item["name"],
        "base_url": item["base_url"], "model": item["model"],
        "has_api_key": bool(item.get("api_key")),
    }


class ProviderStore:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.path = providers_path(state_dir)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"providers": [], "active": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            return {
                "providers": list(data.get("providers") or []),
                "active": dict(data.get("active") or {}),
            }
        except (OSError, ValueError) as exc:
            raise ProviderError("provider_store_invalid", "Cannot read providers.json.", 500) from exc

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with NamedTemporaryFile("w", dir=self.path.parent, delete=False, encoding="utf-8") as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)

    def view(self) -> dict[str, Any]:
        with self._lock:
            data = self._read()
        return {
            "active": data["active"],
            "providers": [public_provider(p) for p in data["providers"]],
        }

    def get(self, provider_id: str | None) -> dict[str, Any] | None:
        if not provider_id:
            return None
        with self._lock:
            data = self._read()
        return next((p for p in data["providers"] if p["id"] == provider_id), None)

    def active_for(self, agent: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
        pid = data["active"].get(agent)
        return next((p for p in data["providers"] if p["id"] == pid), None)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            pid = str(payload.get("id") or "")
            prev = next((p for p in data["providers"] if p["id"] == pid), None) if pid else None
            if pid and prev is None:
                raise ProviderError("provider_not_found", "Provider not found.", 404)
            norm = normalize_provider_input(payload, prev)
            record = {"id": pid or f"prov-{uuid4().hex[:12]}", **norm}
            if prev:
                data["providers"] = [record if p["id"] == pid else p for p in data["providers"]]
            else:
                data["providers"].append(record)
            self._write(data)
        return public_provider(record)

    def activate(self, agent: str, provider_id: str | None) -> dict[str, Any]:
        if agent not in SUPPORTED_AGENTS:
            raise ProviderError("unsupported_agent", "Agent must be codex or claude.")
        with self._lock:
            data = self._read()
            if provider_id:
                found = next((p for p in data["providers"] if p["id"] == provider_id), None)
                if not found:
                    raise ProviderError("provider_not_found", "Provider not found.", 404)
                if found["agent"] != agent:
                    raise ProviderError("agent_mismatch", f"Provider is configured for {found['agent']}, not {agent}.")
                data["active"][agent] = provider_id
            else:
                data["active"].pop(agent, None)
            self._write(data)
        return self.view()

    def delete(self, provider_id: str) -> bool:
        with self._lock:
            data = self._read()
            before = len(data["providers"])
            data["providers"] = [p for p in data["providers"] if p["id"] != provider_id]
            for agent, active_id in list(data["active"].items()):
                if active_id == provider_id:
                    data["active"].pop(agent, None)
            if len(data["providers"]) == before:
                return False
            self._write(data)
        return True

    def resolve_for_run(self, agent: str, provider_id: str | None = None) -> dict[str, Any] | None:
        if provider_id == "native":
            return None
        if provider_id:
            found = self.get(provider_id)
            if found and found["agent"] == agent:
                return found
            raise ProviderError("provider_not_found", "Selected provider is unavailable.", 404)
        return self.active_for(agent)


def fetch_models(connection: dict[str, Any]) -> dict[str, Any]:
    base = connection["base_url"].rstrip("/")
    url = f"{base}/models" if base.endswith("/v1") or connection["agent"] == "codex" else f"{base}/v1/models"
    headers = {"Accept": "application/json"}
    if connection.get("api_key"):
        if connection["agent"] == "claude":
            headers["x-api-key"] = connection["api_key"]
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {connection['api_key']}"
    req = Request(url, headers=headers, method="GET")
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=10) as resp:
            raw = resp.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ProviderError("response_too_large", "Models payload exceeded 1MB.")
        payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ProviderError("auth_failed", "Authentication failed; check API Key.", 400) from None
        raise ProviderError("models_unavailable", f"Provider returned HTTP {exc.code}.", 400) from None
    except (URLError, OSError, ValueError) as exc:
        raise ProviderError("models_unavailable", f"Could not fetch models: {exc}", 400) from None

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data", payload.get("models"))
    else:
        items = None
    if not isinstance(items, list):
        raise ProviderError("models_unavailable", "Unexpected models response shape.", 400)
    ids = sorted({
        str(v.get("id") or v.get("slug") or "")
        for v in items if isinstance(v, dict)
        and _MODEL_RE.fullmatch(str(v.get("id") or v.get("slug") or ""))
    })
    return {"models": ids[:2000], "source": "provider"}


def test_connection(connection: dict[str, Any]) -> dict[str, Any]:
    result = fetch_models(connection)
    return {"ok": True, "model_count": len(result["models"]), "sample_models": result["models"][:8]}


def codex_runtime_args(connection: dict[str, Any] | None) -> list[str]:
    if not connection:
        return []
    alias = "goodteam_api"
    table: dict[str, Any] = {
        "name": connection["name"],
        "base_url": connection["base_url"],
        "wire_api": "responses",
        "requires_openai_auth": False,
    }
    if connection.get("api_key"):
        table["env_key"] = PROVIDER_KEY_ENV
    encoded = "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in table.items()) + " }"
    return ["-c", f"model_provider={json.dumps(alias)}", "-c", f"model_providers.{alias}={encoded}"]


def codex_env(connection: dict[str, Any] | None) -> dict[str, str]:
    if not connection or not connection.get("api_key"):
        return {}
    return {PROVIDER_KEY_ENV: str(connection["api_key"])}


def claude_env(connection: dict[str, Any] | None) -> dict[str, str]:
    if not connection:
        return {}
    env = {"ANTHROPIC_BASE_URL": str(connection["base_url"])}
    if connection.get("api_key"):
        env["ANTHROPIC_API_KEY"] = str(connection["api_key"])
    return env
