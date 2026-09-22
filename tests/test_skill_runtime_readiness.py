"""Offline evidence must not become Runtime authority or conceal escapes."""
from copy import deepcopy
import errno
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from goodteam_os import skill_runtime_readiness as readiness


@pytest.fixture
def observation():
    originals = {name: b"NONSECRET-fixture" for name in readiness.FILES.values()}
    positive, denied = {}, {}
    for action in readiness.OPERATIONS:
        value = True
        if action in readiness.READS:
            value = readiness.probe.digest(b"NONSECRET-fixture")[7:]
        if action == "network_loopback":
            value = "NONSECRET_NETWORK_RESPONSE"
        positive[action] = {"succeeded": True, "value": value}
        denied[action] = (deepcopy(positive[action]) if action in {"read_source", "read_package"}
                          else {"succeeded": False, "errno": errno.EPERM})

    def result(value):
        return {"status": "completed", "exit_code": 0, "stdout": json.dumps(value)}

    return originals, positive, denied, result


def evaluate(observation, modify=None):
    originals, positive, denied, result = observation
    if modify:
        modify(positive, denied)
    return readiness.assess(result(denied), result(positive), originals, originals, "NONSECRET_NETWORK_RESPONSE")


def test_exact_positive_reads_and_real_permission_denials_are_observed(observation):
    assert all(item["status"] == "supported" for item in evaluate(observation).values())


@pytest.mark.parametrize("action", ["read_outside", "read_control", "write_source", "network_loopback"])
def test_successful_forbidden_operation_cannot_be_accepted(observation, action):
    checks = evaluate(observation, lambda positive, denied: denied.update({action: positive[action]}))
    assert checks[action]["status"] == "unsupported"


@pytest.mark.parametrize("number", [errno.ENOENT, errno.ECONNREFUSED, errno.ETIMEDOUT, None, True])
def test_setup_errors_are_not_permission_denials(observation, number):
    checks = evaluate(observation, lambda positive, denied: denied["network_loopback"].update(errno=number))
    assert checks["network_loopback"]["status"] == "unverified"


def test_no_network_positive_control_means_no_network_proof(observation):
    checks = evaluate(observation, lambda positive, denied: positive["network_loopback"].update(value="not the challenge"))
    assert checks["network_loopback"]["status"] == "unverified"


def test_read_challenge_mismatch_is_not_success(observation):
    checks = evaluate(observation, lambda positive, denied: denied["read_package"].update(value="wrong-version"))
    assert checks["read_package"]["status"] == "unsupported"


@pytest.mark.parametrize("broken", [
    {"status": "unverified", "exit_code": -9, "stdout": "{}"},
    {"status": "completed", "exit_code": 1, "stdout": "{}"},
    {"status": "completed", "exit_code": 0, "stdout": "permission denied"},
    {"status": "completed", "exit_code": 0, "stdout": '{"strict_binding_supported": true}'},
])
def test_partial_or_unstructured_output_does_not_prove_boundaries(observation, broken):
    originals, positive, _denied, result = observation
    checks = readiness.assess(broken, result(positive), originals, originals, "NONSECRET_NETWORK_RESPONSE")
    assert all(item["status"] == "unverified" for item in checks.values())


def test_parent_detected_write_overrides_claimed_denial(observation):
    originals, positive, denied, result = observation
    current = {**originals, readiness.FILES["source"]: b"changed"}
    checks = readiness.assess(result(denied), result(positive), originals, current, "NONSECRET_NETWORK_RESPONSE")
    assert checks["write_source"]["status"] == "unsupported"


def test_candidate_profile_does_not_mix_legacy_policy_or_grant_control_paths(tmp_path):
    config = tomllib.loads(readiness._profile(tmp_path))
    assert "sandbox_mode" not in config and "sandbox_workspace_write" not in config
    policy = config["permissions"][readiness.PROFILE]
    assert policy["filesystem"][":root"] == "deny"
    assert policy["network"] == {"enabled": False}
    assert policy["filesystem"][str(tmp_path / "packages")] == "read"
    assert str(tmp_path / "control") not in policy["filesystem"]


def test_default_inspection_never_runs_sandbox_or_model(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No sandbox or model may run by default")
    monkeypatch.setattr(readiness, "_sandbox_checks", forbidden)
    report = readiness.run_readiness(output=tmp_path / "out", binary=str(tmp_path / "missing"))
    assert report["model_invocations"] == report["provider_requests"] == report["provider_cost_usd"] == 0
    assert report["live_ready"] is report["strict_binding_supported"] is False
    assert not (tmp_path / "out/repo").exists()
    assert json.loads((tmp_path / "out/readiness.json").read_text()) == report


def test_binary_change_stops_sandbox_before_launch(tmp_path, monkeypatch):
    binary = Path(sys.executable).resolve()
    monkeypatch.setattr(readiness.probe, "inspect_runtime", lambda *a: {
        "status": "supported", "binary_digest": "sha256:" + "0" * 64,
    })
    # Only the known local positive-control helper is allowed before the hash
    # check. No changed executable may run even when all flags were advertised.
    captures = []
    original_capture = readiness.probe.capture

    def capture(command, **kwargs):
        captures.append(command)
        assert "sandbox" not in command
        return original_capture(command, **kwargs)

    monkeypatch.setattr(readiness.probe, "capture", capture)
    with pytest.raises(ValueError, match="binary changed"):
        readiness.run_readiness(output=tmp_path / "out", binary=str(binary), sandbox_checks=True)
    assert len(captures) == 1
    report = json.loads((tmp_path / "out/readiness.json").read_text())
    assert report["incomplete"] == "ValueError"
    assert report["strict_binding_supported"] is False


def test_fixed_helper_can_observe_and_restore_only_generated_sentinels(tmp_path):
    originals = readiness._fixtures(tmp_path)
    env = readiness.probe.clean_environment(tmp_path)
    with readiness.probe.LoopbackFixture() as network:
        result = readiness.probe.capture(
            [sys.executable, "-I", str(tmp_path / "tools/check.py"), str(tmp_path),
             str(network.server.server_port), json.dumps(readiness.FILES)],
            cwd=tmp_path / "repo", env=env, timeout=10,
        )
        observed = readiness._observations(result)
        assert observed is not None and all(item["succeeded"] for item in observed.values())
        assert network.marker in observed["network_loopback"]["value"]
    assert all((tmp_path / name).read_bytes() != data for name, data in originals.items())
    readiness._restore(tmp_path, originals)
    assert all((tmp_path / name).read_bytes() == data for name, data in originals.items())


def test_existing_or_linked_output_is_never_reused(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    evidence = original / "readiness.json"
    evidence.write_text("keep")
    with pytest.raises(FileExistsError):
        readiness.run_readiness(output=original)
    link = tmp_path / "linked"
    link.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical"):
        readiness.run_readiness(output=link / "nested")
    assert evidence.read_text() == "keep"


def test_public_cli_does_not_accept_live_mode_or_credentials(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-m", "goodteam_os.skill_runtime_readiness", "--output", str(tmp_path / "out"),
         "--live", "--credential-env", "DO_NOT_READ"],
        env={"PYTHONPATH": str(source)}, cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2
    assert not (tmp_path / "out").exists()
