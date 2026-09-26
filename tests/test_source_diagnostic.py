import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

import yaml


WORKFLOW = Path(".github/workflows/source-diagnostic.yml")


def test_source_diagnostic_uses_reviewed_workflow_and_uncredentialed_checkout():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    job = workflow["jobs"]["capture"]
    assert job["if"] == "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
    checkout = job["steps"][0]["with"]
    assert checkout["persist-credentials"] == "false"
    assert checkout["ref"] == "${{ inputs.source_ref || github.sha }}"


def test_source_diagnostic_exports_only_committed_source_without_execution(tmp_path):
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    script = next(step["run"] for step in workflow["jobs"]["capture"]["steps"] if step.get("id") == "capture")
    repository = tmp_path / "repository"
    repository.mkdir()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    output_file = tmp_path / "github-output"

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repository, text=True).strip()

    git("init", "-q")
    (repository / "README.md").write_text("committed source\n", encoding="utf-8")
    (repository / "do-not-run.sh").write_text("touch executed\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=CI test", "-c", "user.email=ci@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "source fixture")
    expected_head = git("rev-parse", "HEAD")
    expected_tree = git("rev-parse", "HEAD^{tree}")
    (repository / "README.md").write_text("uncommitted change\n", encoding="utf-8")
    (repository / "private.local").write_text("untracked data\n", encoding="utf-8")
    (runner_temp / "other-job.txt").write_text("keep\n", encoding="utf-8")

    outputs = []
    for _ in range(2):
        subprocess.run(
            ["bash", "-e", "-c", script], cwd=repository, check=True, timeout=10,
            env={**os.environ, "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output_file)},
        )
        output = Path(output_file.read_text().splitlines()[-1].removeprefix("path="))
        outputs.append(output)
        assert output.parent == runner_temp
        archive = output / "tracked-source.tar"
        with tarfile.open(archive) as source:
            assert set(source.getnames()) == {"README.md", "do-not-run.sh"}
            assert source.extractfile("README.md").read() == b"committed source\n"
        assert (output / "head.txt").read_text().strip() == expected_head
        assert (output / "tree.txt").read_text().strip() == expected_tree
        assert "README.md" in (output / "manifest.txt").read_text()
        assert (output / "sha256.txt").read_text() == (
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  tracked-source.tar\n"
        )
    assert outputs[0] != outputs[1]
    assert not (repository / "executed").exists()
    assert (runner_temp / "other-job.txt").read_text() == "keep\n"
