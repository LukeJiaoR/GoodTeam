# StockFlow first trial: 2026-11-05

Contract: [#630](https://github.com/LukeJiaoR/GoodTeam/issues/630).
Recovery defects: [#631](https://github.com/LukeJiaoR/GoodTeam/issues/631).
Project-autonomy acceptance remains [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419).

This is a record of real-provider trials and their interventions. It is not
acceptance of the full StockFlow delivery or project-level autonomy.

## Repeatable starting conditions

Each independent run starts with a new empty target and separate GoodTeam
workspace, service state, project registry, task history and memory. Preserve
the exact issue/input, source SHA, runtime/model configuration, Git baseline,
Worker identities, transitions, failures, operator interventions and results.
Never copy prior business code or task state into the next independent run.
Fix-and-continue work within one run records its source changes separately;
it is not a clean comparison of two GoodTeam versions.

Initial source: `37745da53d29cbfab97bb458cbcee3b0d9bfb079`.
Runtime: Codex CLI 0.164.0, requested model `gpt-5.6-sol`, ordinary Workers in
`workspace-write` sandbox, unsafe mode false, one Worker attempt. One attempt
does not bound provider request count or monetary cost; both are unknown.

## Observed workflow

| Run | Starting condition | Actual result | Operator intervention |
| --- | --- | --- | --- |
| 001 | Empty non-Git project, original source | Manager draft and preparation completed; start failed, then original task remained `workflow_start_indeterminate` | Added an empty Git root commit after product recovery offered no files; no business code or state edits |
| 002 | Empty Git project, original source | Initial PRD Worker failed ENOENT but template was offered as ready; later real PRD and RFC completed; stopped at pending RFC review | Separate environment-only absolute catalog configuration; normal change discussion and scoped manual PRD approval |
| 003 | Empty non-Git project, fix candidate | Baseline failure left no start claim/Workflow; product-created initial commit allowed the same confirmed task to start; real PRD completed with original Codex HOME; architecture started | Explicit Git initial-commit confirmation and product-scope approval; source updates only at idle review gates |

run002 stopped with no active Worker to avoid duplicate project development.
run003 began at `6916f60c5239f7c7863033cb3ea05d3851875a8d`; continuation
at the PRD gate used `4f78f5544f58b56adba858efa09137ad18e5dcbb` after review
corrections. It used the original relative-catalog configuration, without
run002's environment workaround.

## Defects and changes

1. Empty imported projects could become prepared but had no supported Git
   recovery. Reuse the initializer behind explicit README.md + .gitignore
   confirmation, including the metadata names already ignored by import.
2. A deterministic missing-baseline failure consumed the start claim. Check
   the prerequisite before a new claim; retain uncertain historical starts.
3. Isolated Codex HOME changed relative model-catalog resolution. Bind that
   path to the original HOME with a command override while keeping isolation.
4. Real PRD failures published templates. Block with the existing retry path,
   preserve old artifacts as history, and require normal approval after retry.
5. Full task text made the Worker summary 4357px tall. Preserve the exact text
   and clamp the summary to three lines; run003 measured about 59px.
6. Approval briefs lost Chinese scope/exclusions beyond the 8k preview and
   nested heading context. Parse a bounded, hash-matched document privately;
   retain the public preview limit and exclude H1 document titles from context.
7. Independent review identified misleading success events after failure,
   rejected-PRD retry failure with a stale artifact reference, missing success
   events after recovery, public polling-payload growth, and H1 category
   pollution. Corrections preserve both failure and successful-retry evidence.

## Verification and evidence limits

Tier 4: full backend, related frontend, isolated real-provider/browser trial,
and controlled CLI fault injection. Local verification commands:

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
NODE_OPTIONS=--no-experimental-webstorage npm test
npm run lint
npm run build
```

The backend run at `4f78f55` passed 389 tests with eight existing fork warnings.
Frontend passed 312 tests in 37 files; lint and build passed. The first Node
25 test run failed because experimental global localStorage shadows jsdom;
its failure log is preserved alongside the successful option-based run.
Final-source results and independent review are recorded in the linked PR.

Controlled CLI fault injection is explicitly fixture evidence, not model
inference: fail first PRD, retry, reject old PRD, fail regeneration, retry.
The four Worker outcomes are failed/completed/failed/completed; no template or
false ready event is published, successful retries identify the replacement
artifact, and the final PRD approval remains pending.

Independent PRD Reviewer invocation was absent in the default live workflow.
The manual decision approved product scope only and retained that advisory
gap; it is not independent-review PASS or authority for later gates.
No full StockFlow implementation, two-way implementation parallelism, hosted
delivery, Docker acceptance, or Manager pick/execute/learn/pick-again result
has been demonstrated at this checkpoint.

GitHub CI remains paused under the owner decision of 2026-10-31. No candidate
branch or Actions dispatch was used. Local results do not imply CI passed.
Original input, state snapshots, current artifact paths, Worker transcripts,
failure diagnostics, intervention records and per-run reports remain in the
operator's separate `goodteam-evaluations/stockflow/2026-11-05-run-00{1,2,3}`
directories. Authentication and environment configuration are not uploaded.
