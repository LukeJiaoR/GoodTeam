from pathlib import Path

from goodteam_os.models import apply_role_overrides, load_model_profile, resolve_project_model_profile


def test_load_model_profile_reads_balanced_defaults():
    profile = load_model_profile(Path("models.yaml"), "balanced")

    assert profile["orchestrator"]["primary"] == "codex:gpt-5.6-sol"
    assert profile["research"]["primary"] == "codex:gpt-5.6-sol"
    assert profile["frontend"]["primary"] == "codex:gpt-5.6-sol"
    assert profile["architect"]["primary"] == "codex:gpt-5.6-sol"
    assert profile["implementation"]["mode"] == "worker-dispatch"
    assert profile["implementation"]["primary"] == "codex:gpt-5.6-sol"
    assert profile["qa"]["primary"] == "codex:gpt-5.6-sol"


def test_all_model_profiles_route_programming_roles_to_codex_model():
    for profile_name in ["balanced", "cheap", "max_quality"]:
        profile = load_model_profile(Path("models.yaml"), profile_name)
        expected_model = "codex:gpt-5.6-sol" if profile_name == "balanced" else "codex:gpt-5.4"
        for role in ["implementation", "frontend", "backend", "qa", "code_review"]:
            assert profile[role].get("primary") == expected_model, f"{profile_name}.{role}"
        assert profile["architect"]["primary"] == expected_model
        assert profile["product_requirement"]["primary"] == expected_model


def test_all_model_profiles_follow_task_strength_routing_policy():
    coding_and_debug_roles = {
        "implementation": "daily coding / IDE agent workflow",
        "frontend": "daily coding",
        "backend": "daily coding",
        "qa": "deep debugging",
        "code_review": "maintenance / multi-file refactor / deep debugging",
    }
    reasoning_product_docs_roles = {
        "orchestrator": "general orchestration",
        "research": "docs / analysis / research",
        "product_requirement": "product logic",
        "architect": "architecture design",
        "review": "complex reasoning",
        "planner": "complex reasoning",
        "ui": "product logic",
        "release": "docs / analysis",
        "retro_memory": "docs / analysis",
    }

    for profile_name in ["balanced", "cheap", "max_quality"]:
        profile = load_model_profile(Path("models.yaml"), profile_name)
        expected_model = "codex:gpt-5.6-sol" if profile_name == "balanced" else "codex:gpt-5.4"

        # Balanced is the product default; explicitly selected alternatives retain
        # their own routing and can still be overridden per role.
        for role, scene in {**coding_and_debug_roles, **reasoning_product_docs_roles}.items():
            assert profile[role].get("primary") == expected_model, f"{profile_name}.{role} should use {expected_model} for {scene}"


def test_apply_role_overrides_replaces_primary_model():
    profile = load_model_profile(Path("models.yaml"), "balanced")

    updated = apply_role_overrides(profile, ["architect=codex:gpt-5.5"])

    assert updated["architect"]["primary"] == "codex:gpt-5.5"
    assert updated["architect"]["reviewer"] == "codex:gpt-5.6-sol"


def test_resolve_project_model_profile_applies_project_defaults_and_overrides():
    target = {
        "model_profile": "cheap",
        "role_model_overrides": {
            "architect": "codex:gpt-5.5",
            "frontend": "codex:gpt-5.3-codex",
        },
    }

    profile_name, role_models = resolve_project_model_profile(
        Path("models.yaml"),
        target,
        explicit_profile="",
        role_overrides=[],
    )

    assert profile_name == "cheap"
    assert role_models["planner"]["primary"] == "codex:gpt-5.4"
    assert role_models["architect"]["primary"] == "codex:gpt-5.5"
    assert role_models["frontend"]["primary"] == "codex:gpt-5.3-codex"


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
    assert role_models["planner"]["primary"] == "codex:gpt-5.4"
    assert role_models["architect"]["primary"] == "codex:gpt-5.5"
