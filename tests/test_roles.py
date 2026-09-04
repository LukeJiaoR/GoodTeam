from goodteam_os.roles import (
    artifact_contract,
    list_role_definitions,
    workflow_steps_for_source,
)


def test_all_roles_have_unique_stable_prompts():
    roles = list_role_definitions()
    prompts = [role["stable_prompt"] for role in roles]

    assert {role["key"] for role in roles} >= {
        "orchestrator",
        "project_intake",
        "research",
        "product_requirement",
        "architect",
        "review",
        "planner",
        "ui",
        "implementation",
        "qa",
        "code_review",
        "release",
        "retro_memory",
    }
    assert all(prompt.strip() for prompt in prompts)
    assert len(set(prompts)) == len(prompts)
    manager = next(role for role in roles if role["key"] == "orchestrator")
    assert manager["name"] == "Task Assistant"
    assert "Task Assistant role" in manager["stable_prompt"]


def test_role_artifacts_have_configured_contracts():
    for role in list_role_definitions():
        if role["artifact"] == "state":
            continue
        contract = artifact_contract(role["artifact"])
        assert contract["role"] == role["key"]


def test_workflow_steps_are_loaded_from_role_config():
    new_project_steps = workflow_steps_for_source("new_project")
    existing_project_steps = workflow_steps_for_source("existing_project")

    assert new_project_steps[0]["key"] == "product_requirement"
    assert existing_project_steps[0]["key"] == "takeover_review"
    assert existing_project_steps[1]["key"] == "product_requirement"


def test_prd_contract_is_project_roadmap_focused():
    contract = artifact_contract("prd")

    assert contract["role"] == "product_requirement"
    assert "roadmap" in contract["purpose"].lower()
    assert "takeover" not in contract["purpose"].lower()
    items = [item for section in contract["sections"] for item in section["items"]]
    assert any("decision-ready PRD" in item for item in items)
    assert any("Product / Requirement, Architect, and Review / QA" in item for item in items)
