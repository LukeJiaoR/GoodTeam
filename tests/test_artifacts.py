from goodteam_os.artifacts import ArtifactStore


def test_artifact_store_writes_run_file(tmp_path):
    store = ArtifactStore(root=tmp_path)
    path = store.write_markdown("run-1", "prd", "prd.md", "# PRD\n")

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# PRD\n"
    assert str(path).endswith("artifacts/runs/run-1/prd/prd.md")


def test_artifact_store_writes_sibling_proposed_file(tmp_path):
    store = ArtifactStore(root=tmp_path)
    path = store.write_proposed_markdown("run-1", "prd", "prd.md", "# Proposed PRD\n")

    assert path.read_text(encoding="utf-8") == "# Proposed PRD\n"
    assert str(path).endswith("artifacts/runs/run-1/prd/prd.proposed.md")
