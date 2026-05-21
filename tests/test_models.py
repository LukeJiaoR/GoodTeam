from pathlib import Path

from goodteam_os.models import apply_role_overrides, load_model_profile


def test_load_model_profile_reads_balanced_defaults():
    profile = load_model_profile(Path("models.yaml"), "balanced")

    assert profile["orchestrator"]["primary"] == "codex:gpt-5.2"
    assert profile["research"]["primary"] == "agy:gemini-3-pro-preview"
    assert profile["frontend"]["primary"] == "agy:gemini-3-pro-preview"
    assert profile["architect"]["primary"] == "claude:opusplan"
    assert profile["implementation"]["mode"] == "worker-dispatch"
    assert profile["qa"]["primary"] == "deepseek:deepseek-reasoner"


def test_apply_role_overrides_replaces_primary_model():
    profile = load_model_profile(Path("models.yaml"), "balanced")

    updated = apply_role_overrides(profile, ["architect=agy:gemini-3-pro-preview"])

    assert updated["architect"]["primary"] == "agy:gemini-3-pro-preview"
    assert updated["architect"]["reviewer"] == "openai:gpt-5.2-pro"
