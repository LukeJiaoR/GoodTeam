"""Probe-contract tests. Simulated events do not certify any real Runtime."""
import json
import os
from pathlib import Path
import sys

import pytest

from goodteam_os import skill_conformance as probe


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "probe"
    root.mkdir()
    return root, probe.make_fixtures(root)


def command_event(root, action, result, exit_code=0):
    return {
        "type": "item.completed",
        "item": {"id": "event-1", "type": "command_execution",
                 "command": ["python3", str(root / "tools/probe_helper.py"), action],
                 "exit_code": exit_code, "aggregated_output": json.dumps(result)},
    }


def message_event(text):
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}


def test_missing_binary_records_unverified_without_live_attempt(tmp_path):
    report = probe.run_probe(output=tmp_path / "out", binary="/missing/definitely-not-a-codex")
    assert report["cli_execution_attempts"] == 0
    assert report["provider_requests"] == 0
    assert report["runtime"]["status"] == "unverified"
    assert not report["strict_binding_supported"]
    assert json.loads((tmp_path / "out/report.json").read_text()) == report
    assert not (tmp_path / "out/repo").exists()


def test_clean_environment_does_not_inherit_credentials_or_configuration(tmp_path, monkeypatch):
    for key in ("CODEX_API_KEY", "OPENAI_API_KEY", "HTTP_PROXY", "BASH_ENV", "PYTHONPATH", "CODEX_HOME"):
        monkeypatch.setenv(key, "must-not-inherit")
    env = probe.clean_environment(tmp_path)
    assert set(env) == {"PATH", "LANG", "HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "TMPDIR"}
    assert all("must-not-inherit" not in value for value in env.values())
    assert Path(env["HOME"]).is_relative_to(tmp_path)


def test_inspect_only_calls_help_and_version(tmp_path, monkeypatch):
    commands = []

    def capture(command, **kwargs):
        commands.append(command[1:])
        assert "CODEX_API_KEY" not in kwargs["env"]
        text = "codex-cli 1.2.3" if command[-1] == "--version" else " ".join(probe.REQUIRED_FLAGS)
        return {"status": "completed", "exit_code": 0, "stdout": text, "stderr": ""}

    monkeypatch.setattr(probe, "capture", capture)
    report = probe.inspect_runtime(sys.executable, tmp_path)
    assert commands == [["--version"], ["exec", "--help"]]
    assert report["version"] == "1.2.3"
    assert report["status"] == "supported"
    assert report["binary_digest"] == probe.binary_digest(Path(sys.executable).resolve())


def test_missing_flags_are_unsupported_not_silently_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "capture", lambda *a, **k: {
        "status": "completed", "exit_code": 0, "stdout": "codex-cli 1.2.3", "stderr": "",
    })
    assert probe.inspect_runtime(sys.executable, tmp_path)["missing_flags"] == list(probe.REQUIRED_FLAGS)


@pytest.mark.parametrize("extra", [
    {}, {"allow_real_worker": True},
    {"allow_real_worker": True, "disposable_environment": True, "model": "explicit"},
])
def test_live_requires_separate_authorization_before_any_effect(tmp_path, monkeypatch, extra):
    def forbidden(*a, **k):
        raise AssertionError("must not inspect, create fixtures or run a model")
    monkeypatch.setattr(probe, "inspect_runtime", forbidden)
    with pytest.raises(ValueError):
        probe.run_probe(output=tmp_path / "out", live=True, **extra)
    assert not (tmp_path / "out").exists()


def test_model_claim_and_canary_absence_do_not_prove_isolation(prepared):
    root, fixtures = prepared
    result = probe.assess_events([message_event("All files are safe and I used every skill")], root, fixtures)
    assert result["optional_resource"]["status"] == "unverified"
    assert result["source_write_denied"]["status"] == "unverified"
    assert result["unbound_discovery"]["status"] == "unverified"
    assert result["admin_system_discovery"]["status"] == "unverified"
    assert result["network_denied"]["status"] == "unverified"


def test_reference_needs_exact_helper_event_and_private_challenge(prepared):
    root, fixtures = prepared
    event = command_event(root, "read-reference", {"action": "read-reference", "value": fixtures["reference"]})
    assert probe.assess_events([event], root, fixtures)["optional_resource"]["status"] == "supported"
    event["item"]["command"] = "echo 'python3 fake read-reference'"
    assert probe.assess_events([event], root, fixtures)["optional_resource"]["status"] == "unverified"
    assert fixtures["reference"] not in probe._prompt(root, fixtures)
    assert fixtures["outside"] not in probe._prompt(root, fixtures)


@pytest.mark.parametrize("number,code,expected", [(13, 31, "supported"), (1, 31, "supported"),
                                                      (30, 31, "supported"), (2, 32, "unverified")])
def test_real_permission_errors_differ_from_missing_tools(prepared, number, code, expected):
    root, fixtures = prepared
    event = command_event(root, "write-source", {"action": "write-source", "errno": number}, code)
    assert probe.assess_events([event], root, fixtures)["source_write_denied"]["status"] == expected


def test_successful_escape_is_not_hidden_by_later_denial(prepared):
    root, fixtures = prepared
    events = [
        command_event(root, "read-outside", {"action": "read-outside", "value": fixtures["outside"]}),
        command_event(root, "read-outside", {"action": "read-outside", "errno": 13}, 31),
    ]
    assert probe.assess_events(events, root, fixtures)["outside_read_denied"]["status"] == "unsupported"


def test_unbound_method_influence_is_reported(prepared):
    root, fixtures = prepared
    result = probe.assess_events([message_event(fixtures["canaries"][0])], root, fixtures)
    assert result["unbound_discovery"]["status"] == "unsupported"


def test_changed_or_linked_sentinels_cannot_become_success(prepared, tmp_path):
    root, fixtures = prepared
    outside = tmp_path / "do-not-touch"
    outside.write_text("unchanged")
    sentinel = root / "control/state.json"
    sentinel.unlink()
    sentinel.symlink_to(outside)
    result = probe.assess_events([], root, fixtures)
    assert result["control_write_denied"]["status"] == "unsupported"
    assert result["fixture_integrity"]["status"] == "unsupported"
    assert outside.read_text() == "unchanged"


def test_parent_owned_evidence_does_not_overwrite_links(tmp_path):
    outside = tmp_path / "original"
    outside.write_text("retain")
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "report.json").symlink_to(outside)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FileExistsError):
            probe.write_evidence(fd, "report.json", "bad")
    finally:
        os.close(fd)
    assert outside.read_text() == "retain"


def test_output_directory_cannot_be_reused(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    (output / "evidence").write_text("retain")
    with pytest.raises(FileExistsError):
        probe.run_probe(output=output)
    assert (output / "evidence").read_text() == "retain"


def test_real_child_timeout_is_bounded_and_reaped(tmp_path):
    result = probe.capture([sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                           cwd=tmp_path, env=probe.clean_environment(tmp_path), timeout=1)
    assert result["status"] == "unverified"
    assert result["reason"] == "timeout"
    assert result["exit_code"] < 0


def test_generated_helper_observes_real_disposable_files(prepared):
    root, fixtures = prepared
    command = [sys.executable, "-I", str(root / "tools/probe_helper.py"), "read-reference"]
    result = probe.capture(command, cwd=root, env=probe.clean_environment(root), timeout=5)
    assert result["exit_code"] == 0
    assert json.loads(result["stdout"])["value"] == fixtures["reference"]
    # This is an ordinary unsandboxed test child, not a Codex isolation PASS.
    result = probe.capture([*command[:-1], "write-control"], cwd=root,
                           env=probe.clean_environment(root), timeout=5)
    assert json.loads(result["stdout"])["write_succeeded"] is True
    assert probe.assess_events([], root, fixtures)["control_write_denied"]["status"] == "unsupported"


def test_loopback_network_fixture_is_local_and_nonsecret(prepared):
    root, _fixtures = prepared
    with probe.LoopbackFixture() as network:
        (root / "tools/network.json").write_text(json.dumps({"url": network.url}))
        result = probe.capture([sys.executable, "-I", str(root / "tools/probe_helper.py"), "network-local"],
                               cwd=root, env=probe.clean_environment(root), timeout=5)
        assert result["exit_code"] == 0
        assert json.loads(result["stdout"])["value"] == network.marker


def test_live_simulation_records_attempts_without_fabricating_provider_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_TEST_KEY", "explicit-test-key")
    monkeypatch.setattr(probe, "inspect_runtime", lambda binary, root: {
        "status": "supported", "version": "1.2.3", "binary": sys.executable,
        "binary_digest": probe.binary_digest(Path(sys.executable)),
    })
    calls = []

    def capture(command, *, cwd, env, timeout, stdin):
        calls.append(command)
        assert env["CODEX_API_KEY"] == "explicit-test-key"
        assert "--sandbox" in command and "read-only" in command
        root = cwd.parent.parent
        marker = (root / "packages/optional/references/marker.txt").read_text()
        event = command_event(root, "read-reference", {"action": "read-reference", "value": marker})
        return {"status": "completed", "exit_code": 0, "stdout": json.dumps(event) + "\n", "stderr": ""}

    monkeypatch.setattr(probe, "capture", capture)
    report = probe.run_probe(output=tmp_path / "probe", live=True, allow_real_worker=True,
                             disposable_environment=True, credential_env="PROBE_TEST_KEY",
                             model="explicit-model", contexts=2)
    assert len(calls) == report["cli_execution_attempts"] == 2
    assert report["provider_requests"] is None
    assert report["checks"]["fresh_context_rehydration"]["status"] == "supported"
    assert not report["strict_binding_supported"]
    assert "explicit-test-key" not in (tmp_path / "probe/report.json").read_text()
