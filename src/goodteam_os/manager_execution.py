from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from goodteam_os.graph import resolve_project_runtime_root, resolve_role_adapter, worker_type_settings_for_role
from goodteam_os.projects import load_project_target
from goodteam_os.workers import WorkerJobStore


class ManagerExecutionError(RuntimeError):
    """Raised when a configured Task Assistant cannot produce a safe interpretation."""


def run_manager_interpretation(
    *,
    project_config: Path,
    run_id: str,
    project_key: str,
    intent_id: str,
    change_request: str,
    context: str,
) -> dict[str, object]:
    target = load_project_target(project_config)
    project_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if project_root is None:
        raise ManagerExecutionError("The Task Assistant project runtime root is unavailable.")

    adapter = resolve_role_adapter(target, "orchestrator")
    configured_model = str(dict(target.get("role_model_overrides", {})).get("orchestrator", "") or "").strip()
    model = _model_name(configured_model)
    settings = worker_type_settings_for_role(target, "orchestrator")
    # Task Assistant is one logical owner. Parallel Task Assistant authority is intentionally
    # unsupported even if an older project file contains a larger value.
    settings["desired_instances"] = 1

    store = WorkerJobStore(project_root)
    job = store.create_job(
        run_id=run_id,
        role="orchestrator",
        adapter=adapter,
        model=model,
        task=_manager_interpretation_task(change_request),
        context=context,
        constraints=[
            "Interpret only the user's explicit request; do not add requirements.",
            "Do not modify project files, workflow state, artifacts, approvals, or external systems.",
            "Return exactly one JSON object and no prose or markdown outside it.",
        ],
        expected_artifacts=[],
        project_key=project_key,
        phase="manager_intent",
        idempotency_key=f"manager-intent:{intent_id}",
        **settings,
    )
    job_id = str(job["id"])
    result = store.run_job(job_id)
    if str(result.get("status", "")) != "completed":
        summary = str(result.get("summary", "") or "Task Assistant worker failed.").strip()
        raise ManagerExecutionError(f"The configured Task Assistant could not complete: {summary}")

    result_path = Path(str(result.get("result_path") or store.root / job_id / "result.md"))
    try:
        output = result_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManagerExecutionError("The configured Task Assistant did not return a readable result.") from exc
    parsed = _parse_interpretation(output)
    return {
        "schema_version": "intent_interpretation.v1",
        "source": "manager_worker",
        "summary": parsed["summary"],
        "items": [
            {"kind": "explicit_request", "text": item}
            for item in parsed["items"]
        ],
        "request": change_request,
        "manager_execution": {
            "job_id": job_id,
            "role": "orchestrator",
            "adapter": adapter,
            "model": str(result.get("runtime_model") or model),
            "reasoning_depth": str(settings.get("reasoning_depth") or "default"),
        },
    }


def _model_name(configured_model: str) -> str:
    if ":" not in configured_model:
        return configured_model
    _configured_adapter, model = configured_model.split(":", 1)
    return model.strip()


def _manager_interpretation_task(change_request: str) -> str:
    return "\n".join(
        [
            "Produce a bounded Task Assistant interpretation of this request for human confirmation.",
            "The JSON shape must be: {\"summary\": \"short restatement\", \"items\": [\"explicit item\"]}.",
            "Every item must be directly supported by the exact request below.",
            "",
            "## Exact user request",
            change_request,
        ]
    )


def _parse_interpretation(output: str) -> dict[str, Any]:
    text = str(output or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    try:
        payload = json.loads(candidate)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManagerExecutionError("The configured Task Assistant did not return a structured interpretation.") from exc
    if not isinstance(payload, dict):
        raise ManagerExecutionError("The configured Task Assistant did not return a structured interpretation.")
    summary = str(payload.get("summary", "") or "").strip()
    raw_items = payload.get("items")
    if not summary or len(summary) > 4_000 or not isinstance(raw_items, list):
        raise ManagerExecutionError("The configured Task Assistant returned an invalid structured interpretation.")
    items: list[str] = []
    for raw_item in raw_items:
        item = str(raw_item or "").strip()
        if item and item not in items:
            items.append(item)
    if not items or len(items) > 20 or any(len(item) > 1_000 for item in items):
        raise ManagerExecutionError("The configured Task Assistant returned an invalid structured interpretation.")
    return {"summary": summary, "items": items}
