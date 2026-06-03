# Model / agent scorecard — bug attribution for task routing

> Purpose: record the **root-cause owner** of each bug/defect so we can evaluate which
> model/agent to route which kind of task to (and where to invest — spec precision vs model
> choice). **Attribute to ROOT CAUSE, not the last hand that touched it.** A model that
> faithfully implements an under-specified spec did not "own" the bug — the spec did. Keep
> this honest (incl. self-attribution to Claude) or it gives the wrong routing signal.

## Legend — root-cause owner categories
- **spec(Claude)** — the task/PRD I wrote was wrong/under-specified; the implementer did what
  was asked. *Fix: better specs, not a different model.*
- **impl(<model>)** — the implementer produced something the spec did NOT ask for / got wrong
  despite a clear spec. *This is the real model-quality signal.*
- **process(Claude)** — orchestration/workflow mistake by me (rebase, dispatch hygiene).
- **env / tooling** — auth/model availability / script harness; not a model-quality issue.
- **pre-existing** — defect already in the codebase, not introduced this work.
- **caught-by**: who found it (pre-review / CI / Claude-review / agy / runtime).

## Session ledger (2026-06-02 → 06-03)

| # | Bug / issue | Sev | Root-cause owner | Implementer | Caught by | Notes |
|---|-------------|-----|------------------|-------------|-----------|-------|
| 1 | `append_message` identity excluded `gate` → cross-gate same-role votes deduped & dropped (#43; latent in M1a #35) | High | **spec(Claude)** — my M1a PRD §5.1 listed identity fields without `gate` | codex gpt-5.5 (faithful to spec) | pre-review (codex code_review) | Fixed: gate added to identity. NOT a model defect. |
| 2 | G1 v1 transition table fields decorative + self-referential (#36) | Med | **spec(Claude)** — v1 spec didn't require the driver to *consume* the fields | codex gpt-5.5 | Claude-review | v2 spec ("authoritative, consumed") → codex produced it correctly |
| 3 | M1c FE lint error `react-hooks/set-state-in-effect` (#38) | Med | **spec(Claude)** — acceptance said `npm run build`, omitted `npm run lint` | codex gpt-5.5 | CI (frontend lint) | re-specced w/ lint → codex fixed via correct derived-state refactor |
| 4 | Stray `result.md` committed into the branch (#35) | Low | **spec(Claude)** — early specs didn't say "don't commit result.md" | codex gpt-5.5 | pre-review | later specs added the rule; no recurrence |
| 5 | Stale-base "reversion" risk — branch cut before another land, diff looks like it reverts (#36, #42, #43, #44) | Med | **process(Claude)** — didn't rebase onto current main before land | n/a | pre-review + Claude | mitigated: ALWAYS rebase feature branch onto origin/main pre-land |
| 6 | Launched pre-review with bare `&` → reaped mid-run + stale worktree registration broke re-run (twice) | Low | **process(Claude)** | n/a | Claude | rule: always run_in_background, never `&` |
| 7 | Dispatch model `gpt-5.3-codex` rejected (ChatGPT-account auth) — blocked all dispatch | High (blocker) | **env** — codex CLI auth/model availability | n/a | dispatch error / Claude | resolved: model = local config (GOODTEAM_CODEX_MODEL=gpt-5.5) |
| 8 | dispatch `--verify .venv/bin/python` 127s in fresh worktrees (no venv) → false `needs_human` | Low | **tooling** — dispatch-track.sh verify path | n/a | Claude | backlog; I re-confirm green with the main venv |
| 9 | Memory-event detail unreachable via UI nav (no event index; drill-down only on mem: ids in text) | Med | **pre-existing** design gap (not introduced this work) | n/a | agy UI-test | backlog |
| 10 | `default_debate_dispatch` placeholder returned `[]` (M1b #37) | n/a (scoped) | **not a bug** — intentional placeholder, flagged; real-wiring landed in #39 | codex gpt-5.5 | Claude (live smoke) | — |
| 11 | Memory-events search: stale debounced response can overwrite newer results (no per-request guard) (#47) | Med | **impl(codex gpt-5.5)** — race-safe handling of debounced async search is standard FE competence; spec said "debounced" but a faithful debounce still needs response-ordering guards | codex gpt-5.5 | pre-review (codex code_review) | FIRST impl-attributed defect this program. Fixed via per-role request guard. Caught by independent pre-review, not Claude-review. |
| 12 | Workflow-graph endpoint emitted a non-CLOSED graph (edge endpoints didn't resolve to node names) (#49) | Med | **spec(Claude)** — my cross-track viz contract didn't pin the node/edge namespace or a closure invariant | codex gpt-5.5 | pre-review | Fixed: union nodes ∪ gates ∪ all edge endpoints + closure test. Join-key omission #1. |
| 13 | Workflow-graph failure edges originated at the gate, not the executing node (#49) | Med | **spec(Claude)** — contract didn't say WHERE failure edges live (node vs gate); node/gate name duality | codex gpt-5.5 | pre-review | Fixed: from=node.name (matches runtime error_next_for_node). Join-key omission #2. |
| 14 | D2a committee messages omitted `id` → FE could not resolve `in_reply_to`, replies didn't thread (#52/#53) | Med | **spec(Claude)** — my committee contract listed message fields but omitted the `id` the `in_reply_to` reference resolves against | codex gpt-5.5 | pre-review | Fixed (#53): project `id` + threading test. Join-key omission #3 — same root cause as #12/#13. |
| 15 | agy modified `docs/current-progress.md` (out of lane) + did NOT apply the requested App.jsx fix | Med | **impl/process(agy)** — agy's built-in finishing skill auto-edits outside the whitelist (known CLAUDE.md warning) | agy (antigravity) | Claude lane-integrity check | Discarded the out-of-lane change; re-routed the fix to codex. agy = greenfield-UI + read-only-test only, NOT precise lane-bound fixes. |

## Read-out for model routing (the point of this doc)
- **codex (gpt-5.5), implementer** — across this program it largely produced what the spec
  asked; **1 `impl(model)` defect in ~25 tracks** (row 11: a stale-response race in
  debounced async search — Med, caught by pre-review), every other code defect traced to a
  spec/process root cause. When re-specced precisely (G1 v2, M1c lint fix, dedup fix) it
  produced correct code first try. **Signal: reliable for well-specified implementation;
  output quality tracks spec precision, not model ceiling — but it is NOT defect-free on
  subtle async/concurrency correctness, so the independent pre-review gate stays load-bearing**
  → a *cheaper* implementer (e.g. the parked opencode/ds4pro bake-off) could plausibly do the
  same low/medium-risk tracks IF specs stay tight and pre-review/CI gate. Verify empirically.
- **agy, UI research + UI testing** — strong on greenfield UI + read-only Playwright (built the
  workflow-graph view well: closure-robust + race-guarded; ran clean read-only smokes). BUT on a
  precise lane-bound *code fix* (row 15) its finishing skill edited `current-progress.md` out of
  lane AND skipped the actual fix. **Signal: agy for greenfield UI + read-only test ONLY; route
  precise lane-bound fixes to codex.** (Always run the lane-integrity `git status` check on agy
  output — it caught this.)
- **Parallel-orchestration discipline (Claude) — the dominant lesson of the DAG wave.** Three
  Med defects (rows 12/13/14) had the SAME root cause: a cross-track contract that omitted the
  **join/reference key** — closure (edge→node), failure-edge origin (node vs gate), reply→parent
  (`in_reply_to`→`id`). When BE/FE/track-A/track-B build in parallel against a contract, each piece
  was locally correct but didn't COMPOSE. **Rule: any cross-track contract with references between
  items MUST specify the identifier the reference resolves against + a testable composability
  invariant** (e.g. "every edge endpoint is a node name", "every `in_reply_to` resolves to a
  projected `id`"). Without that, aggressive parallelism's integration cost eats the parallelism
  gain. (D2a's committee contract still missed it once — id — but the genuinely-independent tracks
  with tight contracts landed first-try; the cost concentrated in reference-bearing contracts.)
- **Claude (me)** — owner of the majority of root causes (spec under-specification +
  orchestration). **Highest-leverage investment is spec precision + workflow hygiene, not
  swapping the implementer.** Spec checklist now: acceptance must include `npm run lint` for FE;
  "don't commit result.md/.goodteam"; identity/dedup fields must be enumerated deliberately;
  state explicitly when a table/field must be *consumed*; always rebase before land.
- **pre-review (codex code_review), independent gate** — caught the 1 High (cross-gate dedup),
  the stray result.md, and stale-base risks my own review passed. **Signal: keep it as a hard
  gate; it pays for itself.**

## How to use going forward
Append one row per new bug with its ROOT-CAUSE owner + caught-by. Over time:
`impl(<model>)` rows = that model's real defect rate (route accordingly); a pile of
`spec(Claude)` rows = invest in specs, not model swaps. Re-evaluate the implementer model
(incl. any cheap-tier bake-off) against the `impl(...)` column only.
