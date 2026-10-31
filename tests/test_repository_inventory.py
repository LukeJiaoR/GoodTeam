from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from goodteam_os import repository_inventory
from goodteam_os.repository_access import RepositoryEntry, RepositoryAccessError
from goodteam_os.repository_inventory import build_repository_inventory
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    RepositorySnapshotError,
    capture_repository_snapshot,
    compute_repository_snapshot_id,
)


def test_inventory_is_snapshot_bound_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "build": "vite build"}}),
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "main.ts").write_text(
        "export const main = true;\n",
        encoding="utf-8",
    )
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    first = build_repository_inventory(root, snapshot=snapshot)
    second = build_repository_inventory(root, snapshot=snapshot)

    assert first["schema"] == "repository_inventory.v1"
    assert first["snapshot_id"] == snapshot.snapshot_id
    assert first["inventory_digest"] == second["inventory_digest"]
    assert {item["command"] for item in first["commands"]} == {
        "npm run build",
        "npm run test",
    }
    assert all(item["classification"] == "heuristic" for item in first["commands"])
    assert "src/main.ts" in first["entry_points"]
    assert first["entrypoint_candidates"] == [
        {
            "path": "src/main.ts",
            "basis": "conventional_entrypoint_filename",
            "classification": "heuristic",
        }
    ]


def test_inventory_rejects_snapshot_drift(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "main.py"
    source.write_text("print('first')\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    source.write_text("print('changed')\n", encoding="utf-8")

    with pytest.raises(RepositorySnapshotError) as captured:
        build_repository_inventory(root, snapshot=snapshot)

    assert captured.value.code == "repository_snapshot_changed"

