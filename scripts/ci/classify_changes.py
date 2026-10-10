from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence


_MODE_RANK = {"none": 0, "focused": 1, "full": 2}
_SAFE_TEST_PATH = re.compile(r"^tests/test_[A-Za-z0-9_]+\.py$")

_DOC_PATHS = {
    ".github/pull_request_template.md",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
}

_TIER2_BACKEND_TARGETS = {
    "src/goodteam_os/artifacts.py": ("tests/test_artifacts.py",),
    "src/goodteam_os/cli.py": (
        "tests/test_cli.py",
        "tests/test_cli_doctor.py",
        "tests/test_init.py",
    ),
    "src/goodteam_os/context.py": ("tests/test_context.py",),
    "src/goodteam_os/cross_project_memory.py": ("tests/test_cross_project_memory.py",),
    "src/goodteam_os/failure_patterns.py": ("tests/test_failure_patterns.py",),
    "src/goodteam_os/frontend_assets.py": ("tests/test_frontend_assets.py",),
    "src/goodteam_os/memory.py": ("tests/test_memory.py",),
    "src/goodteam_os/messages.py": ("tests/test_messages.py",),
    "src/goodteam_os/projects.py": ("tests/test_projects.py",),
    "src/goodteam_os/resources.py": ("tests/test_resources.py",),
    "src/goodteam_os/role_prompts.json": ("tests/test_roles.py",),
    "src/goodteam_os/roles.py": ("tests/test_roles.py",),
    "src/goodteam_os/tasks.py": ("tests/test_tasks.py",),
}

_TIER3_BACKEND_FILES = {
    "src/goodteam_os/debate.py",
    "src/goodteam_os/dispatch.py",
    "src/goodteam_os/graph.py",
    "src/goodteam_os/models.py",
    "src/goodteam_os/multi_reviewer_projection.py",
    "src/goodteam_os/operator.py",
    "src/goodteam_os/operator_github.py",
    "src/goodteam_os/operator_queue.py",
    "src/goodteam_os/project_reviewer_profiles.py",
    "src/goodteam_os/review_policy.py",
    "src/goodteam_os/review_provenance.py",
    "src/goodteam_os/reviewer_dispatch.py",
    "src/goodteam_os/reviewer_evidence_snapshot.py",
    "src/goodteam_os/reviewer_profiles.py",
    "src/goodteam_os/service.py",
    "src/goodteam_os/session_runtimes.py",
    "src/goodteam_os/staging.py",
    "src/goodteam_os/state.py",
    "src/goodteam_os/supervisor.py",
    "src/goodteam_os/workers.py",
    "src/goodteam_os/workflow_table.py",
}

_TIER4_BACKEND_FILES = {
    "src/goodteam_os/full_smoke.py",
    "src/goodteam_os/native_picker.py",
    "src/goodteam_os/pi_rpc.py",
    "src/goodteam_os/project_import.py",
    "src/goodteam_os/project_readiness.py",
    "src/goodteam_os/project_readiness_executor.py",
    "src/goodteam_os/smoke.py",
    "src/goodteam_os/telegram.py",
    "src/goodteam_os/telegram_runtime_controls.py",
    "src/goodteam_os/web.py",
}

_TIER3_TEST_PREFIXES = (
    "tests/test_agent_sandbox",
    "tests/test_api",
    "tests/test_debate",
    "tests/test_execution",
    "tests/test_fix_approval",
    "tests/test_graph",
    "tests/test_land_provenance",
    "tests/test_models",
    "tests/test_multi_reviewer",
    "tests/test_operator",
    "tests/test_pre_review",
    "tests/test_review",
    "tests/test_reviewer",
    "tests/test_service",
    "tests/test_session",
    "tests/test_staging",
    "tests/test_state",
    "tests/test_supervisor",
    "tests/test_web",
    "tests/test_workers",
    "tests/test_workflow",
)

_TIER4_TEST_PREFIXES = (
    "tests/test_full_smoke",
    "tests/test_native_picker",
    "tests/test_project_import",
    "tests/test_project_readiness",
    "tests/test_smoke",
    "tests/test_telegram",
)


@dataclass(frozen=True)
class Change:
    status: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    tier: int
    backend: str
    frontend: str
    backend_test_targets: tuple[str, ...] = ()
    live_smoke_evidence_required: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def browser_required(self) -> bool:
        return self.tier >= 3 or self.backend == "full" or self.live_smoke_evidence_required

    def as_dict(self) -> dict[str, object]:
        return {**asdict(self), "browser_required": self.browser_required}


def _plan(
    tier: int,
    backend: str,
    frontend: str,
    path: str,
    reason: str,
    *,
    backend_targets: tuple[str, ...] = (),
    smoke: bool = False,
) -> Plan:
    return Plan(
        tier=tier,
        backend=backend,
        frontend=frontend,
        backend_test_targets=backend_targets,
        live_smoke_evidence_required=smoke,
        reasons=(f"{path}: {reason}",),
    )


def _is_root_markdown(path: str) -> bool:
    pure_path = PurePosixPath(path)
    return len(pure_path.parts) == 1 and pure_path.suffix.lower() == ".md"


def _decision_for_test(path: str) -> Plan:
    if path.startswith("tests/test_ci_"):
        return _plan(3, "full", "full", path, "CI authority contract")
    if path.startswith(_TIER4_TEST_PREFIXES):
        return _plan(4, "full", "none", path, "live-path contract", smoke=True)
    if path.startswith(_TIER3_TEST_PREFIXES):
        return _plan(3, "full", "none", path, "control-plane contract")
    if _SAFE_TEST_PATH.fullmatch(path):
        return _plan(
            1,
            "focused",
            "none",
            path,
            "bounded backend test",
            backend_targets=(path,),
        )
    return _plan(3, "full", "full", path, "unmapped test path; fail closed")


def _decision_for_frontend(path: str) -> Plan:
    if path in {
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/playwright.config.js",
        "frontend/vite.config.js",
    }:
        return _plan(3, "none", "full", path, "frontend build authority")

    if path in {
        "frontend/src/App.css",
        "frontend/src/App.jsx",
        "frontend/src/index.css",
        "frontend/src/main.jsx",
    } or path.startswith(("frontend/e2e/", "frontend/src/app/", "frontend/src/components/", "frontend/src/features/", "frontend/src/hooks/", "frontend/src/styles/")):
        return _plan(
            4,
            "focused",
            "full",
            path,
            "browser-visible workflow",
            backend_targets=("tests/test_web.py",),
            smoke=True,
        )

    if path.startswith(("frontend/src/constants/", "frontend/src/i18n/", "frontend/src/lib/", "frontend/src/utils/")):
        return _plan(
            2,
            "none",
            "full",
            path,
            "frontend helper contract",
        )

    if path.startswith(("frontend/public/", "frontend/src/assets/")):
        return _plan(2, "none", "full", path, "frontend asset")

    if path.startswith("frontend/"):
        return _plan(3, "none", "full", path, "unmapped frontend build input")

    raise AssertionError(f"not a frontend path: {path}")


def _decision_for_path(path: str) -> Plan:
    if path.startswith(("docs/", "openwiki/")) or path in _DOC_PATHS or _is_root_markdown(path):
        return _plan(0, "none", "none", path, "non-executable documentation")

    if path.startswith((".github/workflows/", "scripts/ci/")):
        return _plan(3, "full", "full", path, "CI authority")

    if path.startswith("scripts/"):
        return _plan(3, "full", "full", path, "build or release authority")

    if path == "pyproject.toml":
        return _plan(3, "full", "none", path, "backend dependency authority")

    if path.startswith("projects/"):
        return _plan(3, "full", "none", path, "project configuration contract")

    if path.startswith("tests/"):
        return _decision_for_test(path)

    if path.startswith("frontend/"):
        return _decision_for_frontend(path)

    if path.startswith(("src/goodteam_os/execution/", "src/goodteam_os/nodes/")):
        return _plan(3, "full", "none", path, "control-plane core")

    if path in _TIER4_BACKEND_FILES:
        return _plan(4, "full", "none", path, "live or adapter path", smoke=True)

    if path in _TIER3_BACKEND_FILES:
        return _plan(3, "full", "none", path, "control-plane contract")

    if path in _TIER2_BACKEND_TARGETS:
        return _plan(
            2,
            "focused",
            "none",
            path,
            "ordinary backend contract",
            backend_targets=_TIER2_BACKEND_TARGETS[path],
        )

    return _plan(3, "full", "full", path, "unknown path; fail closed")


def classify_changes(changes: Sequence[Change]) -> Plan:
    if not changes:
        return Plan(3, "full", "full", reasons=("empty changed-file set; fail closed",))

    decisions = [_decision_for_path(path) for change in changes for path in change.paths]
    return Plan(
        tier=max(decision.tier for decision in decisions),
        backend=max((decision.backend for decision in decisions), key=_MODE_RANK.__getitem__),
        frontend=max((decision.frontend for decision in decisions), key=_MODE_RANK.__getitem__),
        backend_test_targets=tuple(
            sorted({target for decision in decisions for target in decision.backend_test_targets})
        ),
        live_smoke_evidence_required=any(
            decision.live_smoke_evidence_required for decision in decisions
        ),
        reasons=tuple(reason for decision in decisions for reason in decision.reasons),
    )


def parse_name_status(data: bytes) -> tuple[Change, ...]:
    fields = data.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()

    changes = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        if index + path_count > len(fields):
            raise ValueError("incomplete git name-status record")
        paths = tuple(field.decode("utf-8") for field in fields[index : index + path_count])
        changes.append(Change(status=status, paths=paths))
        index += path_count
    return tuple(changes)


def changes_between(base: str, head: str) -> tuple[Change, ...]:
    result = subprocess.run(
        ["git", "diff", "--name-status", "-z", f"{base}...{head}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return parse_name_status(result.stdout)


def full_plan(reason: str = "manual full verification") -> Plan:
    return Plan(3, "full", "full", reasons=(reason,))


def write_github_outputs(path: Path, plan: Plan) -> None:
    values = {
        "tier": str(plan.tier),
        "backend": plan.backend,
        "frontend": plan.frontend,
        "browser_required": str(plan.browser_required).lower(),
        "backend_test_targets": " ".join(plan.backend_test_targets),
        "live_smoke_evidence_required": str(plan.live_smoke_evidence_required).lower(),
        "plan_json": json.dumps(plan.as_dict(), separators=(",", ":"), sort_keys=True),
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def append_summary(path: Path, plan: Plan) -> None:
    reasons = [reason.replace("\n", "\\n") for reason in plan.reasons]
    rows = [
        "## CI change classification",
        "",
        f"- Tier: **{plan.tier}**",
        f"- Backend: `{plan.backend}`",
        f"- Frontend: `{plan.frontend}`",
        f"- Browser acceptance required: `{str(plan.browser_required).lower()}`",
        "- Live smoke evidence required: "
        f"`{str(plan.live_smoke_evidence_required).lower()}`",
        "",
        "### Reasons",
        *[f"- {reason}" for reason in reasons],
        "",
    ]
    with path.open("a", encoding="utf-8") as summary:
        summary.write("\n".join(rows))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify GoodTeam CI changes")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--full", action="store_true")
    source.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.full:
        plan = full_plan()
    else:
        if not args.head:
            parser.error("--head is required with --base")
        try:
            plan = classify_changes(changes_between(args.base, args.head))
        except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
            plan = Plan(
                3,
                "full",
                "full",
                reasons=(f"git diff failed; fail closed ({type(error).__name__})",),
            )

    print(json.dumps(plan.as_dict(), sort_keys=True))
    if args.github_output:
        write_github_outputs(args.github_output, plan)
    if args.summary:
        append_summary(args.summary, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
