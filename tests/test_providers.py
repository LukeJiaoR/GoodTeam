"""Unit tests for ProviderStore and runtime environment builders."""
from __future__ import annotations

import os
import stat
import pytest

from goodteam.providers import (
    PROVIDER_KEY_ENV,
    ProviderError,
    ProviderStore,
    claude_env,
    codex_env,
    codex_runtime_args,
)


def test_provider_save_masking_and_permissions(tmp_path):
    store = ProviderStore(state_dir=tmp_path)
    saved = store.save({
        "agent": "codex",
        "name": "My OpenAI Proxy",
        "base_url": "https://proxy.example.com/v1/",
        "model": "gpt-5.4",
        "api_key": "sk-secret-value-12345",
    })
    assert saved["has_api_key"] is True
    assert "api_key" not in saved
    assert saved["base_url"] == "https://proxy.example.com/v1"

    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600

    # Updating name without resending api_key preserves stored secret
    updated = store.save({
        "id": saved["id"],
        "agent": "codex",
        "name": "Renamed Proxy",
        "base_url": "https://proxy.example.com/v1",
    })
    assert updated["has_api_key"] is True
    resolved = store.get(saved["id"])
    assert resolved["api_key"] == "sk-secret-value-12345"

    store.activate("codex", saved["id"])
    active = store.resolve_for_run("codex")
    assert active["id"] == saved["id"]
    assert store.resolve_for_run("codex", "native") is None

    args = codex_runtime_args(active)
    assert "-c" in args
    assert "sk-secret-value-12345" not in " ".join(args)
    assert codex_env(active) == {PROVIDER_KEY_ENV: "sk-secret-value-12345"}


def test_claude_provider_url_and_env(tmp_path):
    store = ProviderStore(state_dir=tmp_path)
    saved = store.save({
        "agent": "claude",
        "name": "Claude Mirror",
        "base_url": "https://claude-proxy.example.com/v1/messages",
        "model": "claude-sonnet-4-6",
        "api_key": "sk-ant-12345",
    })
    assert saved["base_url"] == "https://claude-proxy.example.com"
    env = claude_env(store.get(saved["id"]))
    assert env == {
        "ANTHROPIC_BASE_URL": "https://claude-proxy.example.com",
        "ANTHROPIC_API_KEY": "sk-ant-12345",
    }

    with pytest.raises(ProviderError):
        store.activate("codex", saved["id"])


def test_provider_rejects_bad_urls(tmp_path):
    store = ProviderStore(state_dir=tmp_path)
    with pytest.raises(ProviderError):
        store.save({"agent": "codex", "name": "Bad", "base_url": "ftp://x.com/v1"})
    with pytest.raises(ProviderError):
        store.save({"agent": "codex", "name": "Bad", "base_url": "https://user:pass@x.com/v1"})
    with pytest.raises(ProviderError):
        store.save({"agent": "codex", "name": "Bad", "base_url": "https://x.com/v1/responses"})
