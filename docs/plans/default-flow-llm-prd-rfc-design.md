# Default-flow PRD/RFC via LLM (with template fallback) — architect design

> Stage: architect. Founder priority: make the NORMAL workflow generate PRD/RFC via the LLM
> worker (today only the on-demand /generate-prd /generate-rfc buttons do). Grounded in an
> OFFLOADED read-only research survey (codex, 2026-06-04, cited). Touches the core start/approve
> flow → SUPERVISED. **Hard constraint: preserve zero-credential onboarding** (vision §5.1: first
> run reaches a gate with NO model creds because PRD is deterministic + default backend is `fake`).

## Today (research-confirmed)
- Normal PRD: `run_until_next_gate` → `render_prd_markdown` (template) — sync, from `/start` +
  takeover-approval (graph.py:1397-1411).
- Normal RFC: `run_architecture_until_gate` → `render_downstream_artifact_markdown("rfc")`
  (template) — sync, from PRD-approval via `advance_approved_workflow` (graph.py:738-753).
- On-demand LLM: `generate_prd`/`generate_rfc` (graph.py:1431/1494) dispatch a `product_requirement`
  / `architect` worker, write `result.md`→artifact, and RAISE on worker failure. They take
  `(workspace, project_key)` and reload state — NOT directly reusable from the in-memory normal flow.
- `/start` and `/approve` run the workflow SYNCHRONOUSLY in the POST handler (web.py:5628-5661);
  a worker run blocks the request (CLI worker `communicate(timeout=1800)`).
- Fresh zero-cred config defaults backend to `fake` (projects.py:25-59).

## Design

### 1. Reusable in-memory generator helper (extract, don't duplicate)
Extract the worker-dispatch core of `generate_prd`/`generate_rfc` into a helper shaped for the
normal flow's in-memory inputs, e.g.
`generate_artifact_via_worker(*, state, workspace, project_config, role, artifact_key, task_text) -> str | None`
that: loads target + runtime root from the EXISTING `project_config`, resolves adapter/model/context
(`resolve_role_adapter`/`worker_model_for_role`/`worker_context`), runs the worker, and returns the
worker `result.md` content on success or `None` on failure. `generate_prd`/`generate_rfc` refactor
to call it (their on-demand semantics — raise on failure — preserved at their layer).

### 2. Readiness-gated LLM-vs-template (the fallback that keeps onboarding + tests intact)
In `run_until_next_gate` (PRD) and `run_architecture_until_gate` (RFC):
- Resolve the role adapter; if it is a **built-in** (`fake`/`noop`/`human`) OR
  `worker_backend_doctor(adapter)` is not `ready` → render the **template** (today's behavior).
- Else (real, ready backend) → call the helper; **on success** use the LLM output; **on worker
  FAILURE → fall back to the template** (graceful — a model hiccup must NOT break `/start`; this is
  the KEY difference from the on-demand path which raises).
This gives: zero-cred/`fake`/test → template (unchanged); real model configured → LLM PRD/RFC;
model failure → template (never blocks the flow).

### 3. Latency — recommend SYNC (localhost-appropriate), with FE loading states
`/start` and `/approve` block on the worker (~30-90s for a single PRD/RFC generation; not the 1800s
coding-worker case). For a **local-first** dashboard (localhost, no proxy) a minute-scale request
with an FE spinner is acceptable, and it AVOIDS re-living SA-2's async-in-server complexity (5
rounds). **Recommendation: sync first.** FE adds a "generating PRD/RFC…" loading state on
start/approve (the requests are no longer instant). Async (background + a `generating` gate state)
is a future option if the wait proves painful — deliberately deferred, not built now.

## Test regression surface (small, by design)
- Renderer UNIT tests (`render_prd_markdown`/`render_..._rfc` directly) — unaffected (renderer stays,
  used for fallback).
- FLOW tests that assert template content (test_graph_sample 123-177/399-449/499-518; test_web
  1914-1980) run with the `fake` backend → fall back to template → **stay green** (the elegant part).
- ADD: real-backend → LLM path in the normal flow; worker-FAILURE → template fallback (graceful,
  does not raise); zero-cred/fake → template.

## Risk / supervised
Medium: touches the core start/approve flow + adds worker dispatch to it. The readiness+failure
fallback keeps it behavior-preserving for fake/failure (today's path), so the blast radius is
"real-backend installs get LLM PRDs." Supervised gate + the existing flow tests as the net.

## Decomposition
- **BE** (codex): helper extraction + readiness gating + graceful template fallback in
  run_until_next_gate/run_architecture_until_gate + tests. (graph.py + tests)
- **FE** (agy): "generating…" loading states on start/approve so a slow (real-backend) request
  isn't a silent hang. (App.jsx) — lower priority; can follow BE.

## Recommendation
Build the BE (helper + readiness-gated LLM-with-graceful-template-fallback, SYNC) first; FE
loading-state follow-up. Defer async. Keep zero-cred onboarding via the fake→template fallback.
