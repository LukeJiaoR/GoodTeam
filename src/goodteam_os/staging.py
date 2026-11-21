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
