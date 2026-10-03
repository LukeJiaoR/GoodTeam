import importlib
import sys
import types
from dataclasses import FrozenInstanceError

import pytest

from goodteam_os.reviewer_profiles import (
    REVIEWER_PROFILE_EVIDENCE_KEYS,
    get_reviewer_profile,
    list_reviewer_profiles,
    reviewer_profile_ids,
    validate_reviewer_profile_ids,
)


EXPECTED_PROFILE_IDS = (
    "correctness-security",
    "architecture-maintainability",
    "product-operator",
)


def test_builtin_reviewer_profiles_have_required_contract_fields():
    for profile in list_reviewer_profiles():
        assert profile.profile_id
        assert profile.label
        assert profile.adapter
        assert profile.focus_areas
        assert profile.required_report_sections
        assert profile.required_evidence == REVIEWER_PROFILE_EVIDENCE_KEYS
        assert "invocation_evidence" in profile.required_evidence
        assert profile.fallback_policy
        assert profile.timeout_seconds > 0


def test_get_reviewer_profile_rejects_unknown_id_clearly():
    with pytest.raises(ValueError, match="Unknown reviewer profile 'missing-reviewer'"):
        get_reviewer_profile("missing-reviewer")


def test_list_reviewer_profiles_does_not_expose_mutable_global_state():
    profiles = list_reviewer_profiles()
    profiles.append(get_reviewer_profile("correctness-security"))

    assert reviewer_profile_ids() == EXPECTED_PROFILE_IDS
    assert len(list_reviewer_profiles()) == len(EXPECTED_PROFILE_IDS)

    profile = profiles[0]
    with pytest.raises(FrozenInstanceError):
        profile.focus_areas += ("mutated",)


def test_validate_reviewer_profile_ids_accepts_valid_ids_and_dedupes_in_order():
    assert validate_reviewer_profile_ids(
        [
            "product-operator",
            "correctness-security",
            "product-operator",
        ]
    ) == ("product-operator", "correctness-security")


def test_validate_reviewer_profile_ids_rejects_unknown_ids_clearly():
    with pytest.raises(ValueError, match="Unknown reviewer profile 'vendor-specific'"):
        validate_reviewer_profile_ids(["correctness-security", "vendor-specific"])


def test_profile_listing_has_no_runtime_dispatch_or_worker_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    profiles = list_reviewer_profiles()

    assert [profile.profile_id for profile in profiles] == list(EXPECTED_PROFILE_IDS)
    assert not (tmp_path / ".goodteam").exists()


def test_reviewer_profiles_module_does_not_touch_worker_or_dispatch_modules(monkeypatch):
    workers = types.ModuleType("goodteam_os.workers")
    dispatch = types.ModuleType("goodteam_os.dispatch")

    def fail_getattr(name):
        raise AssertionError(f"runtime module attribute accessed: {name}")

    workers.__getattr__ = fail_getattr
    dispatch.__getattr__ = fail_getattr
    monkeypatch.setitem(sys.modules, "goodteam_os.workers", workers)
    monkeypatch.setitem(sys.modules, "goodteam_os.dispatch", dispatch)

    import goodteam_os.reviewer_profiles as reviewer_profiles

    module = importlib.reload(reviewer_profiles)

    assert module.reviewer_profile_ids() == EXPECTED_PROFILE_IDS
