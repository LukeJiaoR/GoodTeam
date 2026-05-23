from pathlib import Path

from goodteam_os.models import apply_role_overrides, load_model_profile, resolve_project_model_profile


def test_load_model_profile_reads_balanced_defaults():
    profile = load_model_profile(Path("models.yaml"), "balanced")

    assert profile["orchestrator"]["primary"] == "codex:gpt-5.5"
    assert profile["research"]["primary"] == "codex:gpt-5.5"
    assert profile["frontend"]["primary"] == "codex:gpt-5.5"
    assert profile["architect"]["primary"] == "codex:gpt-5.5"
    assert profile["implementation"]["mode"] == "worker-dispatch"
    assert profile["qa"]["primary"] == "codex:gpt-5.5"


def test_all_model_profiles_route_primary_roles_to_codex_gpt_55():
    for profile_name in ["balanced", "cheap", "max_quality"]:
        profile = load_model_profile(Path("models.yaml"), profile_name)
        for role, config in profile.items():
            assert config.get("primary") == "codex:gpt-5.5", f"{profile_name}.{role}"


def test_apply_role_overrides_replaces_primary_model():
    profile = load_model_profile(Path("models.yaml"), "balanced")

    updated = apply_role_overrides(profile, ["architect=codex:gpt-5.5"])

    assert updated["architect"]["primary"] == "codex:gpt-5.5"
    assert updated["architect"]["reviewer"] == "codex:gpt-5.5"


def test_resolve_project_model_profile_applies_project_defaults_and_overrides():
    target = {
        "model_profile": "cheap",
        "role_model_overrides": {
            "architect": "codex:gpt-5.5",
            "frontend": "codex:gpt-5.5",
        },
    }

    profile_name, role_models = resolve_project_model_profile(
        Path("models.yaml"),
        target,
        explicit_profile="",
        role_overrides=[],
    )

    assert profile_name == "cheap"
    assert role_models["planner"]["primary"] == "codex:gpt-5.5"
    assert role_models["architect"]["primary"] == "codex:gpt-5.5"
    assert role_models["frontend"]["primary"] == "codex:gpt-5.5"


def test_resolve_project_model_profile_explicit_overrides_win_last():
    target = {
        "model_profile": "cheap",
        "role_model_overrides": {
            "architect": "codex:gpt-5.5",
        },
    }

    profile_name, role_models = resolve_project_model_profile(
        Path("models.yaml"),
        target,
        explicit_profile="max_quality",
        role_overrides=["architect=codex:gpt-5.5"],
    )

    assert profile_name == "max_quality"
    assert role_models["planner"]["primary"] == "codex:gpt-5.5"
    assert role_models["architect"]["primary"] == "codex:gpt-5.5"
