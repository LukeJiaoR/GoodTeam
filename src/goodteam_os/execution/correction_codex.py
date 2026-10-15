"""Dormant Codex correction protocol; no launcher or admission authority.

This 0.159.2 argv proposal requests native sandboxing, not verified confinement.
Nothing imports it from production dispatch. Existing external admission stays
closed; native config, login, version and tool permissions are not probed here.
"""
from pathlib import Path
import json
import os
import re

from goodteam_os.skills.review_capture import capture_final_turn

from .correction_feedback import render_correction_input
from .task_context import MAX_TASK_CONTEXT_BYTES
from .task_snapshot import TaskInputError, canonical_bytes

CLI_VERSION = "0.159.2"
OUTPUT_SCHEMA = "goodteam.codex-correction-output.v1"


def proposed_command(*, binary, home, workspace, model, version):
    """Pure host-side recipe, not a capability, selected route or executable probe."""
    paths = [Path(path) for path in (binary, home, workspace)]
    if (version != CLI_VERSION or any(not p.is_absolute() or ".." in p.parts for p in paths)
            or not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model) is None):
        raise TaskInputError("correction_codex_contract_invalid")
    binary, home, workspace = paths
    if home.is_relative_to(workspace) or workspace.is_relative_to(home) or binary.is_relative_to(workspace):
        raise TaskInputError("correction_codex_contract_invalid")
    return {"command": (str(binary), "--ask-for-approval", "never", "--no-daemon", "exec", "--ephemeral", "--json",
                        "--color", "never", "--sandbox", "workspace-write", "--cd", str(workspace),
                        "--model", model, "-"),
            "environment": {"PATH": os.defpath, "LC_ALL": "C.UTF-8", "HOME": str(home.parent),
                            "CODEX_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"},
            "available": False, "verification": "unactivated_command_contract",
            "native_configuration": "unverified", "sandbox_confinement": "unverified"}


def correction_prompt(authority):
    value = authority.load()
    if value.original.to_dict()["adapter"] != "codex":
        raise TaskInputError("correction_codex_contract_invalid")
    authority.workspace(value)
    prompt = render_correction_input(authority.project_root, value, host_seals=True)
    result = {"schema_version": OUTPUT_SCHEMA, "input": value.reference,
              "base_sha": value.to_dict()["candidate"]["head_sha"], "summary": "Describe edits and remaining risks."}
    prompt += ("\n\nReturn only a JSON object in your final message with this exact shape. "
               "Keep input and base_sha unchanged. Summary is commentary, not a claim of acceptance.\n"
               + canonical_bytes(result).decode())
    if len(prompt.encode()) > MAX_TASK_CONTEXT_BYTES:
        raise TaskInputError("correction_context_size_limit")
    return prompt


def admit_output(raw, value):
    """Decode a host-captured complete turn, never a model-selected candidate/path."""
    turn = capture_final_turn(raw, returncode=0)
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result
    try:
        body = json.loads(turn.report_bytes, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(body, dict) or set(body) != {"schema_version", "input", "base_sha", "summary"}
                or body["schema_version"] != OUTPUT_SCHEMA or body["input"] != value.reference
                or body["base_sha"] != value.to_dict()["candidate"]["head_sha"]
                or not isinstance(body["summary"], str) or not body["summary"].strip()
                or len(body["summary"].encode()) > 16384):
            raise ValueError("mismatched output")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise TaskInputError("correction_codex_output_invalid") from exc
    return {"summary": body["summary"], "transcript_digest": turn.transcript_digest,
            "verification": "unverified_model_commentary"}
