import json
from pathlib import Path

from goodteam_os.state import create_initial_state
from goodteam_os.web import decide_approval, start_workflow
from goodteam_os.workflow_table import (
    FIX_PROFILE,
    GREENFIELD_PROFILE,
    resolve_profile,
)


def _write_fake_project(tmp_path: Path) -> Path:
    """A minimal project config wired to the deterministic `fake` worker."""
    project = tmp_path / "fixproj"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (docs / "codex.md").write_text("# Memory\n- A small service with a bug to fix.\n", encoding="utf-8")
    config = tmp_path / "fixproj.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Fix Project",
                "key: FIXPROJ",
                "repos:",
                f"  docs: {docs}",
                "worker_backend:",
                "  mode: fake",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _action_log(tmp_path: Path) -> list[dict]:
    path = tmp_path / "state" / "projects" / "FIXPROJ" / "action-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- Profile isolation -------------------------------------------------------


def test_resolve_profile_fix_is_registered_and_named():
    assert resolve_profile("fix") is FIX_PROFILE
    assert resolve_profile("fix").name == "fix"


def test_fix_nodes_and_gates_are_disjoint_from_greenfield():
    fix_nodes = set(FIX_PROFILE.nodes)
    fix_gates = set(FIX_PROFILE.gate_transitions)
    greenfield_nodes = set(GREENFIELD_PROFILE.nodes)
    greenfield_gates = set(GREENFIELD_PROFILE.gate_transitions)

    assert fix_nodes == {"fix_implement", "fix_qa", "fix_close"}
    assert fix_nodes.isdisjoint(greenfield_nodes)
    # `code_review`/`qa` gate names are unique to fix; greenfield never declares them.
    assert fix_gates == {"fix_plan", "code_review", "qa"}
    assert fix_gates.isdisjoint(greenfield_gates)


def test_greenfield_profile_never_sees_fix_nodes():
    for fix_node in ("fix_implement", "fix_qa", "fix_close"):
        assert GREENFIELD_PROFILE.node_for(fix_node) is None


def test_fix_gate_transitions_chain_correctly():
    assert FIX_PROFILE.approved_next_for_gate("fix_plan") == "fix_implement"
    assert FIX_PROFILE.approved_next_for_gate("code_review") == "fix_qa"
    assert FIX_PROFILE.approved_next_for_gate("qa") == "fix_close"


# --- Fix end-to-end ----------------------------------------------------------


def test_fix_profile_runs_end_to_end_with_fake_worker(tmp_path):
    project_config = _write_fake_project(tmp_path)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Fix the off-by-one error in the pagination helper",
        project_key="FIXPROJ",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        source_type="new_project",
        project_config=project_config,
        load_profile="fix",
    )

    # 1) start -> fix_plan gate, with a fix_plan artifact.
    assert payload["load_profile"] == "fix"
    assert payload["current_node"] == "fix_plan_review_gate"
    assert payload["next_node"] == "await_fix_plan_approval"
    assert "fix_plan" in payload["artifacts"]
    assert Path(payload["artifacts"]["fix_plan"]).is_file()

    # 2) approve fix_plan -> code_review gate, with an implementation artifact.
    payload = decide_approval(
        workspace=tmp_path,
        project_key="FIXPROJ",
        approval="fix_plan",
        decision="approved",
        note="Model-free acceptance: continue with simulated evidence to verify the workflow contract.",
    )
    assert payload["next_node"] == "await_code_review_approval"
    assert "implementation_handoff" in payload["artifacts"]
    assert Path(payload["artifacts"]["implementation_handoff"]).is_file()

    # 3) approve code_review -> qa gate.
    payload = decide_approval(
        workspace=tmp_path,
        project_key="FIXPROJ",
        approval="code_review",
        decision="approved",
        note="Model-free acceptance: continue with simulated evidence to verify the workflow contract.",
    )
    assert payload["next_node"] == "await_qa_approval"
    assert "qa_report" in payload["artifacts"]
    assert Path(payload["artifacts"]["qa_report"]).is_file()

    # 4) approve qa -> run complete.
    payload = decide_approval(
        workspace=tmp_path,
        project_key="FIXPROJ",
        approval="qa",
        decision="approved",
        note="Model-free acceptance: continue with simulated evidence to verify the workflow contract.",
    )
    assert payload["next_node"] == ""
    assert payload["current_node"] == "complete"

    # Each decision wrote an approval_decided audit row with the right gate.
    decided = [a for a in _action_log(tmp_path) if a["action"] == "approval_decided"]
    assert [a["gate"] for a in decided] == ["fix_plan", "code_review", "qa"]
    for row in decided:
        assert row["decision"] == "approved"


def test_fix_dry_run_plans_fix_plan_gate(tmp_path):
    project_config = _write_fake_project(tmp_path)
    payload = start_workflow(
        workspace=tmp_path,
        idea="Fix a flaky test",
        project_key="FIXPROJ",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        source_type="new_project",
        project_config=project_config,
        load_profile="fix",
        dry_run=True,
    )
    assert payload["dry_run"] is True
    assert payload["load_profile"] == "fix"
    assert payload["next_node"] == "await_fix_plan_approval"
    assert "fix_plan" in payload["planned_artifacts"]


def test_default_start_stays_greenfield(tmp_path):
    state = create_initial_state("run-1", "FIXPROJ", "Build something new")
    # A plain greenfield state must resolve to the greenfield profile.
    assert resolve_profile(state.load_profile) is GREENFIELD_PROFILE
