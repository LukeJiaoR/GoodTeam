from goodteam_os.execution.idempotency import (
    branch_name_from_execution_id,
    execution_id_from_task,
    idempotency_key_for_operation,
    task_id_from_github_issue,
)


def test_task_id_is_stable_for_same_github_issue():
    first = task_id_from_github_issue("LukeJiaoR/GoodTeam", 220)
    second = task_id_from_github_issue(" lukejiaor/goodteam ", 220)

    assert first == second
    assert first == "task_6cbcd80b063928a8"


def test_task_id_changes_for_different_github_issue():
    assert task_id_from_github_issue("LukeJiaoR/GoodTeam", 220) != task_id_from_github_issue(
        "LukeJiaoR/GoodTeam",
        221,
    )


def test_execution_id_is_stable_from_task_and_attempt():
    task_id = task_id_from_github_issue("LukeJiaoR/GoodTeam", 220)

    first = execution_id_from_task(task_id, attempt=1)
    second = execution_id_from_task(task_id, attempt=1)

    assert first == second
    assert first == "exec_c10c36739cb80f57"
    assert execution_id_from_task(task_id, attempt=2) != first


def test_execution_id_accepts_run_seed_instead_of_attempt():
    task_id = task_id_from_github_issue("LukeJiaoR/GoodTeam", 220)

    assert execution_id_from_task(task_id, run_seed="manual-rerun") == "exec_979c1f46fd827842"


def test_branch_name_is_stable_from_execution_id():
    assert branch_name_from_execution_id("exec_c10c36739cb80f57") == "goodteam/exec_c10c36739cb80f57"


def test_idempotency_key_is_stable_for_issue_operation():
    first = idempotency_key_for_operation("LukeJiaoR/GoodTeam", 220, "open_pr")
    second = idempotency_key_for_operation(" lukejiaor/goodteam ", 220, " open_pr ")

    assert first == second
    assert first == "idem_d61b53e0e45bba16"
    assert first != idempotency_key_for_operation("LukeJiaoR/GoodTeam", 220, "close_issue")
