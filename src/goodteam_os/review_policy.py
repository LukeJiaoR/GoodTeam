from collections.abc import Iterable, Mapping


POLICY_STATES = (
    "ok",
    "missing_evidence",
    "fallback_evidence",
    "non_provider_evidence",
    "failed_invocation",
)
POLICY_MODES = ("advisory", "strict")
HUMAN_AUTHORITY_MESSAGE = "Human operator decides."


def normalize_review_evidence_policy_config(config: object) -> dict[str, object]:
    if config in (None, ""):
        return {"mode": "advisory", "blockers": ()}
    if not isinstance(config, Mapping):
        raise ValueError("review_evidence_policy must be a mapping.")

    mode = _text(config.get("mode"), "advisory").lower()
    if mode not in POLICY_MODES:
        modes = ", ".join(POLICY_MODES)
        raise ValueError(f"review_evidence_policy.mode must be one of: {modes}.")

    blockers = _coerce_blocker_states(config.get("blockers"))
    if blockers and mode != "strict":
        raise ValueError("review_evidence_policy.blockers requires mode: strict.")
    return {"mode": mode, "blockers": blockers}


def evaluate_review_evidence_policy(
    reviewer_provenance: Mapping[str, object] | None,
    *,
    gate: str,
    config: object = None,
) -> dict[str, object]:
    policy_config = normalize_review_evidence_policy_config(config)
    provenance = dict(reviewer_provenance or {})
    badge = _mapping(provenance.get("badge"))
    invocation = _mapping(provenance.get("invocation"))
    evidence = _evidence_payload(badge, invocation)
    state = _state_for_evidence(evidence)
    severity = _severity_for_state(state, policy_config)
    return {
        "state": state,
        "severity": severity,
        "gate": str(gate),
        "rationale": _rationale(state=state, severity=severity, gate=str(gate), mode=str(policy_config["mode"])),
        "visible_to_operator": True,
        "advisory_only": True,
        "can_human_override": True,
        "evidence": evidence,
    }


def _coerce_blocker_states(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        if value.strip() == "[]":
            return ()
        raw_states = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, Mapping) or not isinstance(value, Iterable):
        raise ValueError("review_evidence_policy.blockers must be a list or comma-separated string.")
    else:
        raw_states = [str(item).strip() for item in value if str(item).strip()]

    states: list[str] = []
    for state in raw_states:
        if state not in POLICY_STATES or state == "ok":
            allowed = ", ".join(item for item in POLICY_STATES if item != "ok")
            raise ValueError(f"review_evidence_policy.blockers contains unknown state {state!r}. Use one of: {allowed}.")
        if state not in states:
            states.append(state)
    return tuple(states)


def _state_for_evidence(evidence: Mapping[str, object]) -> str:
    badge_kind = str(evidence.get("badge_kind", "") or "").lower()
    invocation_status = str(evidence.get("invocation_status", "") or "").lower()
    if badge_kind == "failed" or invocation_status == "failed":
        return "failed_invocation"
    if badge_kind == "missing":
        return "missing_evidence"
    if badge_kind == "fallback":
        return "fallback_evidence"
    if badge_kind in {"builtin", "manual"}:
        return "non_provider_evidence"
    if badge_kind == "provider" and invocation_status == "completed":
        return "ok"
    if badge_kind == "provider":
        return "fallback_evidence"
    return "fallback_evidence"


def _severity_for_state(state: str, config: Mapping[str, object]) -> str:
    if state == "ok":
        return "ok"
    blockers = set(config.get("blockers", ()))
    if config.get("mode") == "strict" and state in blockers:
        return "blocker"
    return "warning"


def _rationale(*, state: str, severity: str, gate: str, mode: str) -> str:
    base = {
        "ok": f"Provider-backed reviewer evidence completed for {gate}.",
        "missing_evidence": f"Reviewer invocation evidence is missing for {gate}; verify review quality manually.",
        "fallback_evidence": f"Reviewer evidence for {gate} came from fallback or incomplete provider evidence.",
        "non_provider_evidence": f"Reviewer evidence for {gate} came from builtin or manual non-provider evidence.",
        "failed_invocation": f"Reviewer provider invocation failed for {gate}; inspect failure evidence before approving.",
    }.get(state, f"Reviewer evidence quality for {gate} needs operator review.")
    if severity == "blocker":
        return (
            f"{base} Configured strict review_evidence_policy marks {state} as a blocker. "
            f"{HUMAN_AUTHORITY_MESSAGE} Human override remains available."
        )
    if mode == "strict":
        return f"{base} Strict review_evidence_policy did not mark {state} as a blocker. {HUMAN_AUTHORITY_MESSAGE}"
    return f"{base} Default policy is advisory and does not block approvals. {HUMAN_AUTHORITY_MESSAGE}"


def _evidence_payload(badge: Mapping[str, object], invocation: Mapping[str, object]) -> dict[str, object]:
    return {
        "badge_kind": _text(badge.get("kind"), "missing").lower(),
        "adapter": _text(badge.get("adapter"), "unknown"),
        "provider": _text(badge.get("provider"), "unknown"),
        "external_invocation": _bool(invocation.get("external_invocation")),
        "command_available": _nullable_bool(invocation.get("command_available")),
        "invocation_status": _text(invocation.get("status"), "missing"),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _nullable_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
