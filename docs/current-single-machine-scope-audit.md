# Current single-machine development scope audit

Scope: user-defined #686 Manager/Worker/Context/Human loop; #445 methods,
#696 Planner, #700 invocation supervision, #701 Search, #677 compact UI.
Issue text re-read from GitHub for this audit; unchecked historic issue lists are
not proof of missing implementation. This candidate is local, not latest main.

## Code connected in the complete candidate

| User outcome | Existing production connection |
| --- | --- |
| Manager discusses, clarifies, delegates rather than doing project work | manager_dialogue / manager_execution; first-party Manager method package; manager_pi_tools and Runtime witness |
| Invocation-specific Role methods and generic Workers | team_control_plane explicit runtime source; dispatch_methods; task_start; PiMethodInput; original job/run/task identity |
| Independent concurrent work, capacity 10 | project_parallel_dispatch; original batch ledger; short atomic claim; shared_worker_pool; no per-Role persistent pool |
| New work during execution | assistant_queue; project_scope_addition; original grant confirmation and budgets |
| Researcher / Planner on demand | project_outcome_discovery; readonly_pi_methods; project_investigation_feedback; no mandatory research/planning stage |
| Authorized Search, not default network | tasks; assistant_queue; TaskSearchConfiguration; original JobWebSearch; immutable Provider policy; readonly optional tool contract |
| Worker progress / follow-up | manager_worker_control; project_running_observation; actual Runtime capabilities and original job receipt |
| Known success, partial, failed and mixed results | project_batch_feedback; project_investigation_feedback; continuation; no false acceptance or retry of unknown execution |
| Human question/reply without whole-project idle | original board; project_human_feedback / running observation; no authorization from mere clarification |
| Next work and recovery | project_iteration_continuation; original planning reservations, task/dispatch receipts; Stop and budget retained |
| UI compact Review/Settings/Knowledge | existing reviewCompact and compact-pages candidates already present |
| UI attachments/import continuity | exact already-pushed b6517d7629f299e197fad8f45f05df859657d182 patch incorporated, not redeveloped |

Audit found and fixed one additional production defect: all-Pi mixed Roles could
suppress the supported Role's default methods. See per-role-method-capture.md.
No other confirmed, currently authorized missing core implementation was found
in this audit. This is a scoped code conclusion, not proof all possible product
behaviors work or that every historic open issue is only verification.

## Verification / release still outstanding

- Actual installed Pi SDK tool registry, method delivery/resource reads, behavior
  and session restoration. Fixtures do not prove model adherence.
- Actual configured Responses endpoint and hosted Search, source feedback and
  real request costs; no real network/provider call made in these slices.
- Trusted Git workspace execution and integration; full backend still has the
  same 37 baseline failures/errors, not a green suite.
- #419/#630 actual continuous project: real delivery → new fact/investigation →
  Manager's different next work → second real delivery, plus original multi-task,
  multi-project, independent review and recovery requirements.
- #677 actual browser desktop/dark/narrow/keyboard/dimensions evidence.
- Publishing/integration to GitHub and service uptake: local source archive is
  not pushed main, a running service, or deployment.

Paused strict historical Reviewer/Skill pilots and #687 Backlog are not newly
authorized development and not global prerequisites for the ordinary Pi path.
No scope, credentials, permissions, production deployment or paid budget changed.
