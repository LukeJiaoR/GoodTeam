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
    assert first == "task_6cbcd80b063928a8d91d676a9cb2b95e"
    assert len(first.removeprefix("task_")) == 32


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
    assert first == "exec_d72afff76d2f4d481f00f65f9018fdce"
    assert len(first.removeprefix("exec_")) == 32
    assert execution_id_from_task(task_id, attempt=2) != first
    assert execution_id_from_task(task_id) == first


def test_execution_attempt_must_be_strict_positive_int():
    task_id = task_id_from_github_issue("LukeJiaoR/GoodTeam", 220)
    invalid_attempts = [0, -1, True, False, 1.9, "1"]

    for attempt in invalid_attempts:
        try:
            execution_id_from_task(task_id, attempt=attempt)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid attempt: {attempt!r}")


def test_execution_id_accepts_run_seed_instead_of_attempt():
    task_id = task_id_from_github_issue("LukeJiaoR/GoodTeam", 220)

    assert execution_id_from_task(task_id, run_seed="manual-rerun") == "exec_8053e2120da4462abea0db22211fc193"


def test_branch_name_is_stable_from_execution_id():
    assert (
        branch_name_from_execution_id("exec_d72afff76d2f4d481f00f65f9018fdce")
        == "goodteam/exec_d72afff76d2f4d481f00f65f9018fdce"
    )


def test_idempotency_key_is_stable_for_issue_operation():
    first = idempotency_key_for_operation("LukeJiaoR/GoodTeam", 220, "open_pr")
    second = idempotency_key_for_operation(" lukejiaor/goodteam ", 220, " open_pr ")

    assert first == second
    assert first == "idem_d61b53e0e45bba168cb93b3d8b25e0de"
    assert len(first.removeprefix("idem_")) == 32
    assert first != idempotency_key_for_operation("LukeJiaoR/GoodTeam", 220, "close_issue")


def test_issue_number_must_be_strict_positive_int():
    invalid_issue_numbers = [0, -1, True, False, 1.9, "220"]

    for issue_number in invalid_issue_numbers:
        try:
            task_id_from_github_issue("LukeJiaoR/GoodTeam", issue_number)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid issue number: {issue_number!r}")

        try:
            idempotency_key_for_operation("LukeJiaoR/GoodTeam", issue_number, "open_pr")
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid issue number: {issue_number!r}")
