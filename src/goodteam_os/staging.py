import subprocess
import re
from pathlib import Path
from shutil import which
from typing import Callable

from goodteam_os.models import _parse_simple_yaml


StagingCommandRunner = Callable[[list[str]], dict[str, object]]

DEPLOYMENT_REQUIRED_FIELDS = [
    "staging_ssh_target",
    "staging_remote_dir",
    "staging_deploy_command",
    "staging_healthcheck_command",
]

STEP_TIMEOUTS = {
    "deploy": 1800,
    "healthcheck": 300,
    "smoke": 300,
    "acceptance": 600,
}

EVIDENCE_ORDER = {
    "none": 0,
    "infrastructure": 1,
    "functional": 2,
}


def deployment_backend_config(project_config: Path) -> dict[str, object]:
    if not project_config.is_file():
        return {"mode": ""}
    target = _parse_simple_yaml(project_config)
    backend = dict(target.get("deployment_backend", {})) if isinstance(target.get("deployment_backend", {}), dict) else {}
    contract = delivery_contract_config(target)
    return {
        "mode": str(backend.get("mode", "") or ""),
        "staging_ssh_target": str(backend.get("staging_ssh_target", "") or ""),
        "staging_remote_dir": str(backend.get("staging_remote_dir", "") or ""),
        "staging_git_ref": str(backend.get("staging_git_ref", "") or ""),
        "staging_public_url": str(backend.get("staging_public_url", "") or ""),
        "staging_deploy_command": str(backend.get("staging_deploy_command", "") or ""),
        "staging_healthcheck_command": str(backend.get("staging_healthcheck_command", "") or ""),
        "staging_smoke_command": str(backend.get("staging_smoke_command", "") or ""),
        "delivery_contract": contract,
    }


def staging_backend_enabled(*, source_type: str, project_config: Path | None) -> bool:
    if source_type != "existing_project" or project_config is None or not project_config.is_file():
        return False
    return deployment_backend_config(project_config).get("mode", "") == "ssh_compose"


def deployment_backend_report(target: dict[str, object], *, ssh_available: bool | None = None) -> dict[str, object]:
    backend = dict(target.get("deployment_backend", {})) if isinstance(target.get("deployment_backend", {}), dict) else {}
    mode = str(backend.get("mode", "") or "")
    resolved_ssh = bool(which("ssh")) if ssh_available is None else ssh_available
    if not mode:
        return {"mode": "", "configured": False, "missing_fields": [], "ssh_available": resolved_ssh}

    missing_fields = [field for field in DEPLOYMENT_REQUIRED_FIELDS if not str(backend.get(field, "") or "").strip()]
    return {
        "mode": mode,
        "configured": mode == "ssh_compose" and not missing_fields,
        "missing_fields": missing_fields,
        "ssh_available": resolved_ssh,
        "staging_ssh_target": str(backend.get("staging_ssh_target", "") or ""),
        "staging_remote_dir": str(backend.get("staging_remote_dir", "") or ""),
        "staging_git_ref": str(backend.get("staging_git_ref", "") or ""),
        "staging_public_url": str(backend.get("staging_public_url", "") or ""),
    }


def delivery_contract_config(target: dict[str, object]) -> dict[str, object]:
    raw_contract = target.get("delivery_contract", {})
    contract = dict(raw_contract) if isinstance(raw_contract, dict) else {}
    acceptance = _normalize_acceptance_checks(contract.get("staging_acceptance", {}))
    fatal_patterns = _normalize_named_values(contract.get("fatal_output_patterns", {}))
    required = _truthy(contract.get("required", False))
    minimum_evidence = str(contract.get("minimum_evidence", "") or ("functional" if required else "infrastructure"))
    if minimum_evidence not in EVIDENCE_ORDER:
        minimum_evidence = "functional" if required else "infrastructure"
    return {
        "required": required,
        "minimum_evidence": minimum_evidence,
        "fatal_output_patterns": fatal_patterns,
        "staging_acceptance": acceptance,
    }


def delivery_contract_report(target: dict[str, object]) -> dict[str, object]:
    contract = delivery_contract_config(target)
    acceptance = list(contract["staging_acceptance"])
    fatal_patterns = list(contract["fatal_output_patterns"])
    evidence_level = "functional" if acceptance else "infrastructure"
    minimum_evidence = str(contract["minimum_evidence"])
    missing_fields: list[str] = []
    if EVIDENCE_ORDER[evidence_level] < EVIDENCE_ORDER[minimum_evidence]:
        missing_fields.append("staging_acceptance")
    return {
        "required": bool(contract["required"]),
        "minimum_evidence": minimum_evidence,
        "configured": not missing_fields,
        "missing_fields": missing_fields,
        "evidence_level": evidence_level,
        "acceptance_count": len(acceptance),
        "fatal_output_patterns_count": len(fatal_patterns),
    }


def run_staging_backend(
    *,
    project_config: Path,
    command_runner: Callable[..., dict[str, object]] | None = None,
    execution_root: Path | None = None,
    repository_candidate: dict[str, object] | None = None,
) -> dict[str, object]:
    config = deployment_backend_config(project_config)
    if config.get("mode") != "ssh_compose":
        raise ValueError("Staging backend is not configured for ssh_compose.")

    missing_fields = [field for field in DEPLOYMENT_REQUIRED_FIELDS if not config.get(field, "").strip()]
    if missing_fields:
        raise ValueError(f"Staging backend is missing required fields: {', '.join(missing_fields)}")

    if repository_candidate is not None:
        # SSH commands currently target a separately configured remote ref.
        # Merely running the local launcher in the retained task directory
        # cannot prove which source bytes that remote deployment executed.
        result = _staging_result(
            status="blocked", failed_step="candidate", config=config, steps=[],
            evidence_level="none", evidence_sufficient=False,
            evidence_gap=("SSH staging cannot attest the retained task candidate; deployment was not invoked."
                          if repository_candidate else
                          "The workflow has no retained task baseline; staging deployment was not invoked."),
        )
        result["repository_result"] = repository_candidate
        return result

    runner = command_runner or _run_ssh_command
    local_root = execution_root if execution_root is not None else project_config.parent
    contract = dict(config.get("delivery_contract", {}))
    fatal_patterns = [str(pattern) for pattern in contract.get("fatal_output_patterns", [])]
    steps: list[dict[str, object]] = []
    for label, config_key in [
        ("deploy", "staging_deploy_command"),
        ("healthcheck", "staging_healthcheck_command"),
        ("smoke", "staging_smoke_command"),
    ]:
        shell_command = config.get(config_key, "").strip()
        if not shell_command:
            continue
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            config["staging_ssh_target"],
            f"cd {config['staging_remote_dir']} && {shell_command}",
        ]
        step = _run_stage_step(
            runner=runner,
            command=command,
            cwd=local_root,
            timeout=STEP_TIMEOUTS[label],
            label=label,
            fatal_patterns=fatal_patterns,
        )
        steps.append(step)
        if step["exit_code"] != 0 or step.get("fatal_pattern", ""):
            return _staging_result(
                status="failed",
                failed_step=label,
                config=config,
                steps=steps,
                evidence_level="infrastructure",
                evidence_sufficient=False,
            )

    for check in contract.get("staging_acceptance", []):
        acceptance = dict(check)
        check_id = str(acceptance.get("id", "") or "acceptance")
        check_type = str(acceptance.get("type", "") or "ssh_command")
        shell_command = str(acceptance.get("command", "") or "").strip()
        if not shell_command:
            continue
        if check_type == "local_command":
            command = ["/bin/sh", "-lc", shell_command]
            cwd = local_root
        else:
            command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                config["staging_ssh_target"],
                f"cd {config['staging_remote_dir']} && {shell_command}",
            ]
            cwd = local_root
        step = _run_stage_step(
            runner=runner,
            command=command,
            cwd=cwd,
            timeout=STEP_TIMEOUTS["acceptance"],
            label=f"acceptance:{check_id}",
            fatal_patterns=fatal_patterns,
        )
        steps.append(step)
        if step["exit_code"] != 0 or step.get("fatal_pattern", ""):
            return _staging_result(
                status="failed",
                failed_step="acceptance",
                config=config,
                steps=steps,
                evidence_level="functional",
                evidence_sufficient=False,
                evidence_gap=f"{check_id} failed",
            )

    evidence_level = "functional" if contract.get("staging_acceptance", []) else "infrastructure"
    evidence_gap = ""
    evidence_sufficient = _evidence_sufficient(evidence_level, str(contract.get("minimum_evidence", "infrastructure")))
    if not evidence_sufficient:
        evidence_gap = "functional validation missing"
        return _staging_result(
            status="failed",
            failed_step="acceptance",
            config=config,
            steps=steps,
            evidence_level=evidence_level,
            evidence_sufficient=False,
            evidence_gap=evidence_gap,
        )

    return _staging_result(
        status="passed",
        failed_step="",
        config=config,
        steps=steps,
        evidence_level=evidence_level,
        evidence_sufficient=True,
        evidence_gap=evidence_gap,
    )


def _run_stage_step(
    *,
    runner: Callable[..., dict[str, object]],
    command: list[str],
    cwd: Path,
    timeout: int,
    label: str,
    fatal_patterns: list[str],
) -> dict[str, object]:
    result = runner(command, cwd=cwd, timeout=timeout)
    step = {
            "label": label,
            "command": command,
            "timeout": timeout,
            "exit_code": int(result.get("exit_code", 0)),
            "stdout": str(result.get("stdout", "")),
            "stderr": str(result.get("stderr", "")),
    }
    fatal_pattern = _matching_fatal_pattern(
        fatal_patterns,
        f"{step['stdout']}\n{step['stderr']}",
    )
    if fatal_pattern:
        step["fatal_pattern"] = fatal_pattern
    return step


def _staging_result(
    *,
    status: str,
    failed_step: str,
    config: dict[str, object],
    steps: list[dict[str, object]],
    evidence_level: str,
    evidence_sufficient: bool,
    evidence_gap: str = "",
) -> dict[str, object]:
    return {
        "status": status,
        "failed_step": failed_step,
        "steps": steps,
        "ssh_target": config["staging_ssh_target"],
        "remote_dir": config["staging_remote_dir"],
        "git_ref": config["staging_git_ref"],
        "public_url": config["staging_public_url"],
        "evidence_level": evidence_level,
        "evidence_sufficient": evidence_sufficient,
        "evidence_gap": evidence_gap,
    }


def _matching_fatal_pattern(patterns: list[str], output: str) -> str:
    for pattern in patterns:
        if re.search(pattern, output, flags=re.IGNORECASE | re.MULTILINE):
            return pattern
    return ""


def _evidence_sufficient(evidence_level: str, minimum_evidence: str) -> bool:
    return EVIDENCE_ORDER.get(evidence_level, 0) >= EVIDENCE_ORDER.get(minimum_evidence, 1)


def _normalize_acceptance_checks(value: object) -> list[dict[str, str]]:
    if isinstance(value, list):
        checks: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command", "") or "").strip()
            if not command:
                continue
            check_id = str(item.get("id", "") or f"acceptance-{len(checks) + 1}")
            check_type = str(item.get("type", "ssh_command") or "ssh_command")
            if check_type not in {"ssh_command", "local_command"}:
                check_type = "ssh_command"
            checks.append(
                {
                    "id": check_id,
                    "type": check_type,
                    "command": command,
                }
            )
        return checks
    if not isinstance(value, dict):
        return []
    checks: list[dict[str, str]] = []
    for check_id, raw_check in value.items():
        if not isinstance(raw_check, dict):
            continue
        command = str(raw_check.get("command", "") or "").strip()
        if not command:
            continue
        check_type = str(raw_check.get("type", "ssh_command") or "ssh_command")
        if check_type not in {"ssh_command", "local_command"}:
            check_type = "ssh_command"
        checks.append(
            {
                "id": str(raw_check.get("id", "") or check_id),
                "type": check_type,
                "command": command,
            }
        )
    return checks


def _normalize_named_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "required"}


def _run_ssh_command(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "timeout": timeout,
        "exit_code": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }
