"""Revision authority for editable workflow artifacts and their derived evidence.

Removing a current reference never deletes historical files or audit records.
The dependency map describes artifact producers within the workflow runners;
separate branches (for example QA and code review) are not invalidated together.
"""
from pathlib import Path
from uuid import uuid4

from goodteam_os.reviewer_dispatch import artifact_version_for_path
from goodteam_os.roles import approval_artifact_for_gate
from goodteam_os.state import ApprovalDecision
from goodteam_os.workflow_table import resolve_profile


GREENFIELD_DEPENDENCIES = {
    "takeover": (), "prd": ("takeover",), "rfc": ("prd",),
    "review_report": ("rfc",), "task_breakdown": ("rfc", "review_report"),
    "ui_spec": ("task_breakdown",),
    "implementation_handoff": ("task_breakdown", "ui_spec"),
    "qa_report": ("implementation_handoff",), "code_review": ("implementation_handoff",),
    "staging_report": ("qa_report", "code_review"),
    "release_notes": ("staging_report", "qa_report", "code_review"),
    "retro_memory": ("release_notes",),
}
FIX_DEPENDENCIES = {
    "fix_plan": (), "implementation_handoff": ("fix_plan",),
    "qa_report": ("implementation_handoff",), "retro_memory": ("qa_report",),
}


def invalidate_artifact(state, artifact_key, *, rewind=False):
    """Invalidate the changed artifact's gates and derived current references."""
    profile = resolve_profile(state.load_profile)
    dependencies = FIX_DEPENDENCIES if profile.name == "fix" else GREENFIELD_DEPENDENCIES
    affected = {artifact_key}
    while True:
        expanded = affected | {key for key, inputs in dependencies.items() if affected.intersection(inputs)}
        if expanded == affected:
            break
        affected = expanded
    generation = uuid4().hex
    for key in affected:
        state.artifact_generations[key] = generation
        state.artifact_proposals.pop(key, None)
        state.resources.pop(f"{key}_proposed", None)
        state.artifacts.pop(f"{key}_proposed", None)
        if key != artifact_key:
            state.resources.pop(key, None)
            state.artifacts.pop(key, None)
    own_gates = []
    affected_gates = []
    for gate in profile.gate_names():
        gated_artifact = approval_artifact_for_gate(gate)
        if gated_artifact in affected:
            affected_gates.append(gate)
            state.approvals[gate] = ApprovalDecision.PENDING
            if gated_artifact == artifact_key:
                own_gates.append(gate)
    if rewind and own_gates:
        from goodteam_os.nodes.review_gate import gate_next_node

        # An explicit upstream revision restarts review and regeneration for
        # these descendants. Keep blockers unrelated to the restarted evidence.
        resolved = tuple(f"Artifact {key} changed. Derived approvals and artifacts were invalidated." for key in affected)
        state.blockers = [message for message in state.blockers if not message.startswith(resolved)]
        gate = own_gates[0]
        state.current_node = f"{gate}_review_gate"
        state.next_node = gate_next_node(state, gate=gate, approved_next=profile.approved_next_for_gate(gate))
        # A completed old decision must not replay after its evidence changes.
        state.pending_transition = {}
    elif rewind and affected_gates:
        # These artifacts are produced inside a bundled delivery stage. There is
        # no safe resume node that both rebuilds descendants and preserves the
        # human edit. Do not leave an old downstream approval actionable.
        state.current_node = "artifact_revision"
        state.next_node = "blocked"
        state.pending_transition = {}
        message = (f"Artifact {artifact_key} changed. Derived approvals and artifacts were invalidated. "
                   "Regenerate the affected delivery artifacts and review them before continuing; "
                   "automatic resume is unavailable for this intermediate artifact. "
                   "Incorporate the required changes into the upstream RFC, save a new RFC revision, "
                   "then reapprove RFC and security to regenerate delivery.")
        if message not in state.blockers:
            state.blockers.append(message)
    return affected


def capture_proposal(state, artifact_key, proposed_path):
    binding = {
        "proposal_id": uuid4().hex, "run_id": state.run_id, "artifact_key": artifact_key,
        "base_version": artifact_version_for_path(state.artifacts.get(artifact_key, "")),
        "proposal_version": artifact_version_for_path(str(proposed_path)),
    }
    state.artifact_proposals[artifact_key] = binding
    return dict(binding)


def require_proposal(state, artifact_key, *, expected_run_id="", expected_artifact_key="",
                     expected_artifact_version="", expected_proposal_id="", expected_proposal_version=""):
    expected = {
        "run_id": expected_run_id, "artifact_key": expected_artifact_key,
        "base_version": expected_artifact_version, "proposal_id": expected_proposal_id,
        "proposal_version": expected_proposal_version,
    }
    if not all(isinstance(value, str) and value.strip() for value in expected.values()):
        raise ValueError("Proposal binding is required. Reload the exact proposal before acting.")
    binding = state.artifact_proposals.get(artifact_key)
    if (not binding or expected != binding or binding["run_id"] != state.run_id
            or binding["artifact_key"] != artifact_key
            or binding["base_version"] != artifact_version_for_path(state.artifacts.get(artifact_key, ""))
            or binding["proposal_version"] != artifact_version_for_path(state.artifacts.get(f"{artifact_key}_proposed", ""))):
        raise ValueError("This proposal changed. Reload the exact proposal before acting.")
    return dict(binding)


def current_job_artifact(job_root: Path, status, *, artifact_key, filename):
    """Only the current job's result is authoritative, never a shared docs mirror."""
    from goodteam_os.graph import _is_worker_meta_summary

    # result_path is adapter output, but it cannot point at a previous job or a
    # shared repository document. A missing current result is a failure.
    root = job_root.resolve()
    result = Path(str(status.get("result_path") or root / "result.md")).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        return None
    text = result.read_text(encoding="utf-8")
    adapter_placeholder = text.startswith("# Worker Result\n") and (
        "See stdout.txt and stderr.txt." in text or "Status: completed-without-assistant-text" in text
    )
    if text.strip() and not _is_worker_meta_summary(text) and not adapter_placeholder:
        return text
    # File-producing adapters can return a job-local deliverable. Require the
    # exact declared filename and job-local ownership, not arbitrary references.
    artifact = (root / filename).resolve()
    if artifact.is_relative_to(root) and artifact.is_file():
        content = artifact.read_text(encoding="utf-8")
        if content.strip() and not _is_worker_meta_summary(content):
            return content
    return None
