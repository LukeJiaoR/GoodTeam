# Basic gate cleanup: execution milestone suites

Baseline: `a11a336f842baeb818782f1457bbae143be72395` (latest `main` checked on
2026-10-02). This first increment reduces permanent execution test scaffolding;
it does not claim that the entire backend has become a minimal gate suite.

## Current user flows and retained gates

| User behavior or risk | Permanent coverage |
| --- | --- |
| Intake an issue, run a Worker, bind its PR, observe CI, ingest reviews/merge, replay and finalize | `test_execution_e2e_contract.py::test_execution_lifecycle_gate_replays_without_duplicate_side_effects`: one fake-Worker lifecycle, repeated operations, persisted PR, review identities, complete evidence, one linked memory/ledger closure |
| CI changes after an earlier successful observation | `test_execution_e2e_contract.py::test_check_sync_is_idempotent_until_summary_changes`: identical observations do not duplicate events; a changed failure is recorded |
| Review/merge attempted without an execution PR | `test_execution_e2e_contract.py::test_sync_methods_fail_clearly_without_pr_binding`: both operations reject missing binding |
| Finalize an unbound or failed execution | `test_execution_e2e_contract.py::test_missing_pr_returns_pending_without_memory_or_ledger_closure` and `test_failed_execution_without_pr_builds_failed_provenance`: no false completed report or premature closure |
| Use the execution CLI | `test_execution_cli.py`: actual command dispatch and JSON, missing arguments, service error exit/status |
| Start another task without letting history control current work | Two existing contract tests moved into `test_team_workspace.py`; current workflow authority and historical read-only behavior remain protected |
| Persist model effort without bypassing Worker eligibility | Five existing effort partitions moved into `test_team_control_plane.py`; both doctor lookup sites stay mocked and the unavailable-runtime rejection stays asserted |

Existing focused safety tests remain alongside this gate: deterministic identity
(`test_execution_idempotency.py`), PR authority (`test_execution_pr_boundary.py`),
CI binding (`test_execution_ci_binding.py`), sandbox boundaries
(`test_agent_sandbox_v0.py`), Skill cancellation/process lifecycle, task locks and
Worker task guards. A happy-path lifecycle alone does not replace these risks.

## Deletion and consolidation map

Names below are pytest function names; all paths are under `tests/`. Call times
are observations from the baseline focused run, excluding setup/collection.
Rows sharing a retained gate must not be counted as independent timing savings.

| Old file and test | Call ms | Disposition and remaining coverage |
| --- | ---: | --- |
| `test_execution_first_run.py::test_deterministic_task_and_execution_ids_are_created_from_issue` | 0.09 | Delete historical issue/hash example. `test_execution_idempotency.py::test_task_id_is_stable_for_same_github_issue`, `test_task_id_changes_for_different_github_issue`, and `test_execution_id_is_stable_from_task_and_attempt` retain the identity contract. The sample issue's literal hash is no longer a permanent oracle. |
| `test_execution_first_run.py::test_first_run_intake_writes_task_run_worker_job_and_replay` | 6.63 | Delete milestone file-existence/replay duplication. Lifecycle gate checks actual persisted replay and transitions through completion. |
| `test_execution_first_run.py::test_re_running_dogfood_path_does_not_duplicate_run_job_or_events` | 7.14 | Delete a second intake/rerun setup. Lifecycle gate asserts one job, PR and each lifecycle event after replay. |
| `test_execution_first_run.py::test_first_run_uses_local_github_client_without_network` | 6.26 | Retire the historical fixture-specific constant/adapter check. Core gate injects an in-memory GitHub client and fake Worker; historical offline dogfood entrypoint is a manual smoke, not four permanent milestone tests. |
| `test_execution_github_loop.py::test_check_sync_is_idempotent_until_summary_changes` | 10.20 | Move the changed-state boundary to the lifecycle contract file using its shared intake fixture. |
| `test_execution_github_loop.py::test_review_sync_records_reviews_and_comments_once_by_github_id` | 7.49 | Absorb into lifecycle gate, including distinct review/comment IDs and actors. |
| `test_execution_github_loop.py::test_merge_sync_records_merge_once_and_preserves_completed_run` | 7.92 | Absorb into lifecycle gate, including one merge event, commit SHA, actor and persisted PR. |
| `test_execution_github_loop.py::test_sync_methods_fail_clearly_without_pr_binding` | 3.30 | Move to lifecycle contract file through the public API; both rejection paths stay permanent. |
| `test_execution_final_provenance.py::test_full_replay_creates_final_provenance_evidence` | 14.48 | Extend lifecycle gate with execution/task/issue/PR identity, CI/review/merge truth, status and all pre-closure event IDs. |
| `test_execution_final_provenance.py::test_finalize_writes_one_memory_record_and_one_ledger_closure_event` | 17.03 | Same lifecycle gate checks one memory record, one closure, reciprocal IDs, PR/merge binding and evidence hash. |
| `test_execution_final_provenance.py::test_finalize_is_idempotent_across_reruns` | 14.32 | Same gate finalizes via fresh API instances twice; record IDs and evidence stay identical and counts remain one. |
| `test_execution_final_provenance.py::test_missing_pr_returns_pending_without_memory_or_ledger_closure` | 7.85 | Move the pending/no-write boundary without a second fake client or full-loop helper. |
| `test_execution_final_provenance.py::test_failed_execution_without_pr_builds_failed_provenance` | 0.38 | Move the failed-report boundary; keep failed status rather than falsely claiming completion. |
| `test_execution_cli.py::test_execution_parser_contains_expected_commands` | 4.20 | Delete parser-only happy path. `test_execution_cli_commands_emit_stable_json` invokes all five same commands through `cli.main`; missing-argument and service-error cases remain. |
| `test_execution_e2e_contract.py::test_execution_fixture_e2e_contract_replays_full_p0_1_loop_without_duplicates` | 36.77 | Rename to the current lifecycle gate and extend it with finalization. |
| `test_team_workspace_next_task.py::test_completed_current_project_accepts_next_task_but_history_stays_read_only` | 0.11 | Move unchanged into `test_team_workspace.py`, which owns the same projection contract. |
| `test_team_workspace_next_task.py::test_active_workflow_keeps_authority_over_the_assistant_conversation` | 0.09 | Move unchanged into `test_team_workspace.py`; active workflow authority remains asserted. |
| `test_runtime_effort_contract.py::test_model_effort_survives_team_persistence_snapshot_and_resolution[none/minimal/xhigh/max/ultra]` | 3.00–3.46 each | Move all five partitions into `test_team_control_plane.py`; retain direct-import doctor patching and rejected dispatch. Import the web helper inside this test to avoid adding it to unrelated focused collection. |

The three milestone suites' repeated fake GitHub clients and clocks are removed.
The retained lifecycle file has five tests, rather than concatenating the old
fourteen tests. Two small fragments join their existing domain suites without
duplicating imports; their seven cases are retained.

## Counts and measurements

| Measure | Before | After |
| --- | ---: | ---: |
| Python test files | 159 | 154 |
| Python test functions | 2,332 | 2,322 |
| Collected backend cases | 3,067 | 3,057 |
| Related focused cases | 110 | 100 |
| Test source lines in this diff | — | −528 net |
| Focused process wall time, median of three alternating runs | 1.180 s | 1.058 s |
| Pytest collection median, includes imports | 0.390 s | 0.327 s |
| Test call time median | 0.420 s | 0.334 s |
| Setup median | 0.069 s | 0.073 s |
| Teardown median | 0.011 s | 0.010 s |
| Fixture timing subtotal, diagnostic only | 0.046 s | 0.054 s |
| Child process launches in focused tests | 0 | 0 |

The observed process reduction is only 0.121 s, with run-to-run variation. Phase
medians do not add to process medians; fixture times overlap setup and other
fixture scopes. Do not add these columns or extrapolate a full-CI speedup. A
separate fresh-process API/workspace/team/web import probe was 0.212/0.208 s;
there is no established import improvement. Maintenance reduction is the main
benefit. Frontend counts remain 6 Node and 270 Vitest cases.

## Validation and environment

Cloud: Linux x64, Python 3.12.14, pytest 9.1.1, Ruff 0.16.10. Dependencies match
the isolated baseline; `PYTHONPATH=src` selects each worktree's sources. All
focused before/after runs passed. Full final-head results and exact SHA are
recorded in the Draft PR description, with failures compared against baseline
node IDs. No skip/xfail, runner security change or manual remote workflow rerun
is part of this cleanup.

The baseline full run was 2,705 passed, 358 failed and four setup errors in
188.26 s. This cloud harness owns `/usr/bin/git` and its ancestors as UID 65534,
so root-owned executable trust checks reject them. These existing environment
failures remain in the permanent suite and cannot count as cleanup regressions
or successful coverage. Other heads' green CI is not evidence for this PR.

Manual historical-tool smoke: run `python -m goodteam_os.execution.dogfood
--project-root <fresh temporary directory> --adapter noop`; observed exit 0,
one persisted `succeeded` execution. No real model, Telegram or business message
was used. This one-off tool does not need a dedicated permanent test file.
