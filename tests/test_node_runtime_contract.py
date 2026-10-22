"""Keep frontend and manual documentation builds on patched Node LTS lines."""

import json
from pathlib import Path

import yaml


def test_frontend_node_security_floor_matches_lockfile_and_ci():
    version = Path(".nvmrc").read_text().strip()
    assert version == "24.22.1"
    package = json.loads(Path("frontend/package.json").read_text())
    lock = json.loads(Path("frontend/package-lock.json").read_text())
    assert package["engines"] == {"node": "^22.23.4 || ^24.22.1"}
    assert lock["packages"][""]["engines"] == package["engines"]
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    setup_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/setup-node@")
    ]
    assert setup_steps
    for step in setup_steps:
        assert step["with"]["node-version-file"] == ".nvmrc"
        assert "node-version" not in step["with"]


def test_openwiki_preserves_node_22_with_october_security_patch():
    workflow = yaml.safe_load(Path(".github/workflows/openwiki-update.yml").read_text())
    setup_steps = [
        step for step in workflow["jobs"]["update"]["steps"]
        if step.get("uses", "").startswith("actions/setup-node@")
    ]
    assert len(setup_steps) == 1
    assert setup_steps[0]["with"]["node-version"] == "22.23.4"
