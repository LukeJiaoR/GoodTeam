"""One smoke check for focused CI classifier paths after test consolidation."""

import importlib.util
import sys
from pathlib import Path


def test_changed_feature_targets_still_exist_after_pruning():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_goodteam_lean_ci_classifier", root / "scripts" / "ci" / "classify_changes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plan = module.classify_changes((
        module.Change("M", ("src/goodteam_os/project_iteration.py",)),
        module.Change("M", ("frontend/src/features/runs/WorkPage.jsx",)),
    ))
    assert "tests/test_project_iteration.py" in plan.backend_test_targets
    assert "tests/test_web_cors.py" in plan.backend_test_targets
    assert all((root / name).is_file() for name in plan.backend_test_targets)
