from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


REVIEWER_PROFILE_EVIDENCE_KEYS = (
    "invocation_evidence",
    "result_path",
    "stdout_path",
    "stderr_path",
    "changed_files",
    "verification_summary",
)


@dataclass(frozen=True)
class ReviewerProfile:
    profile_id: str
    label: str
    adapter: str
    model: str | None
    focus_areas: tuple[str, ...]
    required_report_sections: tuple[str, ...]
    required_evidence: tuple[str, ...]
    fallback_policy: str
    timeout_seconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "adapter": self.adapter,
            "model": self.model,
            "focus_areas": list(self.focus_areas),
            "required_report_sections": list(self.required_report_sections),
            "required_evidence": list(self.required_evidence),
            "fallback_policy": self.fallback_policy,
            "timeout_seconds": self.timeout_seconds,
        }


_BUILTIN_REVIEWER_PROFILES: dict[str, ReviewerProfile] = {
    "correctness-security": ReviewerProfile(
        profile_id="correctness-security",
        label="Correctness and security",
        adapter="codex",
        model=None,
        focus_areas=(
            "Behavioral correctness",
            "Security regressions",
            "Data handling and input validation",
            "Test coverage for risky paths",
        ),
        required_report_sections=(
            "Summary",
            "Findings",
            "Evidence",
            "Risk assessment",
            "Recommendation",
        ),
        required_evidence=REVIEWER_PROFILE_EVIDENCE_KEYS,
        fallback_policy="manual_review_required_on_missing_evidence",
        timeout_seconds=1800,
    ),
    "architecture-maintainability": ReviewerProfile(
        profile_id="architecture-maintainability",
        label="Architecture and maintainability",
        adapter="claude",
        model=None,
        focus_areas=(
            "Module boundaries",
            "API and contract stability",
            "Long-term maintainability",
            "Complexity and coupling",
        ),
        required_report_sections=(
            "Summary",
            "Architecture findings",
            "Maintainability risks",
            "Evidence",
            "Recommendation",
        ),
        required_evidence=REVIEWER_PROFILE_EVIDENCE_KEYS,
        fallback_policy="warn_on_unavailable",
        timeout_seconds=1800,
    ),
    "product-operator": ReviewerProfile(
        profile_id="product-operator",
        label="Product and operator review",
        adapter="gemini",
        model=None,
        focus_areas=(
            "User workflow impact",
            "Operator ergonomics",
            "Release readiness",
            "Documentation and handoff clarity",
        ),
        required_report_sections=(
            "Summary",
            "Operator impact",
            "Product risks",
            "Evidence",
            "Recommendation",
        ),
        required_evidence=REVIEWER_PROFILE_EVIDENCE_KEYS,
        fallback_policy="allow_fake_only_in_tests",
        timeout_seconds=1800,
    ),
}

BUILTIN_REVIEWER_PROFILES: Mapping[str, ReviewerProfile] = MappingProxyType(_BUILTIN_REVIEWER_PROFILES)


def reviewer_profile_ids() -> tuple[str, ...]:
    return tuple(BUILTIN_REVIEWER_PROFILES)


def list_reviewer_profiles() -> list[ReviewerProfile]:
    return list(BUILTIN_REVIEWER_PROFILES.values())


def get_reviewer_profile(profile_id: str) -> ReviewerProfile:
    try:
        return BUILTIN_REVIEWER_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown reviewer profile {profile_id!r}") from exc


def validate_reviewer_profile_ids(profile_ids: Iterable[str]) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for profile_id in profile_ids:
        get_reviewer_profile(profile_id)
        if profile_id in seen:
            continue
        selected.append(profile_id)
        seen.add(profile_id)
    return tuple(selected)
