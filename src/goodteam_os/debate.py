from collections.abc import Callable
from pathlib import Path
from typing import Any

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import append_message, read_messages
from goodteam_os.workers import WorkerJobStore


DispatchFn = Callable[..., list[str]]
MAX_DEBATE_ROUNDS = 5
VOTE_VALUES = ("approve", "reject", "abstain")


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
    rounds: int = 1,
) -> list[dict]:
    round_count = _clamped_rounds(rounds)
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

    owner_messages = []
    rebuttals = dispatch_fn(
        role=owner_role,
        kind="rebut",
        gate=gate,
        challenges=challenger_messages,
    )
    for challenge, rebuttal in zip(challenger_messages, _text_items(rebuttals), strict=False):
        message_kind, body = _prefixed_kind(rebuttal, allowed=("assert", "propose"), default="assert")
        owner_messages.append(
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
        )

    for _round_index in range(2, round_count + 1):
        if not owner_messages:
            break
        challenges = dispatch_fn(
            role=challenger_role,
            kind="challenge",
            gate=gate,
            artifact_ref=artifact_ref,
            challenges=owner_messages,
        )
        challenger_messages = []
        for owner_message, challenge in zip(owner_messages, _text_items(challenges)[:3], strict=False):
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
                    in_reply_to=str(owner_message["id"]),
                )
            )
        if not challenger_messages:
            break

        owner_messages = []
        rebuttals = dispatch_fn(
            role=owner_role,
            kind="rebut",
            gate=gate,
            challenges=challenger_messages,
        )
        for challenge, rebuttal in zip(challenger_messages, _text_items(rebuttals), strict=False):
            message_kind, body = _prefixed_kind(rebuttal, allowed=("assert", "propose"), default="assert")
            owner_messages.append(
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
            )

    return read_messages(workspace, project_key, run_id, gate=gate)


class DebateGated(Exception):
    """Raised internally when the team gates a debate role."""


def _team_worker_runtime(
    workspace: Path,
    project_key: str,
    role: str,
    *,
    fallback_adapter: str,
) -> dict[str, str]:
    """Runtime for a debate job: the role's team Worker when one is staffed.

    Team states gate fail closed: a disabled or unstaffed/unready role raises
    :class:`DebateGated`, and the dispatch produces no lines and no job.
    A state file that exists but cannot be read is corrupted truth, not
    absent truth, so it also fails closed instead of falling back to legacy.
    Only projects with no team state at all keep the explicit adapter.
    """
    from goodteam_os.team_control_plane import (
        TeamControlPlaneError,
        TeamControlPlaneStore,
        resolve_worker_from_state,
    )

    try:
        state = TeamControlPlaneStore(workspace, project_key).read()
    except TeamControlPlaneError as exc:
        # Corrupt team state must fail closed: no debate lines, no job.
        raise DebateGated(f"Team control-plane state is unreadable: {exc}") from exc
    if state is None:
        return {
            "adapter": fallback_adapter,
            "model": "",
            "reasoning_depth": "",
            "additional_prompt": "",
            "worker_id": "",
            "worker_name": "",
        }
    try:
        resolution = resolve_worker_from_state(state, role)
    except TeamControlPlaneError as exc:
        raise DebateGated(str(exc)) from exc
    if resolution is None:  # pragma: no cover - state implies a resolution
        raise DebateGated(f"Team state has no resolution for role: {role}")
    return {
        "adapter": str(resolution["adapter"]),
        "model": str(resolution["model"] or ""),
        "reasoning_depth": str(resolution["reasoning_depth"]),
        "additional_prompt": str(resolution["additional_prompt"]),
        "worker_id": str(resolution["worker_id"]),
        "worker_name": str(resolution["worker_name"]),
    }


def default_debate_dispatch(
    workspace: Path,
    project_key: str,
    run_id: str,
    *,
    adapter: str = "codex",
) -> DispatchFn:
    from goodteam_os.skills.models import SkillError
    from goodteam_os.skills.task_start import workflow_job_fields
    from goodteam_os.state import StateStore

    original_state = StateStore(workspace).read_project_current(project_key)
    if original_state is not None and original_state.run_id == run_id:
        workflow_job_fields(original_state, role="review", command_key="debate-reference-check")
        if original_state.task_skill_context:
            raise SkillError("skill_debate_not_connected", "Skill-backed debate is not connected; no legacy discussion was dispatched.")
    else:
        from goodteam_os.skills.task_locks import TaskSkillIdentity
        from goodteam_os.skills.task_start import reject_missing_task_context

        # Missing/currently different state cannot erase a registered task's
        # method identity, even after the project restores default methods.
        reject_missing_task_context(TaskSkillIdentity("workflow_run", project_key, run_id))

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
            if original_state is None or original_state.run_id != run_id:
                from goodteam_os.team_control_plane import TeamControlPlaneStore
                from goodteam_os.skills.worker_requests import subject_for_role

                team = TeamControlPlaneStore(workspace, project_key).read()
                if team and team.get("skill_bindings", {}).get(subject_for_role(role), {}).get("mode") == "custom":
                    raise SkillError("skill_debate_not_connected", "Custom-method debate requires an explicit supported task context.")
            try:
                worker_runtime = _team_worker_runtime(workspace, project_key, role, fallback_adapter=adapter)
            except DebateGated:
                return []
            job = store.create_job(
                run_id=run_id,
                role=role,
                adapter=worker_runtime["adapter"],
                model=worker_runtime["model"],
                task=task,
                context=context,
                constraints=[
                    "Do not edit/create/delete any file",
                    "Output debate lines only",
                ],
                expected_artifacts=["result.md"],
                reasoning_depth=worker_runtime["reasoning_depth"],
                additional_prompt=worker_runtime["additional_prompt"],
                worker_id=worker_runtime["worker_id"],
                worker_name=worker_runtime["worker_name"],
            )
            status = store.run_job(str(job["id"]))
            if status.get("status") != "completed":
                return []
            result_path = workspace / ".goodteam" / "workers" / "jobs" / str(job["id"]) / "result.md"
            if not result_path.is_file():
                return []
            return _parse_debate_objections(result_path.read_text(encoding="utf-8"), allowed=allowed)
        except SkillError:
            raise
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


def record_gate_vote(
    workspace: Path,
    project_key: str,
    run_id: str,
    gate: str,
    *,
    role: str,
    vote: str,
) -> dict:
    vote_value = str(vote).strip().lower()
    if vote_value not in VOTE_VALUES:
        raise ValueError(f"Unknown gate vote: {vote}")
    return append_message(
        workspace,
        project_key,
        run_id,
        from_role=role,
        to=["broadcast"],
        kind="assert",
        gate=gate,
        body=f"VOTE:{vote_value}",
    )


def gate_quorum_status(
    workspace: Path,
    project_key: str,
    run_id: str,
    gate: str,
    *,
    quorum_roles: list[str],
    threshold: int,
) -> dict:
    roles = _unique_texts(quorum_roles)
    latest_votes: dict[str, str] = {}
    for message in read_messages(workspace, project_key, run_id, gate=gate):
        role = str(message.get("from_role", "")).strip()
        if role not in roles:
            continue
        vote = _message_vote(message)
        if vote:
            latest_votes[role] = vote

    tally = {vote: 0 for vote in VOTE_VALUES}
    for vote in latest_votes.values():
        tally[vote] += 1
    return {
        "tally": tally,
        "met": tally["approve"] >= int(threshold),
        "outstanding": [role for role in roles if role not in latest_votes],
    }


def _clamped_rounds(rounds: int) -> int:
    return max(1, min(int(rounds), MAX_DEBATE_ROUNDS))


def _text_items(items: list[str]) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("Debate dispatch must return a list of texts.")
    return [str(item).strip() for item in items if str(item).strip()]


def _unique_texts(items: list[str]) -> list[str]:
    unique = []
    seen = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            unique.append(text)
            seen.add(text)
    return unique


def _message_vote(message: dict) -> str:
    if str(message.get("kind", "")) != "assert":
        return ""
    body = str(message.get("body", "")).strip()
    prefix = "VOTE:"
    if not body.upper().startswith(prefix):
        return ""
    vote = body[len(prefix) :].strip().lower()
    return vote if vote in VOTE_VALUES else ""


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
