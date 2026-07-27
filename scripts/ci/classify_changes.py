from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Sequence


_MODE_RANK = {"none": 0, "focused": 1, "full": 2}


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
    frontend_test_targets: tuple[str, ...] = ()
    live_smoke_evidence_required: bool = False
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _decision_for_path(path: str) -> Plan:
    pure_path = PurePosixPath(path)

    if (
        path.startswith(("docs/", "openwiki/"))
        or (len(pure_path.parts) == 1 and pure_path.suffix.lower() == ".md")
        or path == ".github/pull_request_template.md"
    ):
        return Plan(0, "none", "none", reasons=(f"{path}: non-executable documentation",))

    if path.startswith("src/goodteam_os/execution/"):
        return Plan(3, "full", "none", reasons=(f"{path}: control-plane core",))

    if path.startswith(("frontend/src/components/", "frontend/src/app/", "frontend/src/features/")):
        return Plan(
            4,
            "none",
            "full",
            live_smoke_evidence_required=True,
            reasons=(f"{path}: browser-visible workflow",),
        )

    return Plan(3, "full", "full", reasons=(f"{path}: unknown path; fail closed",))


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
        frontend_test_targets=tuple(
            sorted({target for decision in decisions for target in decision.frontend_test_targets})
        ),
        live_smoke_evidence_required=any(
            decision.live_smoke_evidence_required for decision in decisions
        ),
        reasons=tuple(reason for decision in decisions for reason in decision.reasons),
    )
