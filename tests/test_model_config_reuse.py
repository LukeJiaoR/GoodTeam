"""Model parse reuse must never reuse stale files or caller-mutated models."""
from concurrent.futures import ThreadPoolExecutor
import errno
import os
from pathlib import Path
import threading

import pytest

from goodteam_os import models


SOURCE = """profiles:
  balanced:
    planner:
      primary: model-a
      contexts: [task, evidence]
"""


@pytest.fixture(autouse=True)
def isolated_parse_cache():
    cache = getattr(models, "_cached_model_config", None)
    if cache is not None:
        cache.cache_clear()
    yield
    if cache is not None:
        cache.cache_clear()


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def test_repeated_model_access_reads_current_file_without_reparsing(config, monkeypatch):
    parses = []
    reads = []
    parse = models._parse_yaml_mapping
    read = Path.read_text

    def counted_parse(*args, **kwargs):
        parses.append(1)
        return parse(*args, **kwargs)

    def counted_read(path, *args, **kwargs):
        if path == config:
            reads.append(1)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(models, "_parse_yaml_mapping", counted_parse)
    monkeypatch.setattr(Path, "read_text", counted_read)
    first = models.load_model_profile(config, "balanced")
    initial_parses = len(parses)
    assert initial_parses > 0
    assert models.load_model_profile(config, "balanced") == first
    assert models.list_model_profiles(config) == ["balanced"]
    assert len(reads) == 3  # No mtime-only or cross-request file-read cache.
    assert len(parses) == initial_parses


def test_same_size_same_timestamp_edit_uses_new_model_bytes(config):
    before = config.stat()
    assert models.load_model_profile(config, "balanced")["planner"]["primary"] == "model-a"
    config.write_text(SOURCE.replace("model-a", "model-b"), encoding="utf-8")
    os.utime(config, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert config.stat().st_size == before.st_size
    assert config.stat().st_mtime_ns == before.st_mtime_ns
    assert models.load_model_profile(config, "balanced")["planner"]["primary"] == "model-b"


def test_public_models_and_profile_names_do_not_share_mutable_cached_values(config):
    first = models.load_model_profile(config, "balanced")
    first["planner"]["primary"] = "caller-private"
    first["planner"]["contexts"].append("caller-private")
    first["new-role"] = {"primary": "caller-private"}
    names = models.list_model_profiles(config)
    names.append("caller-private")
    assert models.load_model_profile(config, "balanced") == {
        "planner": {"primary": "model-a", "contexts": ["task", "evidence"]},
    }
    assert models.list_model_profiles(config) == ["balanced"]


def test_deleted_model_file_is_not_served_from_cached_parse(config):
    models.load_model_profile(config, "balanced")
    config.unlink()
    with pytest.raises(FileNotFoundError):
        models.load_model_profile(config, "balanced")
    with pytest.raises(FileNotFoundError):
        models.list_model_profiles(config)


def test_unreadable_model_file_is_not_served_from_cached_parse(config, monkeypatch):
    models.load_model_profile(config, "balanced")
    read = Path.read_text

    def denied(path, *args, **kwargs):
        if path == config:
            raise PermissionError(errno.EACCES, "denied")
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        models.load_model_profile(config, "balanced")
    with pytest.raises(PermissionError):
        models.list_model_profiles(config)


def test_malformed_or_removed_profile_does_not_fall_back_to_old_models(config):
    models.load_model_profile(config, "balanced")
    config.write_text("profiles:\n  invalid line\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported YAML"):
        models.load_model_profile(config, "balanced")
    config.write_text(SOURCE.replace("balanced:", "other:"), encoding="utf-8")
    assert models.list_model_profiles(config) == ["other"]
    with pytest.raises(ValueError, match="Unknown model profile"):
        models.load_model_profile(config, "balanced")
    config.write_text(SOURCE.replace("model-a", "model-c"), encoding="utf-8")
    assert models.load_model_profile(config, "balanced")["planner"]["primary"] == "model-c"


def test_retained_model_source_versions_are_bounded(config):
    for index in range(models._MODEL_CONFIG_CACHE_SIZE + 3):
        config.write_text(SOURCE.replace("model-a", f"model-{index}"), encoding="utf-8")
        assert models.load_model_profile(config, "balanced")["planner"]["primary"] == f"model-{index}"
    assert models._cached_model_config.cache_info().currsize == models._MODEL_CONFIG_CACHE_SIZE


def test_oversized_model_config_is_parsed_but_not_retained(config):
    config.write_text(SOURCE + "#" * (models._MAX_CACHED_MODEL_CONFIG_CHARS + 1), encoding="utf-8")
    for _ in range(2):
        assert models.load_model_profile(config, "balanced")["planner"]["primary"] == "model-a"
    assert models._cached_model_config.cache_info().currsize == 0


def test_concurrent_model_consumers_receive_independent_nested_values(config):
    barrier = threading.Barrier(4)

    def consume(index):
        value = models.load_model_profile(config, "balanced")
        barrier.wait(timeout=10)
        value["planner"]["contexts"].append(str(index))
        barrier.wait(timeout=10)
        return value["planner"]["contexts"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(consume, range(4)))
    assert results == [["task", "evidence", str(index)] for index in range(4)]
    assert models.load_model_profile(config, "balanced")["planner"]["contexts"] == ["task", "evidence"]


def test_project_model_overrides_remain_current_and_do_not_mutate_cached_profile(config):
    for model in ("project-a", "project-b"):
        name, selected = models.resolve_project_model_profile(
            config, {"role_model_overrides": {"planner": model}},
        )
        assert name == "balanced"
        assert selected["planner"]["primary"] == model
    assert models.load_model_profile(config, "balanced")["planner"]["primary"] == "model-a"


def test_project_yaml_parser_does_not_use_model_profile_cache(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Project configuration must not use the model cache")

    monkeypatch.setattr(models, "_cached_model_config", forbidden)
    config = tmp_path / "project.yaml"
    for mode in ("fake", "human"):
        config.write_text(f"worker_backend:\n  mode: {mode}\n", encoding="utf-8")
        assert models._parse_simple_yaml(config) == {"worker_backend": {"mode": mode}}
