import json
import subprocess

import pytest

from scripts.ci.classify_changes import (
    Change,
    append_summary,
    changes_between,
    classify_changes,
    full_plan,
    main,
    parse_name_status,
    write_github_outputs,
)


def test_docs_only_is_tier_zero_without_product_jobs():
    plan = classify_changes([Change("M", ("docs/testing-strategy.md",))])

    assert plan.tier == 0
    assert plan.backend == "none"
    assert plan.frontend == "none"


def test_mixed_changes_take_highest_tier_and_union_stack_impact():
    plan = classify_changes(
        [
            Change("M", ("src/goodteam_os/execution/ledger.py",)),
            Change("M", ("frontend/src/components/ApprovalBanner.jsx",)),
        ]
    )

    assert plan.tier == 4
    assert plan.backend == "full"
    assert plan.frontend == "full"
    assert plan.live_smoke_evidence_required is True


@pytest.mark.parametrize(
    ("path", "tier", "backend", "frontend", "smoke"),
    [
        ("tests/test_memory.py", 1, "focused", "none", False),
        ("src/goodteam_os/memory.py", 2, "focused", "none", False),
        ("src/goodteam_os/execution/orchestrator.py", 3, "full", "none", False),
        ("src/goodteam_os/workers.py", 3, "full", "none", False),
        ("src/goodteam_os/telegram.py", 4, "full", "none", True),
        ("frontend/src/utils/time.js", 2, "focused", "full", False),
        ("frontend/src/features/projects/ProjectHomePage.jsx", 4, "focused", "full", True),
        ("frontend/src/App.jsx", 4, "focused", "full", True),
        ("frontend/src/index.css", 4, "focused", "full", True),
        ("src/goodteam_os/pi_rpc.py", 4, "full", "none", True),
        (".github/workflows/ci.yml", 3, "full", "full", False),
        ("frontend/package-lock.json", 3, "none", "full", False),
        ("unowned/new-system.conf", 3, "full", "full", False),
    ],
)
def test_path_policy(path, tier, backend, frontend, smoke):
    plan = classify_changes([Change("M", (path,))])

    assert (plan.tier, plan.backend, plan.frontend) == (tier, backend, frontend)
    assert plan.live_smoke_evidence_required is smoke


def test_focused_paths_emit_fixed_test_targets():
    plan = classify_changes(
        [
            Change("M", ("src/goodteam_os/memory.py",)),
            Change("M", ("frontend/src/utils/time.js",)),
        ]
    )

    assert plan.backend_test_targets == ("tests/test_memory.py", "tests/test_web.py")


@pytest.mark.parametrize(
    ("paths", "tier", "backend", "frontend"),
    [
        (
            [
                "docs/superpowers/specs/2026-07-16-goodteam-product-frontend-redesign-design.md"
            ],
            0,
            "none",
            "none",
        ),
        (
            [
                "src/goodteam_os/telegram.py",
                "src/goodteam_os/workers.py",
                "tests/test_telegram_runtime_controls.py",
            ],
            4,
            "full",
            "none",
        ),
        (
            [
                "frontend/package-lock.json",
                "frontend/src/app/AppRouter.jsx",
                "src/goodteam_os/project_import.py",
                "tests/test_web.py",
            ],
            4,
            "full",
            "full",
        ),
    ],
)
def test_historical_pr_file_sets(paths, tier, backend, frontend):
    plan = classify_changes([Change("M", (path,)) for path in paths])

    assert (plan.tier, plan.backend, plan.frontend) == (tier, backend, frontend)


def test_name_status_parser_preserves_add_delete_and_rename_paths():
    changes = parse_name_status(
        b"A\0docs/new.md\0D\0src/goodteam_os/memory.py\0"
        b"R100\0frontend/src/old.js\0frontend/src/new.js\0"
    )

    assert changes == (
        Change("A", ("docs/new.md",)),
        Change("D", ("src/goodteam_os/memory.py",)),
        Change("R100", ("frontend/src/old.js", "frontend/src/new.js")),
    )


def test_name_status_parser_rejects_incomplete_records():
    with pytest.raises(ValueError, match="incomplete git name-status record"):
        parse_name_status(b"R100\0only-old-path\0")


def test_changes_between_reads_real_git_name_status(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "ci@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "CI Test"], check=True)
    tracked = tmp_path / "README.md"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked.write_text("head\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qam", "head"], check=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.chdir(tmp_path)

    assert changes_between(base, head) == (Change("M", ("README.md",)),)


def test_empty_changes_and_unknown_paths_fail_closed():
    empty = classify_changes([])
    unknown = classify_changes([Change("A", ("unknown/file.bin",))])

    assert (empty.tier, empty.backend, empty.frontend) == (3, "full", "full")
    assert (unknown.tier, unknown.backend, unknown.frontend) == (3, "full", "full")
    assert "fail closed" in empty.reasons[0]
    assert "fail closed" in unknown.reasons[0]


def test_plan_serialization_is_deterministic():
    plan = classify_changes(
        [
            Change("M", ("src/goodteam_os/memory.py",)),
            Change("M", ("frontend/src/utils/time.js",)),
        ]
    )

    first = json.dumps(plan.as_dict(), separators=(",", ":"), sort_keys=True)
    second = json.dumps(plan.as_dict(), separators=(",", ":"), sort_keys=True)
    assert first == second


def test_full_plan_selects_both_stacks_without_smoke():
    plan = full_plan()

    assert (plan.tier, plan.backend, plan.frontend) == (3, "full", "full")
    assert plan.live_smoke_evidence_required is False


def test_github_outputs_and_summary_expose_decision(tmp_path):
    plan = classify_changes([Change("M", ("src/goodteam_os/telegram.py",))])
    output = tmp_path / "output"
    summary = tmp_path / "summary.md"

    write_github_outputs(output, plan)
    append_summary(summary, plan)

    output_text = output.read_text(encoding="utf-8")
    summary_text = summary.read_text(encoding="utf-8")
    assert "tier=4\n" in output_text
    assert "backend=full\n" in output_text
    assert "frontend=none\n" in output_text
    assert "live_smoke_evidence_required=true\n" in output_text
    assert "plan_json={" in output_text
    assert "Tier: **4**" in summary_text
    assert "Live smoke evidence required: `true`" in summary_text
    assert "src/goodteam_os/telegram.py" in summary_text


def test_cli_full_mode_writes_outputs_and_summary(tmp_path, capsys):
    output = tmp_path / "output"
    summary = tmp_path / "summary.md"

    exit_code = main(
        [
            "--full",
            "--github-output",
            str(output),
            "--summary",
            str(summary),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "full"
    assert "backend=full" in output.read_text(encoding="utf-8")
    assert "manual full verification" in summary.read_text(encoding="utf-8")
