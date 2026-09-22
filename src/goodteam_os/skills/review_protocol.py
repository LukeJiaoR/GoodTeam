"""S6 preparation: method-free review inputs, never Runtime authorization.

Only new task locks opt into this version. Historical tasks keep their captured
contract and bytes. The base below is NOT a complete Skill prompt: S5 must still
supply verified content, isolated resources and independently observed delivery.
"""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
from typing import Any

from .models import SkillError, SkillSource, bounded_text, canonical_json, identifier

REVIEW_PROTOCOL = "goodteam.code-review-protocol.v1"
REVIEW_SUBJECT = "role:code_review"
REVIEW_CORE_SOURCE = "f4d58d02d3741271ba48d1381819c95d21775f43"


def review_role_contract(ref: str) -> dict[str, Any]:
    """Capture protocol, not the current legacy Role/profile method."""
    bounded_text(ref)
    return {
        "ref": ref,
        "subject": REVIEW_SUBJECT,
        "protocol_schema": REVIEW_PROTOCOL,
        "input_contract": "Review only the named task, exact candidate and supplied evidence. Preserve explicit task requirements.",
        "output_contract": "Return findings, supporting evidence and a summary through the authorized result channel. Distinguish observed checks from unverified claims and checks not run.",
        "authority_boundary": "Advisory, read-only review. Do not modify reviewed sources, method packages or control-plane state; do not approve, merge, deploy or advance gates. A method cannot grant permissions.",
        "session_contract": "Use a separate reviewer job and a new session; never resume the builder session.",
    }


def is_review_contract(contract: object) -> bool:
    """Missing marker is historical, not silently migrated. Unknown is blocked."""
    if not isinstance(contract, dict):
        raise SkillError("review_protocol_invalid", "Review requires its captured Role contract.")
    if "protocol_schema" not in contract:
        return False
    if (contract.get("protocol_schema") != REVIEW_PROTOCOL
            or contract != review_role_contract(contract.get("ref"))):
        raise SkillError("review_protocol_unsupported", "The captured review protocol is unsupported; do not substitute current defaults.")
    return True


def locked_review_contract(workers, context: object) -> dict[str, Any] | None:
    """Read the original task contract, never current Team or mutable job flags."""
    from .task_context import validate_task_skill_context
    from .task_locks import TaskSkillIdentity
    from .worker_requests import _services

    context = validate_task_skill_context(context)
    root, _operator, locks, _availability = _services(workers)
    lock = locks.read(context["task_lock_ref"], task=TaskSkillIdentity(**context["task"]))
    binding = lock.get("start_input", {}).get("request", {}).get("confirmed_input", {}).get("producer_binding")
    if binding is not None and binding.get("worker_root") != str(root):
        raise SkillError("snapshot_scope_mismatch", "Review belongs to another captured Worker root.")
    subject = lock["subjects"].get(REVIEW_SUBJECT)
    if subject is None:
        raise SkillError("subject_not_locked", "Code Review was not captured in this task.")
    contract = subject["role_contract"]
    return deepcopy(contract) if is_review_contract(contract) else None


def review_execution_fields(workers, context: object, *, role: str) -> dict[str, str]:
    """New protocol producers select readonly/new; legacy requests stay exact."""
    if role != "code_review" or not context or locked_review_contract(workers, context) is None:
        return {}
    return {"execution_policy": "read_only", "session_policy": "new", "worker_session_ref": ""}


def review_artifact_task(*, approval: str, artifact_key: str, artifact_path: str, artifact_version: str) -> str:
    """The target is mandatory protocol data; a profile's checklist is not."""
    for value in (approval, artifact_key, artifact_path):
        bounded_text(value, maximum=4096)
    identifier(artifact_version, "sha256:")
    target = {"approval": approval, "artifact_key": artifact_key,
              "artifact_path": artifact_path, "artifact_version": artifact_version}
    return ("Review the exact artifact identified below using the task's locked methods. "
            "Preserve the supplied task requirements. Return advisory findings, evidence and summary; "
            "do not modify files or decide the gate.\n\n"
            + canonical_json(target))


def review_prompt_base(request: dict[str, Any], contract: dict[str, Any]) -> str:
    """Prepare only the protocol/task layer; no fallback methods or execution.

    Explicit user task/context/constraints/additional instructions remain exact.
    No profile label, hidden focus list, legacy Role text, memory pickup policy,
    or memory write-back template is appended. Required method injection remains
    an independent S5 obligation before this base can be sent to a Runtime.
    """
    if not is_review_contract(contract) or request.get("role") != "code_review":
        raise SkillError("review_protocol_unsupported", "A versioned Code Review contract is required.")
    if (request.get("execution_policy") != "read_only" or request.get("session_policy", "new") != "new"
            or request.get("worker_session_ref")):
        raise SkillError("review_execution_policy_invalid", "Code Review requires read-only execution in a new reviewer session.")
    for key in ("task", "context", "additional_prompt"):
        if not isinstance(request.get(key, ""), str):
            raise SkillError("review_input_invalid", "Explicit review instructions must be text.")
    for key in ("constraints", "expected_artifacts"):
        if not isinstance(request.get(key), list) or any(not isinstance(item, str) for item in request[key]):
            raise SkillError("review_input_invalid", "Review constraints and outputs must be explicit text lists.")
    supplied = {key: deepcopy(request[key]) for key in ("task", "context", "constraints", "expected_artifacts")}
    supplied["additional_prompt"] = request.get("additional_prompt", "")
    return ("# Code Review protocol base\n\n" + canonical_json(contract)
            + "\n\n## Original task inputs\n\n" + canonical_json(supplied) + "\n")


def review_core_package():
    """Optional first-party standard package; no installation/binding side effect.

    This convenience accessor is not a package-name special case in the loader.
    Custom packages continue through the same standard package parser/store.
    """
    from .package import PackageFile, package_from_files

    root = files("goodteam_os.skills").joinpath("builtin", "review-core")
    package = package_from_files(tuple(PackageFile(name, root.joinpath(name).read_bytes())
                                       for name in ("SKILL.md", "references/profile-methods.md", "NOTICE.md")),
                                 directories=("references",))
    return package, SkillSource("builtin", "builtin:review-core", revision=REVIEW_CORE_SOURCE)
