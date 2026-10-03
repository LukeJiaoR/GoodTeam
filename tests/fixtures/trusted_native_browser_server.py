"""Owned model-free server for the real page -> native-shaped child -> report.

No product selector installs this fixture; it cannot read a real native home.
The ordinary default gate producer and guarded page Run API perform the work.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import pytest

from goodteam_os import web
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.projects import write_project_config
from goodteam_os.skills.task_service import ProjectTaskSkillService
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.workers import WorkerJobStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_codex_trusted import FixtureTrustedAdmission, default_page, native, policy


def main():
    owned = Path(os.environ["GOODTEAM_E2E_ROOT"]).resolve()
    if (not owned.name.startswith("goodteam-e2e-") or owned.parent != Path(tempfile.gettempdir()).resolve()
            or (owned / ".owner").read_text() != os.environ["GOODTEAM_E2E_OWNER"]):
        raise RuntimeError("Native-shaped browser fixture requires the owned isolated E2E root")
    root = owned / "trusted-native-fixture"
    root.mkdir()
    workspace, repo = root / "workspace", root / "repo"
    repo.mkdir()
    os.environ.update(GOODTEAM_HOME=str(root / "home"), GOODTEAM_STATE_DIR=str(root / "operator-state"),
                      GOODTEAM_PROJECTS_REGISTRY=str(root / "home" / "projects.json"), GOODTEAM_DISABLE_TG_NETWORK="1")
    config = write_project_config(workspace / "projects", "DEMO", "Native fixture", repo_path=str(repo), worker_mode="fake")
    ProjectRegistry().register_project(config, root=repo, key="DEMO")
    ensure_team_state(workspace, "DEMO", config)
    ctx = SimpleNamespace(root=root, workspace=workspace, repo=repo, config=config,
        service=ProjectTaskSkillService(workspace, "DEMO", worker_roots=(repo,)), workers=WorkerJobStore(repo))
    patch = pytest.MonkeyPatch()
    state, scope = default_page(ctx, patch)
    authority = FixtureTrustedAdmission(policy(ctx, native(ctx), scope))
    original = web.ThreadingHTTPServer

    class Announce(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            print(json.dumps({"url": "http://127.0.0.1:" + str(self.server_port), "run_id": state.run_id,
                              "real_model_calls": 0, "native_codex": False, "verification": "contract_fixture"}), flush=True)

    patch.setattr(web, "ThreadingHTTPServer", Announce)
    web.serve(workspace=workspace, host="127.0.0.1", port=0, frontend_dist=Path(sys.argv[1]).resolve(),
              codex_review_admission=authority)


if __name__ == "__main__":
    main()
