"""Credential-free Runtime prerequisites; never grants Skill execution authority.

Only help/version and, with --sandbox-checks, a fixed non-secret helper under
``codex sandbox`` are executed. No arbitrary command or live/model mode exists.
The helper can deliberately modify its own disposable sentinels. It never opens
user projects, credentials, installed Skills, or other host data.
"""
from __future__ import annotations

import argparse
import errno
import json
from pathlib import Path
import shutil
import sys
from typing import Any
from uuid import uuid4

from goodteam_os import skill_conformance as probe

SCHEMA = "skill_runtime_readiness.v1"
PROFILE = "goodteam-review-probe"
FILES = {
    "source": "repo/source.txt",
    "package": "packages/method/references/marker.txt",
    "control": "control/state.json",
    "outside": "outside/sentinel.txt",
}
READS = tuple("read_" + name for name in FILES)
WRITES = tuple("write_" + name for name in FILES)
OPERATIONS = (*READS, *WRITES, "network_loopback")
DENIALS = {errno.EPERM, errno.EACCES, errno.EROFS}

# Fixed operations only. The root is newly created by this module. The helper
# does not interpret model output, execute package scripts, or accept commands.
HELPER = '''import hashlib, json, socket, sys
from pathlib import Path
root = Path(sys.argv[1])
port = int(sys.argv[2])
files = json.loads(sys.argv[3])
result = {}
for name, relative in files.items():
    path = root / relative
    for operation in ("read", "write"):
        key = operation + "_" + name
        try:
            if operation == "read":
                value = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                with path.open("ab") as stream:
                    stream.write(b"\\nNONSECRET_WRITE_PROBE")
                value = True
            result[key] = {"succeeded": True, "value": value}
        except OSError as exc:
            result[key] = {"succeeded": False, "errno": exc.errno}
try:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
        connection.sendall(b"GET / HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n")
        received = bytearray()
        while len(received) < 65536:
            chunk = connection.recv(4096)
            if not chunk:
                break
            received.extend(chunk)
        result["network_loopback"] = {"succeeded": True, "value": received.decode()}
except OSError as exc:
    result["network_loopback"] = {"succeeded": False, "errno": exc.errno}
print(json.dumps(result, sort_keys=True))
'''


def _fresh_root(output: Path) -> Path:
    output = output.expanduser().absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Output must use canonical paths, without symlink ancestors")
    output.mkdir(mode=0o700, exist_ok=False)
    return output


def _save(root: Path, name: str, value: Any) -> None:
    # Retain failed and partial diagnostics; never overwrite earlier evidence.
    with (root / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _fixtures(root: Path) -> dict[str, bytes]:
    originals = {}
    for relative in FILES.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = ("NONSECRET_" + uuid4().hex).encode()
        path.write_bytes(originals[relative])
    (root / "tools").mkdir()
    (root / "tools/check.py").write_text(HELPER, encoding="utf-8")
    return originals


def _restore(root: Path, originals: dict[str, bytes]) -> None:
    # Only this fixed helper runs; it appends bytes and never creates links.
    for relative, original in originals.items():
        (root / relative).write_bytes(original)


def _profile(root: Path) -> str:
    # Keep both legacy sandbox flags and legacy sandbox config OUT of this
    # candidate profile. Their precedence can defeat default_permissions.
    paths = [root / part for part in ("repo", "packages", "tools")]
    paths.append(Path(sys.base_prefix).resolve())
    return (
        f'default_permissions = "{PROFILE}"\n'
        'approval_policy = "never"\nweb_search = "disabled"\n'
        f'[permissions.{PROFILE}.filesystem]\n'
        '":root" = "deny"\n":minimal" = "read"\n'
        '":tmpdir" = "deny"\n":slash_tmp" = "deny"\n'
        + "".join(json.dumps(str(path)) + ' = "read"\n' for path in paths)
        + f'[permissions.{PROFILE}.network]\nenabled = false\n'
    )


def _observations(result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("status") != "completed" or result.get("exit_code") != 0:
        return None
    try:
        value = json.loads(result["stdout"])
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(value, dict) or set(value) != set(OPERATIONS):
        return None
    if any(not isinstance(item, dict) or type(item.get("succeeded")) is not bool for item in value.values()):
        return None
    return value


def assess(result, positive, originals, current, network_marker):
    """Only exact helper observations and working positive controls count.

    An exit code, missing interpreter, refused connection or model assertion
    cannot certify a boundary. Changed sentinel bytes are sticky failures.
    """
    observed, control = _observations(result), _observations(positive)
    checks = {}
    for action in OPERATIONS:
        status, reason = "unverified", "Missing complete helper/positive-control evidence"
        reference = control.get(action, {}) if control else {}
        positive_ok = reference.get("succeeded") is True
        if action in READS:
            relative = FILES[action.removeprefix("read_")]
            positive_ok = positive_ok and reference.get("value") == probe.digest(originals[relative])[7:]
        if action == "network_loopback":
            positive_ok = positive_ok and network_marker in str(reference.get("value", ""))
        item = observed.get(action, {}) if observed else {}
        if positive_ok and observed:
            expected_read = action in {"read_source", "read_package"}
            if expected_read:
                expected = probe.digest(originals[FILES[action.removeprefix("read_")]])[7:]
                passed = item.get("succeeded") is True and item.get("value") == expected
                status = "supported" if passed else "unsupported"
                reason = "Exact allowed bytes read" if passed else "Allowed read failed or returned different bytes"
            elif item.get("succeeded") is True:
                status, reason = "unsupported", "Forbidden operation succeeded on a disposable sentinel"
            elif type(item.get("errno")) is int and item["errno"] in DENIALS:
                status, reason = "supported", "Actual permission denial with a working positive control"
            else:
                reason = "Failure was not an observed permission denial"
        if action in WRITES and current[FILES[action.removeprefix("write_")]] != originals[FILES[action.removeprefix("write_")]]:
            status, reason = "unsupported", "Parent observed changed sentinel bytes"
        checks[action] = {"status": status, "reason": reason}
    return checks


def _sandbox_checks(root, executable, binary_hash):
    originals = _fixtures(root)
    env = probe.clean_environment(root)
    profile = _profile(root)
    (root / "codex-home/config.toml").write_text(profile, encoding="utf-8")
    _save(root, "fixture-manifest.json", {name: probe.digest(data) for name, data in originals.items()})
    modes = {}
    with probe.LoopbackFixture() as network:
        # This exact trusted helper is also the positive control, outside the
        # sandbox. Its only write targets are our generated files.
        helper = [str(Path(sys.executable).resolve()), "-I", str(root / "tools/check.py"), str(root),
                  str(network.server.server_port), json.dumps(FILES, sort_keys=True)]
        positive = probe.capture(helper, cwd=root / "repo", env=env, timeout=10)
        _save(root, "positive-control.json", positive)
        _restore(root, originals)
        for name, arguments in (
            ("legacy_read_only", ["-c", 'sandbox_mode="read-only"']),
            ("restricted_profile", ["--permission-profile", PROFILE]),
        ):
            if probe.binary_digest(Path(executable)) != binary_hash:
                raise ValueError("Codex binary changed after inspection")
            command = [executable, "sandbox", *arguments, "--", *helper]
            result = probe.capture(command, cwd=root / "repo", env=env, timeout=15)
            current = {name: (root / name).read_bytes() for name in originals}
            modes[name] = {
                "command": command, "result": result,
                "checks": assess(result, positive, originals, current, network.marker),
                "after_digests": {name: probe.digest(data) for name, data in current.items()},
            }
            _save(root, name + ".json", modes[name])
            _restore(root, originals)
    return modes


def run_readiness(*, output: Path, binary: str = "codex", sandbox_checks: bool = False):
    root = _fresh_root(output)
    report = {
        "schema_version": SCHEMA, "mode": "offline_sandbox" if sandbox_checks else "inspect",
        "strict_binding_supported": False, "live_ready": False,
        "model_invocations": 0, "provider_requests": 0, "provider_cost_usd": 0,
        "platform": {"system": sys.platform, "python": sys.version.split()[0]},
        "inspection_code": {"readiness": probe.digest(Path(__file__).read_bytes()),
                            "conformance": probe.digest(Path(probe.__file__).read_bytes())},
        "evidence_kind": "local_cli_prerequisites_not_model_conformance",
        "remaining": ["runtime_skill_inventory", "admin_system_discovery", "provider_egress_isolation",
                      "enforced_provider_request_and_cost_caps", "authorized_model_and_credential_reference",
                      "live_content_and_resource_observations", "worker_integration_and_execution_details"],
    }
    try:
        runtime = probe.inspect_runtime(binary, root)
        report["runtime"] = runtime
        if sandbox_checks and runtime.get("status") == "supported":
            executable = shutil.which(binary)
            if executable is None:
                raise ValueError("Codex disappeared after inspection")
            executable = str(Path(executable).resolve())
            report["sandbox_modes"] = _sandbox_checks(root, executable, runtime["binary_digest"])
        # A prerequisite result must never synthesize real-model conformance,
        # discovery inventory, network egress proof, or production authority.
    except (OSError, ValueError) as exc:
        report["incomplete"] = type(exc).__name__
        raise
    finally:
        _save(root, "readiness.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New canonical directory; parent must exist")
    parser.add_argument("--codex", default="codex", help="Trusted binary, inspected and hash-pinned before each run")
    parser.add_argument("--sandbox-checks", action="store_true", help="Run fixed non-secret helper; never a model")
    args = parser.parse_args(argv)
    try:
        result = run_readiness(output=args.output, binary=args.codex, sandbox_checks=args.sandbox_checks)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Readiness inspection incomplete: {type(exc).__name__}; inspect retained evidence.\n")
    print(json.dumps(result, indent=2))
    # Like the original probe, incomplete real conformance is deliberately not
    # a success exit. Automation must inspect the per-boundary evidence.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
