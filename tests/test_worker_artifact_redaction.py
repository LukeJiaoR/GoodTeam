"""Synthetic credentials must not propagate from Worker reports into artifacts."""
import pytest

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.graph import (
    format_worker_evidence_lines,
    render_downstream_artifact_markdown,
    sanitize_artifact_excerpt,
)
from goodteam_os.state import create_initial_state


@pytest.mark.parametrize("credential", [
    "Authorization: Bearer SYNTHETIC_CREDENTIAL",
    "authorization = Basic SYNTHETIC_CREDENTIAL",
    "AUTHORIZATION:\tBearer\tSYNTHETIC_CREDENTIAL",
    '"Authorization": "Bearer SYNTHETIC_CREDENTIAL"',
    "{'authorization': 'Basic SYNTHETIC_CREDENTIAL'}",
    r'\"Authorization\": \"Bearer SYNTHETIC_CREDENTIAL\"',
    "curl -H 'Authorization: Bearer SYNTHETIC_CREDENTIAL' https://example.invalid",
    'Authorization: Digest username="SYNTHETIC_CREDENTIAL", response="SYNTHETIC_RESPONSE"',
    "Proxy-Authorization: Basic SYNTHETIC_CREDENTIAL",
    "HTTP_AUTHORIZATION=Bearer SYNTHETIC_CREDENTIAL",
    "Authorization:\r\n\tBearer SYNTHETIC_CREDENTIAL",
    "Authorization: Bearer\n  SYNTHETIC_CREDENTIAL",
    "Authorization: Digest username=example,\n  response=SYNTHETIC_CREDENTIAL",
    "Authorization:\nBearer SYNTHETIC_CREDENTIAL",
    "Authorization: Bearer\nSYNTHETIC_CREDENTIAL",
    "**Authorization**: Bearer SYNTHETIC_CREDENTIAL",
    "Authorization\u00a0:\u00a0Bearer SYNTHETIC_CREDENTIAL",
    'headers["Authorization"] = "Bearer SYNTHETIC_CREDENTIAL"',
    'headers.set("Authorization", "Bearer SYNTHETIC_CREDENTIAL")',
    "| Authorization | Bearer SYNTHETIC_CREDENTIAL |",
    "__Authorization__: Bearer SYNTHETIC_CREDENTIAL",
    "<Authorization>Bearer SYNTHETIC_CREDENTIAL</Authorization>",
])
def test_complete_authorization_record_is_removed_before_excerpt_truncation(credential):
    report = f"Changed files: src/example.py\n{credential}\nVerification: synthetic checks passed.\n"

    redacted = sanitize_artifact_excerpt(report)

    assert "SYNTHETIC_CREDENTIAL" not in redacted
    assert "SYNTHETIC_RESPONSE" not in redacted
    assert "[redacted]" in redacted
    assert "Changed files: src/example.py" in redacted
    assert "Verification: synthetic checks passed." in redacted
    assert sanitize_artifact_excerpt(report, max_chars=70) == redacted[:70]


def test_artifact_sanitizer_preserves_ordinary_report_and_existing_secret_assignments():
    ordinary = "# Worker report\nAuthorization boundaries need review.\n- `pytest tests` passed.\n"
    assert sanitize_artifact_excerpt(ordinary) == ordinary
    assert sanitize_artifact_excerpt("API_KEY=synthetic-key") == "API_KEY: [redacted]"


@pytest.mark.parametrize("artifact_key", ["implementation_handoff", "qa_report"])
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_worker_credentials_are_absent_from_artifact_and_docs_mirror(tmp_path, artifact_key, status):
    project = tmp_path / "product"
    project.mkdir()
    config = tmp_path / "project.local.yaml"
    config.write_text(f"name: Synthetic Product\nkey: SYNTHETIC\nrepos:\n  root: {project}\n")
    report = tmp_path / "worker-result.md"
    # The sanitizer matrix above owns credential syntax; this test protects
    # its use in both artifact writers and their completed/failed paths.
    report_text = (
        "# Worker report\nChanged src/example.py.\n"
        'headers.set("Authorization", "Bearer SYNTHETIC_REPORT_CREDENTIAL")\n'
        "Verification: synthetic checks passed.\n"
    )
    report.write_text(report_text)
    result = {
        "role": "implementation", "adapter": "synthetic", "status": status,
        "summary": "Authorization: Bearer SYNTHETIC_SUMMARY_CREDENTIAL",
        "result_path": str(report),
        "changed_files": ["src/example.py", '"Authorization": "Basic SYNTHETIC_FILE_CREDENTIAL"', "src/later.py"],
        "artifacts": ["Authorization: Digest response=SYNTHETIC_ARTIFACT_CREDENTIAL", "evidence/later.txt"],
    }
    original = {**result, "changed_files": list(result["changed_files"]), "artifacts": list(result["artifacts"])}
    state = create_initial_state("run-synthetic", "SYNTHETIC", "Validate synthetic evidence")

    content = render_downstream_artifact_markdown(artifact_key, state, config, worker_results=[result])
    artifact = ArtifactStore(tmp_path / "workspace", mirror_root=project).write_markdown(
        state.run_id, "delivery", f"{artifact_key}.md", content,
    )

    assert not any(marker in content for marker in (
        "SYNTHETIC_REPORT_CREDENTIAL", "SYNTHETIC_SUMMARY_CREDENTIAL",
        "SYNTHETIC_FILE_CREDENTIAL", "SYNTHETIC_ARTIFACT_CREDENTIAL",
    ))
    assert "Changed src/example.py." in content
    assert "Verification: synthetic checks passed." in content
    assert "src/later.py" in content and "evidence/later.txt" in content
    assert f"Observed status: `{status}`" in content
    assert artifact.read_text() == (project / "docs" / f"{artifact_key}.md").read_text() == content
    assert report.read_text() == report_text
    assert result == original


def test_direct_fix_profile_evidence_formatter_redacts_metadata_without_report():
    lines = format_worker_evidence_lines([{
        "role": "qa", "status": "failed", "summary": "Authorization: Bearer SYNTHETIC_FIX_CREDENTIAL",
    }])
    assert "SYNTHETIC_FIX_CREDENTIAL" not in "\n".join(lines)
    assert "qa: `failed`" in "\n".join(lines)


def test_redaction_preserves_indented_verification_after_header_record_boundary():
    report = "Authorization: Bearer SYNTHETIC_CREDENTIAL\n\n  - `pytest tests` passed.\n"
    assert "SYNTHETIC_CREDENTIAL" not in sanitize_artifact_excerpt(report)
    assert "  - `pytest tests` passed." in sanitize_artifact_excerpt(report)


def test_downstream_artifact_keeps_legitimate_content_beyond_excerpt_limit(tmp_path):
    report = tmp_path / "result.md"
    report.write_text("Synthetic report completed.\n")
    state = create_initial_state("run-synthetic", "SYNTHETIC", "A" * 7000)
    content = render_downstream_artifact_markdown("implementation_handoff", state, worker_results=[{
        "role": "implementation", "adapter": "synthetic", "status": "completed", "result_path": str(report),
    }])
    assert len(content) > 6000
    assert "Synthetic report completed." in content
