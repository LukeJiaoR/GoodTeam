from collections.abc import Callable
from pathlib import Path
from typing import Any

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import append_message, read_messages
from goodteam_os.workers import WorkerJobStore


DispatchFn = Callable[..., list[str]]


def run_gate_debate(
    workspace: Path,
    project_key: str,
    run_id: str,
    gate: str,
    *,
    challenger_role: str,
    owner_role: str,
    dispatch_fn: DispatchFn,
    artifact_ref: str = "",
) -> list[dict]:
    challenges = dispatch_fn(
        role=challenger_role,
        kind="challenge",
        gate=gate,
        artifact_ref=artifact_ref,
    )
    challenger_messages = []
    refs = [artifact_ref] if artifact_ref else []
    for challenge in _text_items(challenges)[:3]:
        message_kind, body = _prefixed_kind(challenge, allowed=("ask", "reject"), default="ask")
        challenger_messages.append(
            append_message(
                workspace,
                project_key,
                run_id,
                from_role=challenger_role,
                to=[owner_role],
                kind=message_kind,
                gate=gate,
                body=body,
                refs=refs,
            )
        )

    rebuttals = dispatch_fn(
        role=owner_role,
        kind="rebut",
        gate=gate,
        challenges=challenger_messages,
    )
    for challenge, rebuttal in zip(challenger_messages, _text_items(rebuttals), strict=False):
        message_kind, body = _prefixed_kind(rebuttal, allowed=("assert", "propose"), default="assert")
        append_message(
            workspace,
            project_key,
            run_id,
            from_role=owner_role,
            to=[challenger_role],
            kind=message_kind,
            gate=gate,
            body=body,
            in_reply_to=str(challenge["id"]),
        )

    return read_messages(workspace, project_key, run_id, gate=gate)


def default_debate_dispatch(
    workspace: Path,
    project_key: str,
    run_id: str,
    *,
    adapter: str = "codex",
) -> DispatchFn:
    def dispatch(**kwargs: Any) -> list[str]:
        try:
            role = str(kwargs["role"])
            kind = str(kwargs["kind"])
            gate = str(kwargs["gate"])
            artifact_ref = str(kwargs.get("artifact_ref", "")).strip()
            challenges = kwargs.get("challenges", [])
            allowed = ("ask", "reject") if kind == "challenge" else ("assert", "propose")

            task = _default_dispatch_task(kind=kind, gate=gate, artifact_ref=artifact_ref, challenges=challenges)
            context = _default_dispatch_context(
                project_key=project_key,
                run_id=run_id,
                gate=gate,
                artifact_ref=artifact_ref,
                challenges=challenges,
            )
            store = WorkerJobStore(workspace)
            job = store.create_job(
                run_id=run_id,
                role=role,
                adapter=adapter,
                task=task,
                context=context,
                constraints=[
                    "Do not edit/create/delete any file",
                    "Output debate lines only",
                ],
                expected_artifacts=["result.md"],
            )
            status = store.run_job(str(job["id"]))
            if status.get("status") != "completed":
                return []
            result_path = workspace / ".goodteam" / "workers" / "jobs" / str(job["id"]) / "result.md"
            if not result_path.is_file():
                return []
            return _parse_debate_objections(result_path.read_text(encoding="utf-8"), allowed=allowed)
        except Exception:
            return []

    return dispatch


def record_gate_decision(
    workspace: Path,
    project_key: str,
    run_id: str,
    gate: str,
    *,
    decision: str,
    by_role: str,
    memory_store: RoleMemoryStore | None = None,
) -> dict:
    decision_message = append_message(
        workspace,
        project_key,
        run_id,
        from_role=by_role,
        to=["broadcast"],
        kind="assert",
        gate=gate,
        body=f"DECISION: {decision}",
    )
    if memory_store is None:
        return decision_message

    challenger_count = sum(
        1
        for message in read_messages(workspace, project_key, run_id, gate=gate)
        if str(message.get("kind", "")) in {"ask", "reject"}
    )
    text = f"Gate {gate} decision: {decision}. Challenger messages: {challenger_count}."
    source = f"messages/{run_id}.jsonl#{decision_message['id']}"
    try:
        memory_store.append_memory_event(by_role, horizon="mid-term", text=text, source=source)
    except Exception:
        pass
    return decision_message


def _text_items(items: list[str]) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("Debate dispatch must return a list of texts.")
    return [str(item).strip() for item in items if str(item).strip()]


def _prefixed_kind(text: str, *, allowed: tuple[str, ...], default: str) -> tuple[str, str]:
    for kind in allowed:
        prefix = f"{kind}:"
        if text.lower().startswith(prefix):
            body = text[len(prefix) :].strip()
            return kind, body or text
    return default, text


def _default_dispatch_task(*, kind: str, gate: str, artifact_ref: str, challenges: Any) -> str:
    if kind == "challenge":
        return (
            f"Read the {gate} artifact ({artifact_ref}) and produce UP TO THREE concise, "
            "specific objections. One per line, each prefixed `reject:` (blocking) or "
            "`ask:` (clarifying). Output ONLY those lines. Do NOT modify, create, or "
            "delete any file."
        )
    challenge_lines = [
        f"- {item.get('body', '')}"
        for item in challenges
        if isinstance(item, dict) and str(item.get("body", "")).strip()
    ]
    return "\n".join(
        [
            f"Respond to each challenge for the {gate} gate. One per line, prefixed "
            "`propose:` (a concrete change) or `assert:` (a defense). Output ONLY "
            "those lines. Do NOT modify any file.",
            "",
            "Challenges:",
            *(challenge_lines or ["- (none provided)"]),
        ]
    )


def _default_dispatch_context(
    *,
    project_key: str,
    run_id: str,
    gate: str,
    artifact_ref: str,
    challenges: Any,
) -> str:
    challenge_lines = [
        f"- {item.get('body', '')}"
        for item in challenges
        if isinstance(item, dict) and str(item.get("body", "")).strip()
    ]
    return "\n".join(
        [
            f"Project: {project_key}",
            f"Run: {run_id}",
            f"Gate: {gate}",
            f"Artifact: {artifact_ref or '(none)'}",
            "Challenge bodies:",
            *(challenge_lines or ["- (none)"]),
        ]
    )


def _parse_debate_objections(text: str, *, allowed: tuple[str, ...]) -> list[str]:
    parsed: list[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#"):
            continue
        for marker in ("- ", "* "):
            if line.startswith(marker):
                line = line[len(marker) :].strip()
                break
        if not line:
            continue
        lowered = line.lower()
        if any(lowered.startswith(f"{kind}:") for kind in allowed):
            parsed.append(line)
        elif not any(lowered.startswith(f"{kind}:") for kind in ("ask", "reject", "assert", "propose")):
            parsed.append(line)
        if len(parsed) >= 3:
            break
    return parsed
