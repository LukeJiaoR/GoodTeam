"""Exercise the public probe process without invoking a real Worker/provider."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


MODULE = "goodteam_os.skill_conformance"
SOURCE = Path(__file__).resolve().parents[1] / "src"


def run_cli(tmp_path, *args):
    env = {"PATH": os.environ.get("PATH", os.defpath), "PYTHONPATH": str(SOURCE),
           "HOME": str(tmp_path), "LANG": "C.UTF-8"}
    return subprocess.run(
        [sys.executable, "-m", MODULE, *map(str, args)], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=15, check=False,
    )


def test_probe_cli_persists_unverified_and_refuses_to_replace_evidence(tmp_path):
    output = tmp_path / "evidence"
    args = ("--output", output, "--codex", tmp_path / "missing-codex")
    first = run_cli(tmp_path, *args)
    assert first.returncode == 2
    report = json.loads(first.stdout)
    assert report["runtime"]["status"] == "unverified"
    assert report["cli_execution_attempts"] == 0
    assert report["provider_requests"] == 0
    assert report["strict_binding_supported"] is False
    original = (output / "report.json").read_bytes()
    assert json.loads(original) == report
    second = run_cli(tmp_path, *args)
    assert second.returncode == 2
    assert not second.stdout
    assert (output / "report.json").read_bytes() == original


@pytest.mark.parametrize("options", [
    ("--live",),
    ("--live", "--allow-real-worker", "--disposable-environment", "--model", "fixture-model",
     "--credential-env", "ABSENT_PROBE_TEST_CREDENTIAL"),
])
def test_probe_cli_rejects_missing_live_authority_before_creating_output(tmp_path, options):
    output = tmp_path / "must-not-exist"
    # A tripwire executable must not be invoked even for --help without consent.
    binary = tmp_path / "codex-tripwire"
    tripwire = tmp_path / "invoked"
    binary.write_text(f"#!/bin/sh\ntouch '{tripwire}'\nexit 77\n")
    binary.chmod(0o700)
    result = run_cli(tmp_path, "--output", output, "--codex", binary, *options)
    assert result.returncode == 2
    assert not output.exists()
    assert not tripwire.exists()
