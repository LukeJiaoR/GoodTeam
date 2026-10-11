from collections.abc import Callable, Iterable, Mapping

from goodteam_os.reviewer_profiles import ReviewerProfile, get_reviewer_profile, validate_reviewer_profile_ids


REVIEWER_PROFILE_PHASES = ("implementation", "release")
BackendDoctor = Callable[[str], Mapping[str, object]]


def normalize_reviewer_profile_config(target: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    raw_config = target.get("reviewer_profiles", {})
    if raw_config in (None, ""):
        return {}
    if not isinstance(raw_config, Mapping):
        raise ValueError("reviewer_profiles must be a mapping of phase to reviewer profile ids.")

    configured: dict[str, tuple[str, ...]] = {}
    for raw_phase, raw_profile_ids in raw_config.items():
        phase = str(raw_phase).strip()
        if phase not in REVIEWER_PROFILE_PHASES:
            phases = ", ".join(REVIEWER_PROFILE_PHASES)
            raise ValueError(f"Unknown reviewer profile phase {phase!r}. Use one of: {phases}.")
        configured[phase] = validate_reviewer_profile_ids(_coerce_profile_ids(phase, raw_profile_ids))
    return configured


def reviewer_profile_configuration_error(target: Mapping[str, object] | None) -> str:
    """Validate evidence coverage without changing legacy execution normalization."""
    if target is None:
        return ""
    if target.get("_reviewer_configuration_unavailable"):
        return "Project reviewer configuration could not be read. Repair project reviewer settings before relying on approval advice."
    if "reviewer_profiles" not in target:
        return ""
    invalid = "Reviewer profile configuration is invalid. Repair project reviewer settings before relying on approval advice."
    raw = target["reviewer_profiles"]
    if not isinstance(raw, Mapping):
        return invalid
    try:
        phases = [str(phase).strip() for phase in raw]
        if len(set(phases)) != len(phases):
            return invalid
        for phase, value in raw.items():
            if not isinstance(value, (str, list, tuple)):
                return invalid
            if isinstance(value, (list, tuple)) and any(not isinstance(item, str) for item in value):
                return invalid
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("[") and text.endswith("]"):
                    text = text[1:-1].strip()
                if text and any(not part.strip() for part in text.split(",")):
                    return invalid
            ids = _coerce_profile_ids(str(phase), value)
            if len(set(ids)) != len(ids):
                return "Reviewer profile configuration contains duplicate profiles. Repair project reviewer settings before relying on approval advice."
        normalize_reviewer_profile_config(target)
    except ValueError:
        return invalid
    return ""


def reviewer_profiles_report(target: Mapping[str, object], *, backend_doctor: BackendDoctor) -> dict[str, object]:
    try:
        configured = normalize_reviewer_profile_config(target)
    except ValueError as exc:
        return {"configured": {}, "profiles": [], "errors": [str(exc)], "warnings": []}

    profiles: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    doctor_cache: dict[str, Mapping[str, object]] = {}
    for phase, profile_ids in configured.items():
        for profile_id in profile_ids:
            profile = get_reviewer_profile(profile_id)
            doctor_report = doctor_cache.setdefault(profile.adapter, backend_doctor(profile.adapter))
            entry = _profile_entry(phase, profile, doctor_report)
            profiles.append(entry)
            if entry["ready"] is False:
                warnings.append(_profile_warning(entry))

    return {
        "configured": {phase: list(profile_ids) for phase, profile_ids in configured.items()},
        "profiles": profiles,
        "errors": [],
        "warnings": warnings,
    }


def _coerce_profile_ids(phase: str, value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return _split_profile_id_string(value)
    if isinstance(value, Mapping):
        if not value:
            return ()
        raise ValueError(f"reviewer_profiles.{phase} must be a list or comma-separated string.")
    if isinstance(value, Iterable):
        return tuple(_clean_profile_id(phase, item) for item in value)
    raise ValueError(f"reviewer_profiles.{phase} must be a list or comma-separated string.")


def _split_profile_id_string(value: str) -> tuple[str, ...]:
    cleaned = value.strip()
    if not cleaned or cleaned == "[]":
        return ()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
        if not cleaned:
            return ()
    return tuple(_strip_optional_quotes(part.strip()) for part in cleaned.split(",") if part.strip())


def _clean_profile_id(phase: str, value: object) -> str:
    profile_id = _strip_optional_quotes(str(value).strip())
    if not profile_id:
        raise ValueError(f"reviewer_profiles.{phase} contains an empty reviewer profile id.")
    return profile_id


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _profile_entry(phase: str, profile: ReviewerProfile, doctor_report: Mapping[str, object]) -> dict[str, object]:
    evidence = _adapter_evidence(profile, doctor_report)
    return {
        "phase": phase,
        "profile_id": profile.profile_id,
        "label": profile.label,
        "adapter": profile.adapter,
        "model": profile.model,
        "available": evidence["available"],
        "ready": evidence["ready"],
        "fallback_policy": profile.fallback_policy,
        "evidence": evidence,
    }


def _adapter_evidence(profile: ReviewerProfile, doctor_report: Mapping[str, object]) -> dict[str, object]:
    ready = bool(doctor_report.get("ready", False))
    available = bool(doctor_report.get("available", ready))
    return {
        "adapter": profile.adapter,
        "kind": str(doctor_report.get("kind", "")),
        "command": str(doctor_report.get("command", "")),
        "command_path": str(doctor_report.get("command_path", "")),
        "available": available,
        "ready": ready,
        "fallback_policy": profile.fallback_policy,
        "hint": str(doctor_report.get("hint", "")),
    }


def _profile_warning(entry: Mapping[str, object]) -> dict[str, object]:
    evidence = dict(entry.get("evidence", {}))
    hint = str(evidence.get("hint", "")).strip() or "adapter is not ready"
    return {
        "phase": entry["phase"],
        "profile_id": entry["profile_id"],
        "adapter": entry["adapter"],
        "available": entry["available"],
        "ready": entry["ready"],
        "fallback_policy": entry["fallback_policy"],
        "evidence": evidence,
        "message": (
            f"Reviewer profile {entry['profile_id']!r} is configured for {entry['phase']}, "
            f"but adapter {entry['adapter']!r} is not ready: {hint}"
        ),
    }
