from pathlib import Path
import re

from goodteam_os.resources import (
    ResourceCapability,
    ResourceKind,
    ResourceScope,
    derive_resources_from_legacy_artifacts,
    register_external_reference,
    register_markdown_artifact,
    register_task_ledger,
)
from goodteam_os.state import create_initial_state
from goodteam_os.state import StateStore
from goodteam_os.web import read_artifact


def test_register_markdown_artifact_declares_editable_reviewable_run_resource(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build")
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"

    descriptor = register_markdown_artifact(state, "prd", artifact, producer="product_requirement")

    assert descriptor.kind == ResourceKind.MARKDOWN_ARTIFACT
    assert descriptor.scope == ResourceScope.RUN
    assert descriptor.path == str(artifact)
    assert descriptor.mime == "text/markdown"
    assert descriptor.producer == "product_requirement"
    assert ResourceCapability.EDIT in descriptor.capabilities
    assert ResourceCapability.REVIEW in descriptor.capabilities
    assert state.resources["prd"] == descriptor
    assert state.artifacts["prd"] == str(artifact)


def test_register_task_ledger_declares_board_source_without_edit(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build")
    ledger = tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"

    descriptor = register_task_ledger(state, ledger)

    assert descriptor.kind == ResourceKind.TASK_LEDGER
    assert descriptor.scope == ResourceScope.PROJECT_RUNTIME
    assert descriptor.mime == "application/json"
    assert ResourceCapability.BOARD_SOURCE in descriptor.capabilities
    assert ResourceCapability.READ in descriptor.capabilities
    assert ResourceCapability.EDIT not in descriptor.capabilities
    assert state.resources["task_ledger"] == descriptor
    assert state.artifacts["task_ledger"] == str(ledger)


def test_register_external_reference_stays_out_of_legacy_artifacts():
    state = create_initial_state("run-1", "GTO", "Build")

    descriptor = register_external_reference(
        state,
        "release_pr",
        "https://github.com/example/project/pull/42",
        title="Release PR",
        capabilities=[ResourceCapability.READ, ResourceCapability.PREVIEW],
    )

    assert descriptor.kind == ResourceKind.EXTERNAL_REFERENCE
    assert descriptor.scope == ResourceScope.EXTERNAL
    assert descriptor.url == "https://github.com/example/project/pull/42"
    assert descriptor.path == ""
    assert descriptor.capabilities == [ResourceCapability.READ, ResourceCapability.PREVIEW]
    assert descriptor.legacy_artifact is False
    assert state.resources["release_pr"] == descriptor
    assert "release_pr" not in state.artifacts


def test_derive_resources_from_legacy_artifacts_classifies_known_keys(tmp_path):
    run_root = tmp_path / "artifacts" / "runs" / "run-1"
    legacy = {
        "prd": str(run_root / "prd" / "prd.md"),
        "prd_proposed": str(run_root / "prd" / "prd.proposed.md"),
        "task_ledger": str(tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"),
        "notes": str(run_root / "notes" / "notes.md"),
    }

    resources = derive_resources_from_legacy_artifacts(legacy, run_id="run-1", workspace=tmp_path)

    assert resources["prd"].kind == ResourceKind.MARKDOWN_ARTIFACT
    assert ResourceCapability.EDIT in resources["prd"].capabilities
    assert ResourceCapability.REVIEW in resources["prd"].capabilities
    assert resources["prd_proposed"].kind == ResourceKind.PROPOSED_MARKDOWN_ARTIFACT
    assert ResourceCapability.DIFF in resources["prd_proposed"].capabilities
    assert ResourceCapability.EDIT not in resources["prd_proposed"].capabilities
    assert resources["task_ledger"].kind == ResourceKind.TASK_LEDGER
    assert ResourceCapability.BOARD_SOURCE in resources["task_ledger"].capabilities
    assert resources["notes"].kind == ResourceKind.MARKDOWN_ARTIFACT
    assert ResourceCapability.EDIT not in resources["notes"].capabilities


def test_state_store_migrates_legacy_artifacts_to_resources(tmp_path):
    state_path = tmp_path / "state" / "current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        """{
  "run_id": "run-1",
  "project_key": "GTO",
  "idea": "Build",
  "approvals": {"prd": "pending"},
  "artifacts": {
    "prd": "artifacts/runs/run-1/prd/prd.md",
    "task_ledger": "/tmp/project/.goodteam/tasks/tasks.json"
  }
}""",
        encoding="utf-8",
    )

    state = StateStore(tmp_path).read_current()

    assert state is not None
    assert state.resources["prd"].kind == ResourceKind.MARKDOWN_ARTIFACT
    assert ResourceCapability.EDIT in state.resources["prd"].capabilities
    assert state.resources["task_ledger"].kind == ResourceKind.TASK_LEDGER
    assert ResourceCapability.BOARD_SOURCE in state.resources["task_ledger"].capabilities
    assert state.artifacts == {
        "prd": "artifacts/runs/run-1/prd/prd.md",
        "task_ledger": "/tmp/project/.goodteam/tasks/tasks.json",
    }


def test_read_artifact_uses_resource_capability_for_markdown(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build")
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# PRD", encoding="utf-8")
    register_markdown_artifact(state, "prd", artifact)
    StateStore(tmp_path).write_current(state)

    payload = read_artifact(workspace=tmp_path, artifact_key="prd")

    assert payload["artifact"] == "prd"
    assert payload["content"] == "# PRD"
    assert payload["resource"]["kind"] == "markdown_artifact"


def test_read_artifact_rejects_task_ledger_by_capability(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build")
    ledger = tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"tasks": []}', encoding="utf-8")
    register_task_ledger(state, ledger)
    StateStore(tmp_path).write_current(state)

    try:
        read_artifact(workspace=tmp_path, artifact_key="task_ledger")
    except ValueError as exc:
        assert "Resource is not a markdown artifact" in str(exc)
    else:
        raise AssertionError("Expected task ledger to be rejected by artifact reader")


def test_production_code_does_not_write_state_artifacts_directly():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    pattern = re.compile(r"state\.artifacts\[[^\]]+\]\s*=")
    for path in (root / "src" / "goodteam_os").glob("*.py"):
        if path.name == "resources.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{lineno}:{line.strip()}")

    assert offenders == []
