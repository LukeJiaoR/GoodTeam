# UI and ordinary Runtime acceptance — 2026-10-13

Refs #535 and #534. Baseline: `bcd5dea1cea2a4eee383e78a9359d1945231c854`.
The previously accepted Settings routing, Send overlap and mobile layout were
outside this follow-up. No server authority, approval binding, ledger, adapter,
provider configuration, deployment or CI policy changed.

## UI acceptance

The new motion control provides **Follow system** and **Reduce motion**, persists
across reload, synchronizes mounted consumers and browser tabs, and always honors
a reducing OS preference. Work's existing animation and smooth-scroll consumers
use the same preference as the CSS reduction rule.

Approval notes expose the Ctrl/Command+Enter shortcut through an accessible
description. IME composition cannot accidentally submit approval. Pending
submission has `aria-busy` and a live status. Modal Tab/Shift+Tab cycles stay
inside the currently open panel, including the unsaved-review dialog; Escape
preserves the draft and closing the panel returns focus to the trigger.

Real Chromium and HTTP tests against fresh temporary workspaces observed:

- takeover → PRD → RFC → security change request → RFC/security reapproval →
  release → complete, with one durable decision per deliberate submission;
- rationale-required approval and rejection, preserved ledger after refresh,
  and concurrent tabs producing one decision/execution;
- focus wrap, unsaved edit preservation, Escape and trigger restoration;
- saved reduction across reload/navigation and live OS preference changes;
- an outgoing approval request deliberately interrupted before reaching the
  server, followed by explicit retry; no fabricated API response or seeded ledger.

These UI tests explicitly use **fake Workers**. They establish UI/HTTP contracts,
not real model delivery. An ego-browser desktop observation separately confirmed
readable evidence, a bounded focus loop and an actual keyboard submission result.
No screen-reader application was exercised; this is keyboard/semantic acceptance.

## Actual ordinary Worker task

A fresh Git checkout under a dedicated temporary root contained the UI candidate.
Operator state and GoodTeam home were separate siblings outside the target.
The task used the production `WorkerJobStore.create_job` / `run_job` path, without
mocking subprocesses, changing job status, or repairing internal state.

The useful implementation task was to extract and test #535's motion policy:
`normalizeMotionPreference`, `shouldReduceMotion`, and hook integration. Its
write scope was exactly the helper, Node tests, and hook. The final three files
were copied into this candidate only after review and byte-digest comparison.

Actual backend: Codex CLI, model `gpt-5.6-sol`, ordinary one-shot Worker.
The existing adapter used its standard sandbox-disabled command inside the isolated
target; this run does not claim an enforced OS write boundary. The Worker task
prohibited network, credentials, commits and writes outside the target. The host
used the existing Runtime authentication route; no credentials were printed or
added to the repository. The adapter supplied no authoritative cost/usage total.

- Run: `real-534-535-motion`.
- Implementation job: `job-38dda557-fd90-47db-8ad6-545c0bea239b`.
- Independent read-only QA job: `job-d84c67dd-bcad-42eb-bb1c-a2530f84e1e1`.
- Implementation, QA and explicit implementation reentry all exited 0.
- Implementation and QA each actually ran `node --test
  frontend/src/lib/motion/preferences.test.js`: 5 passed. Hook syntax checks passed.
- QA found no policy failure and identified hook event coverage as a limitation;
  the candidate's hook tests and browser checks cover that integration separately.

A fresh Python process reconstructed the completed job and its four receipts
without dispatch. A later **explicit** `run_job` of that same job performed another
real invocation. It retained the original records and added a distinct preparation
and transport identity:

| Invocation | Transport ID | Prepared reference |
| --- | --- | --- |
| Initial | `7a316aff-ec28-4c36-bdaf-322fa0802ef2` | `b1984776a30abdffa59fb551165a63eec3da333cdc96bc7272f1c89a98dcab52` |
| Explicit reentry | `0a0778ff-66d5-45a3-acd4-74eafcd3c92f` | `474cd60fe48ad50ac0baa806ff79e481e0745815b12c7ad2553bedf8d49f764c` |

Each preparation and writeback was checked against the exact job/project/run and
transport reference. All recorded writeback event IDs and timestamps matched
actual WAL events. The initial implementation appended two result events and one
provenance event. Reentry returned different result text and appended two result
events; identical provenance replay referenced `mem:f2c5d4c7d2ce`, with zero new
append and the original timestamp. QA had its own three writeback events.

The helper/hook file digests and transport identities are retained in
[the bounded receipt](ui-runtime-acceptance-2026-10-13.json). Local raw receipts,
results, screenshots, logs and the task driver remain under
`output/acceptance-534-535/`; the isolated Runtime target is retained for inspection.
No temporary target or untracked output was deleted.

This accepts one ordinary Worker task's actual execution, read-only process
restoration, explicit reentry and reviewed usable code delivery. **It does not
accept the full real Manager → Work → Outcomes browser loop**, a provider transient
failure, arbitrary mid-call crash recovery, Pi sessions, or #411's five tasks across
two projects. Provider receipt/read remain unknown. #534's broader outstanding
Runtime acceptance and #411/#418/#419 must not be closed based on this result alone.

## Verification

Tier 4 browser-visible UI, with Tier 2 presentation/state consumption. The server
approval and Runtime contracts are unchanged. Related backend tests were exercised
because this UI presents their evidence and decisions.

```sh
NODE_OPTIONS=--no-experimental-webstorage npm run test --prefix frontend
NODE_OPTIONS=--no-experimental-webstorage npm run lint --prefix frontend
NODE_OPTIONS=--no-experimental-webstorage npm run build --prefix frontend
NODE_OPTIONS=--no-experimental-webstorage npm run test:e2e --prefix frontend -- \
  approval-keyboard-motion.spec.js informed-review-approval.spec.js \
  informed-work-approval.spec.js concurrent-approval.spec.js
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_worker_memory_handoff.py tests/test_workers.py \
  tests/test_delivery_outcomes.py tests/test_review_workbench_current_result.py \
  tests/test_web.py -q
.venv/bin/ruff check src/goodteam_os/ tests/
git diff --check
```

Node 25 exposes an experimental global Web Storage conflicting with the existing
jsdom test environment; disabling it makes the existing browser storage object
available. The first run's storage failures are preserved, not counted as passing.
Python HTTP tests require permission to bind local temporary ports. The initial
sandboxed focused run had 368 passes / 52 failures; the external rerun passed
all **420**. The initial sandboxed full run was interrupted after 564 passes,
20 failures, 54 errors and 17 skips, predominantly blocked local networking. It is
not a full-suite pass and is not comparable to the earlier #536 baseline report.

Final frontend, browser and full-backend outcomes are recorded below once settled.
A Draft PR is for review only; no exact-SHA candidate CI, ready status, merge or
production deployment is implied by local evidence.

Settled outcomes:

- Frontend: 46 unit files / **593 tests passed**; Node tests **33 passed**;
  lint and production build passed. The final isolated IME/decision regression
  rerun also passed after moving the live announcement outside the busy form.
- Browser: the seven-scenario group passed, and the additional interrupted-request
  scenario passed separately: **8 total**, with real HTTP and fake Workers.
- Related backend: **420 passed**; ruff and `git diff --check` clean.
- Optional external full backend run was stopped after **855 passed / 18 skipped**
  in 454 seconds, with no failure observed in that partial run. It is incomplete,
  not green full-suite evidence. Full backend is not required for this frontend-only
  presentation/input change, which preserves server approval and dispatch contracts;
  focused contract checks and the live UI checks are the selected validation.
- No heavy remote candidate CI was dispatched. The branch/PR remains Draft for review;
  exact-SHA candidate validation is required before making it ready or merging.
