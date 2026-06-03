# G3 — triage classify→route + failure capture (architect design)

> Stage: architect. Moat #1 §6.2 "triage agent + self-correction" + §7 failure library. Grounded
> in an OFFLOADED read-only research survey (codex, 2026-06-03, cited). Builds on D1 (declarative
> failure edges + retry, landed #48). **The routing change touches the workflow failure path →
> SUPERVISED gate; this doc is the autonomous-safe deliverable.**

## What already exists (research-confirmed — build ON, don't rebuild)
- A real **deterministic classifier** `classify_worker_failure` (workers.py:60) sets `failure_class`
  ∈ {auth, rate_limit, network, timeout, stream_disconnect, scaffold, unknown}; transient set =
  {auth, rate_limit, network, timeout, stream_disconnect}.
- **D1 already routes on it**: `run_worker_job_with_retry` retries when `failure_class ∈
  retry_on_classes` (graph.py:1000-1007), else failures hit `error_next_for_node("planner")`
  (graph.py:773-775) = always `"blocked"` today.
- A SECOND coarse classifier `classify_smoke_failure` (smoke.py:201) exists but is NOT wired to
  worker `failure_class`/routing.
- Memory has the **WAL+projection+index** pattern (per-project `events.jsonl`, memory.py) and a
  **read-only cross-project fan-out** (#45) — the models for any failure library.

## What's missing (the G3 surface)
1. Routing is binary: **retry (D1) or dead-end `blocked`**. No per-cause disposition (escalate vs
   block vs route-back).
2. The classifier lacks the richer §6.2 labels (`infra`, `code_bug`, `flaky`, `ambiguous_spec`).
3. **Failed workers don't write to memory** — `_write_back_memory` runs only on `completed`
   (workers.py:533-536). Failures vanish (no capture → no library possible).
4. **No global store** for a cross-project failure-pattern library (all memory is per-project;
   cross-project is read-only fan-out). The §7 `~/.goodteam/global/failure_patterns/` primitive
   does not exist.

## Increments (scoped; honest about risk)

### G3a — declarative classify→route dispositions at the failure point  (the core; SUPERVISED build)
At the concentration point (graph.py:773, after `run_worker_job_with_retry`, before routing),
consult a **declarative `failure_class → disposition` table** instead of always routing to
`"blocked"`. Dispositions (start deterministic, no LLM):
- `retry` — already handled by D1 (transient classes); unchanged.
- `block` — default / `unknown` / `code_bug` / `scaffold`: dead-end as today (behavior-preserving
  default) but record WHY (the class) on the blocker.
- `escalate_human` — `auth` / `network` / `infra` / `rate_limit` after retry exhaustion: block +
  an explicit "needs human: <reason-by-class>" blocker/flag so the human sees the cause, not just
  "blocked".
Record the triage decision (class → disposition + reason) on the action-log + blockers. Keep the
default = today's `blocked` so existing transition tests stay green. Extend the classifier taxonomy
minimally only if a class is needed for a disposition. **Deterministic + table-driven + testable.**
**Out of G3a (deferred):** `route_back_to_spec` (re-opening a passed RFC/PRD gate — changes gate
semantics) and `synthesize_expert` (G5) — both change role/gate behavior; defer.

### G3b — capture failures into memory  (additive, low-risk; do with/after G3a)
On worker failure, write a structured **failure event** to memory (the existing
`append_memory_event` WAL — a `failure`-kind event: `failure_class`, node, summary signature,
attempt count, disposition taken). Closes the gap that failures vanish. This is the DATA foundation
a library needs. Additive, reuses the landed WAL pattern, low risk.

### G3c — failure-pattern library (cross-project, hit-count)  (DEFER — new global primitive)
The §7 compounding payoff: a store keyed by failure SIGNATURE → classifier hint → recommended
action → hit count, readable across projects, applied on match. Research confirms **no global
store exists**; this needs a new global path resolver + mutable schema (hit-count) + write/update +
retrieval — a genuinely new primitive beyond the read-only cross-project fan-out. **Defer until
G3b has accumulated real failure events** (a library with nothing to project is premature) and give
it its own design + gate. Mirrors the "own the ledger" caution: a global MUTABLE cross-project
store is a bigger commitment than the per-project WAL + read-only fan-out we have.

### LLM triage agent  (DEFER — deterministic table first)
Vision frames triage as an *agent*. But a deterministic classifier already covers the known
classes. **Dissent (same shape as the D2b call):** ship the deterministic classify→route table
(G3a) first — testable, no model call, covers known causes — and add an LLM triage agent only for
the `unknown`/ambiguous long tail later, measured against the §12 hit-rate metric. Don't run a
model to route failures a table already handles.

## Recommendation
1. **G3a (deterministic classify→route dispositions)** — the core, highest-value: turns "every
   failure dead-ends to blocked" into "failures route + escalate by cause." Supervised build
   (touches the failure path) but table-driven + behavior-preserving-by-default + test-covered.
2. **G3b (failure capture into memory)** — additive, low-risk; pairs naturally with G3a and is the
   prerequisite for any library.
3. **Defer G3c (global failure library) + the LLM triage agent** until capture accumulates and with
   their own gates.

## Open questions (resolve in G3a/G3b)
- Disposition table location: extend `workflow_table.py` (a `FAILURE_DISPOSITIONS` map class→
  disposition) consistent with D1, vs per-node. Recommend a global class→disposition map (failures
  are cause-driven, not node-driven) + optional per-node override.
- `escalate_human` surfacing: a new blocker prefix / state flag the UI already renders? (the
  Blocker Box exists). Confirm it shows the reason.
- G3b failure event: which role's memory (the failing worker's role) + a distinct `kind:"failure"`
  so it's filterable; ensure it does NOT pollute the retrieval packets that feed normal work
  (scope/horizon choice).
