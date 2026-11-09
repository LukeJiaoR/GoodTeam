"""GoodTeam-owned API connections. Agent names and API providers are separate.

Credentials stay in the operator store; jobs carry only an ID and revision.
The initial adapter is Codex's native Responses API, without a routing proxy.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from openai import APIError, APIStatusError, OpenAI

from goodteam_os.database import database_configured
from goodteam_os import state_documents
from goodteam_os.service import service_state_dir

PROVIDER_KEY_ENV = "GOODTEAM_CODEX_PROVIDER_API_KEY"
_PUBLIC_FIELDS = ("id", "revision", "agent", "name", "protocol", "base_url", "model")


class ProviderError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _text(value, label, maximum, *, optional=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ProviderError("invalid_provider", f"Invalid {label}.")
    value = value.strip()
    if not value and not optional:
        raise ProviderError("invalid_provider", f"{label} is required.")
    return value


def normalize_connection(request, previous=None):
    previous = previous or {}
    if request.get("agent", "codex") != "codex" or request.get("protocol", "responses") != "responses":
        raise ProviderError("unsupported_provider_protocol", "Codex currently supports the OpenAI Responses protocol.")
    base_url = _text(request.get("base_url", ""), "Base URL", 2048)
    try:
        url = urlsplit(base_url)
        if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(c.isspace() for c in base_url)):
            raise ValueError
        url.port
    except ValueError:
        raise ProviderError("invalid_provider_url", "Use an HTTP(S) Base URL without credentials, query or fragment.") from None
    path = url.path.rstrip("/") or "/v1"
    if path.endswith(("/responses", "/chat/completions", "/models", "/embeddings")):
        raise ProviderError("invalid_provider_url", "Enter the API base URL, such as https://api.example.com/v1.")
    model = _text(request.get("model", ""), "Model", 200, optional=True)
    if model and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model) is None:
        raise ProviderError("invalid_provider_model", "Enter a model identifier without spaces.")
    return {
        "agent": "codex", "protocol": "responses",
        "name": _text(request.get("name", ""), "Provider name", 100),
        "base_url": urlunsplit((url.scheme, url.netloc, path, "", "")), "model": model,
        "api_key": _text(request.get("api_key", previous.get("api_key", "")), "API Key", 8192, optional=True),
    }


def public_connection(value):
    return {**{key: value[key] for key in _PUBLIC_FIELDS}, "has_api_key": bool(value["api_key"])}


class ProviderStore:
    def __init__(self):
        self.root = Path(service_state_dir()).expanduser().resolve() / "providers"
        self.path = self.root / "connections.json"

    def _read(self):
        try:
            data = state_documents.read_json(self.path)
        except FileNotFoundError:
            return {"version": 1, "revision": 0, "providers": [], "active": {}}
        try:
            assert data["version"] == 1 and type(data["revision"]) is int
            assert isinstance(data["providers"], list) and isinstance(data["active"], dict)
            ids = set()
            for item in data["providers"]:
                assert re.fullmatch(r"provider-[a-f0-9]{32}", item["id"]) and item["id"] not in ids
                assert type(item["revision"]) is int and item["revision"] > 0
                assert normalize_connection(item) == {k: item[k] for k in normalize_connection(item)}
                ids.add(item["id"])
            assert all(k == "codex" and v in ids for k, v in data["active"].items())
            embedding = data.get("embeddings")
            if embedding is not None:
                assert set(embedding) == {"provider_id", "model", "revision"}
                assert embedding["provider_id"] in ids
                assert type(embedding["revision"]) is int and embedding["revision"] > 0
                assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", embedding["model"])
            return data
        except (AssertionError, KeyError, ValueError, TypeError):
            raise ProviderError("provider_store_invalid", "GoodTeam provider settings are unreadable.", 500) from None

    def read(self):
        state_documents.ensure_storage_config(self.path)
        if not database_configured():
            return {"version": 1, "revision": 0, "providers": [], "active": {}}
        with state_documents.document_lock(self.path):
            return self._read()

    def view(self):
        data = self.read()
        return {"configured": database_configured(), "storage": "postgresql",
                "revision": data["revision"], "active": data["active"],
                "embeddings": data.get("embeddings"),
                "providers": [public_connection(p) for p in data["providers"]],
                "agents": [{"id": "codex", "protocols": ["responses"]}]}

    def active(self, agent="codex"):
        data = self.read()
        return next((p for p in data["providers"] if p["id"] == data["active"].get(agent)), None)

    def embedding_connection(self):
        data = self.read()
        setting = data.get("embeddings")
        if setting is None:
            return None
        provider = next(p for p in data["providers"] if p["id"] == setting["provider_id"])
        return {**provider, "model": setting["model"], "embedding_revision": setting["revision"]}

    def update(self, request):
        if not database_configured():
            raise ProviderError("provider_database_required", "Configure GoodTeam PostgreSQL before managing providers.", 409)
        with state_documents.document_lock(self.path), state_documents.connection() as conn, conn.transaction():
            data = self._read()
            if type(request.get("expected_revision")) is not int or request["expected_revision"] != data["revision"]:
                raise ProviderError("provider_settings_changed", "Provider settings changed. Refresh and try again.", 409)
            action = request.get("action")
            identifier = request.get("id", "")
            previous = next((p for p in data["providers"] if p["id"] == identifier), None)
            if identifier and previous is None:
                raise ProviderError("provider_not_found", "Provider no longer exists.", 404)
            if action == "save":
                item = normalize_connection(request, previous)
                item.update(id=identifier or "provider-" + uuid4().hex,
                            revision=(previous["revision"] + 1 if previous else 1))
                data["providers"] = [item if p["id"] == identifier else p for p in data["providers"]]
                if previous is None:
                    if len(data["providers"]) >= 100:
                        raise ProviderError("provider_limit", "At most 100 providers can be saved.")
                    data["providers"].append(item)
            elif action == "activate":
                if request.get("agent", "codex") != "codex":
                    raise ProviderError("invalid_provider", "Unknown agent.")
                if previous is None:
                    data["active"].pop("codex", None)
                else:
                    data["active"]["codex"] = identifier
            elif action == "delete" and previous:
                if identifier in data["active"].values() or identifier == (data.get("embeddings") or {}).get("provider_id"):
                    raise ProviderError("provider_in_use", "Switch the active provider before deleting it.", 409)
                data["providers"] = [p for p in data["providers"] if p["id"] != identifier]
            elif action == "configure_embeddings":
                provider_id = request.get("provider_id", "")
                if not isinstance(provider_id, str):
                    raise ProviderError("invalid_provider", "Choose a saved embedding provider.")
                if provider_id:
                    if provider_id not in {p["id"] for p in data["providers"]}:
                        raise ProviderError("provider_not_found", "Choose a saved embedding provider.", 404)
                    model = _text(request.get("model", ""), "Embedding model", 200)
                    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model) is None:
                        raise ProviderError("invalid_provider_model", "Enter a model identifier without spaces.")
                    from goodteam_os.memory_embeddings import ensure_index
                    ensure_index(conn)
                    data["embeddings"] = {"provider_id": provider_id, "model": model, "revision": data["revision"] + 1}
                else:
                    data["embeddings"] = None
            else:
                raise ProviderError("invalid_provider_action", "Unknown provider action.")
            data["revision"] += 1
            state_documents.write_json(self.path, data, storage_root=self.root)
            return {"configured": True, "storage": "postgresql", "revision": data["revision"], "active": data["active"],
                    "embeddings": data.get("embeddings"),
                    "providers": [public_connection(p) for p in data["providers"]]}

    def capture(self):
        current = self.active()
        return {"id": current["id"], "revision": current["revision"]} if current else None

    def resolve(self, reference):
        if reference is None:
            return None  # Historical/local jobs never acquire a newly activated connection.
        data = self.read()
        for item in data["providers"]:
            if reference == {"id": item["id"], "revision": item["revision"]}:
                return item
        raise ProviderError("provider_connection_changed", "This job's provider was edited or removed. Create a new task.", 409)

    def fetch_models(self, request):
        previous = None
        if request.get("id"):
            previous = next((p for p in self.read()["providers"] if p["id"] == request["id"]), None)
            if previous is None:
                raise ProviderError("provider_not_found", "Provider no longer exists.", 404)
        connection = normalize_connection({**(previous or {}), **request})
        return fetch_models(connection)


def fetch_models(connection):
    try:
        # Explicit transport settings preserve the credential/redirect boundary.
        # Disable SDK retries so discovery stays a single bounded request.
        with OpenAI(base_url=connection["base_url"], api_key=connection["api_key"] or "",
                    max_retries=0, timeout=10,
                    http_client=httpx.Client(follow_redirects=False, timeout=10)) as client:
            with client.models.with_streaming_response.list() as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise ValueError
        payload = json.loads(raw)
        values = payload.get("data", payload.get("models"))
        if not isinstance(values, list):
            raise ValueError
        ids = {v.get("id", v.get("slug")) for v in values if isinstance(v, dict)
               and isinstance(v.get("id", v.get("slug")), str)}
        models = sorted(v for v in ids if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", v))
        return {"models": models[:2000], "source": "provider"}
    except APIStatusError as exc:
        code = "provider_auth_failed" if exc.status_code in {401, 403} else "provider_models_unavailable"
        raise ProviderError(code, "Model discovery failed. Check the Base URL and API Key.", 400) from None
    except (APIError, httpx.HTTPError, TimeoutError, OSError, ValueError, AttributeError):
        raise ProviderError("provider_models_unavailable", "Could not read the model list. You can enter a model ID manually.", 400) from None


def codex_connection_arguments(connection):
    alias = "goodteam_api"
    options = {"model_provider": alias,
               f"model_providers.{alias}": {"name": connection["name"], "base_url": connection["base_url"],
                   "wire_api": "responses", "requires_openai_auth": False,
                   **({"env_key": PROVIDER_KEY_ENV} if connection["api_key"] else {})}}
    # One inline table replaces the entire provider definition, so inherited
    # headers/auth cannot leak into the selected endpoint. Secrets are env-only.
    provider = options.pop(f"model_providers.{alias}")
    encoded = "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in provider.items()) + " }"
    return ["-c", f'model_provider={json.dumps(alias)}', "-c", f"model_providers.{alias}={encoded}"]
