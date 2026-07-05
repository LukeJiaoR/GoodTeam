import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from goodteam_os.state import StateStore


MESSAGE_KINDS = ("ask", "assert", "reject", "propose")
IDENTITY_FIELDS = ("run_id", "ts", "from_role", "kind", "gate", "body", "in_reply_to")


def append_message(
    workspace: Path,
    project_key: str,
    run_id: str,
    *,
    from_role: str,
    to: list[str],
    kind: str,
    body: str,
    gate: str = "",
    refs: list[str] | None = None,
    in_reply_to: str = "",
) -> dict:
    run_id_value = _required_text(run_id, "run_id")
    from_role_value = _required_text(from_role, "from_role")
    kind_value = _message_kind(kind)
    body_value = _required_text(body, "body")
    to_values = _message_targets(to)
    refs_values = _message_refs(refs)
    gate_value = str(gate).strip()
    in_reply_to_value = str(in_reply_to).strip()

    messages = read_messages(workspace, project_key, run_id_value)
    for existing in messages:
        if (
            str(existing.get("run_id", "")) == run_id_value
            and str(existing.get("from_role", "")) == from_role_value
            and str(existing.get("kind", "")) == kind_value
            and str(existing.get("gate", "")) == gate_value
            and str(existing.get("body", "")) == body_value
            and str(existing.get("in_reply_to", "")) == in_reply_to_value
        ):
            return existing

    row = {
        "id": "",
        "run_id": run_id_value,
        "ts": datetime.now(UTC).isoformat(),
        "from_role": from_role_value,
        "to": to_values,
        "kind": kind_value,
        "gate": gate_value,
        "body": body_value,
        "refs": refs_values,
        "in_reply_to": in_reply_to_value,
    }
    row["id"] = _message_id(row)
    path = _message_path(workspace, project_key, run_id_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_messages(
    workspace: Path,
    project_key: str,
    run_id: str,
    *,
    gate: str | None = None,
) -> list[dict]:
    path = _message_path(workspace, project_key, _required_text(run_id, "run_id"))
    if not path.is_file():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        messages.append(json.loads(line))
    if gate is not None:
        gate_value = str(gate)
        messages = [message for message in messages if str(message.get("gate", "")) == gate_value]
    return messages


def read_role_messages(
    workspace: Path,
    project_key: str,
    run_id: str,
    role_key: str,
    *,
    gate: str | None = None,
) -> list[dict]:
    role_value = _required_text(role_key, "role_key")
    messages = []
    for message in read_messages(workspace, project_key, run_id, gate=gate):
        targets = message.get("to", [])
        if not isinstance(targets, list):
            continue
        if role_value in [str(target).strip() for target in targets]:
            messages.append(message)
    return messages


def _message_path(workspace: Path, project_key: str, run_id: str) -> Path:
    if Path(run_id).name != run_id:
        raise ValueError("run_id must not contain path separators.")
    return StateStore(workspace).project_state_dir(project_key) / "messages" / f"{run_id}.jsonl"


def _message_id(message: dict[str, object]) -> str:
    identity_payload = {key: message.get(key) for key in IDENTITY_FIELDS}
    payload = json.dumps(identity_payload, sort_keys=True, ensure_ascii=False)
    return "msg:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _message_targets(to: list[str]) -> list[str]:
    if not isinstance(to, list) or not to:
        raise ValueError("Message to must be a non-empty list.")
    targets = [str(target).strip() for target in to]
    if any(not target for target in targets):
        raise ValueError("Message to must contain non-empty targets.")
    return targets


def _message_refs(refs: list[str] | None) -> list[str]:
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ValueError("Message refs must be a list.")
    return [str(ref).strip() for ref in refs if str(ref).strip()]


def _required_text(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"Message {field} is required.")
    return text


def _message_kind(value: str) -> str:
    kind = str(value).strip()
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"Unknown message kind: {kind}")
    return kind
