# PRD + architecture — generate the PRD with a real LLM (not a template)

> Stage: product_requirement + architect. PRD/RFC gate — review before per-track
> handoffs. Direction (founder): "PRD via real LLM — we need this."

## 1. Intent (why)

Today `render_prd_markdown` (graph.py:145) is pure string templating: a hardcoded
CONTENTOPS branch + a generic fill-in-the-blanks template that emits boilerplate
("Product Roadmap PRD / Workflow Request / No project context files were loaded /
Define the next v1 product slice…"). It does NOT reason about the user's idea, so
the PRD a user sees at the gate is structure with no real product thinking — the
`product_requirement` role is mapped to gpt-5.5 in models.yaml but never actually
invoked. For GoodTeam to be a usable product, the first artifact a user reviews
must be a real, idea-specific PRD. The machinery to do this already exists
(impl/qa already run real LLM workers via WorkerJobStore); the PRD step just never
used it.

## 2. Users & value

- **The operator (primary):** at the PRD gate, sees a genuine PRD derived from
  their idea + project context (users, value, scope, non-goals, acceptance) — good
  enough to approve, request-changes, or refine — instead of a template they must
  mentally fill in.
- **Design partners:** "describe an idea → get a real PRD" is the first proof the
  team-of-agents actually thinks, not just scaffolds.

## 3. User-facing acceptance

- After `start`, the PRD artifact still appears immediately as a **template
  placeholder** (fast, no credentials, no model spend) — start is NOT slowed.
- The dashboard PRD view has an explicit **"Generate PRD"** action. Triggering it
  runs the `product_requirement` LLM worker (gpt-5.5) against the run's idea +
  project context and **replaces** `prd.md` with the generated PRD.
- The generated PRD is idea-specific (mentions the actual idea, real users/scope/
  non-goals/acceptance), NOT the CONTENTOPS template and NOT the generic boilerplate.
- Re-triggering regenerates (the user can edit the idea / re-run before approving).
- Graceful: if the worker backend isn't ready/fails, the UI shows why (reuse triage
  failure_class surfacing) and the placeholder PRD remains — the gate is never
  bricked.
- The PRD gate approval flow is unchanged; it just now usually approves a real PRD.

## 4. Scope

**In:** an on-demand `product_requirement` worker that generates `prd.md` from
idea+context; a `POST /generate-prd` endpoint + dashboard "Generate PRD" button;
the worker task/prompt for the PR-writing role; keep the template as the initial
placeholder + as fallback when no LLM runs.

**Out (this round):** auto-generating on `start` (decided against — keep start
fast/free; explicit trigger per founder's choice); LLM-generating RFC/other
artifacts (same pattern can extend later); editing the PRD inline in the UI.

## 5. Architecture / key decisions (decision record)

1. **Explicit trigger, not on `start`.** `start` keeps emitting the template
   placeholder (instant, no spend); a `POST /generate-prd` action runs the LLM.
   (Per founder; rejected: synchronous-on-start = slow/costly every run; rejected:
   silent template = the status quo we're fixing.)
2. **Reuse the worker path, don't invent one.** Generate via
   `WorkerJobStore.create_job(role="product_requirement", adapter=resolve_role_adapter(
   target,"product_requirement"), model=worker_model_for_role(state,"product_requirement"))`
   + `run_job`, exactly like run_implementation_workers. The worker writes the PRD;
   we copy its result into `prd.md`. This inherits triage retry + memory write-back
   for free. (Rejected: a bespoke LLM call in web.py — duplicates the worker plumbing,
   misses triage/memory.)
3. **Template stays as placeholder + fallback.** `render_prd_markdown` remains the
   initial artifact and the no-LLM fallback, so CI/tests/no-creds paths are
   unaffected and the gate can't brick. The CONTENTOPS hardcoded branch in
   render_prd_markdown should be dropped/neutralized while here (it's the same
   idea-bleed class as the worker-task hardcode we fixed in #14) — the LLM path
   makes it obsolete; confirm at gate whether to remove it now or leave the
   `_is_contentops_request` detector for the real CONTENTOPS project only.
4. **The worker prompt is idea-first.** task = "Write a real PRD for: {state.idea}.
   Cover intent, users & value, V1 scope, non-goals, acceptance criteria, risks —
   grounded in the project context provided. Output PRD markdown only." context =
   the existing project context packet. Zero hardcoded product wording.
5. **`/generate-prd` mirrors `/retry`.** New POST path in the web.py whitelist
   (next to /retry), `generate_prd(workspace, project_key)` in web.py that resolves
   the run, dispatches the worker, writes prd.md, returns updated state.

## 6. Verified ground truth (for the implementer)

- `render_prd_markdown(state, project_config)` (graph.py:145) = template; called at
  graph.py:~1296 to write prd.md at the takeover→product_requirement step.
- LLM worker pattern: `run_implementation_workers` (graph.py:991) uses
  `WorkerJobStore(runtime_root).create_job(...role/adapter/model/task/context/
  constraints/expected_artifacts...)` then `run_job(id)`; result.md holds the
  worker output; runtime_root = `resolve_project_runtime_root(repos)`.
- `product_requirement` is in models.yaml (primary `codex:gpt-5.5`, mode
  spec-writing); `worker_model_for_role(state,"product_requirement")` resolves it;
  `resolve_role_adapter(target,"product_requirement")` picks the backend.
- POST endpoints: web.py whitelist at ~line 5012 (`/start /approve /artifact
  /retry /terminate /project-delete /open-path /project-config`); `/retry` →
  `retry_blocked_workflow(workspace, project_key)` is the closest template for a new
  `/generate-prd` action. Frontend buttons fetch these (App.jsx).
- Triage (just landed): worker failures carry failure_class/disposition — reuse for
  the graceful-failure UI message.

## 7. Tracks (issued after this gate)
- **A. backend (Codex):** `generate_prd(workspace, project_key)` — dispatch the
  product_requirement worker (idea-first task), write its result to prd.md, return
  state; `POST /generate-prd` endpoint; idea-first worker task builder; neutralize
  the CONTENTOPS branch in render_prd_markdown; tests (worker dispatched with the
  idea, prd.md replaced, fallback intact, bad-project 400). Deterministic tests use
  a fake worker — no real model in CI.
- **B. frontend (Codex):** a "Generate PRD" button on the PRD artifact/approvals
  view that POSTs /generate-prd, shows loading + the refreshed PRD, and a graceful
  error (failure_class) state. App.jsx only.
- **C. QA:** scripted (fake-worker prd-replace + fallback) in CI; a manual real-LLM
  generation check on a dev box (spends tokens) — never in CI.

## 8. Open questions (resolve at this gate)
- **Remove the CONTENTOPS hardcoded PRD branch now?** Recommend yes — it's the same
  idea-bleed risk as #14; the LLM path supersedes it. Keep `_is_contentops_request`
  only if there's still a reason to special-case the real CONTENTOPS project.
- **Where does the "Generate PRD" button live** — Approvals tab (next to the PRD
  review) or an Artifacts/PRD view? Recommend the Approvals/PRD-review area where
  the user is already deciding on the PRD.
- **Worker timeout/UX for the ~tens-of-seconds gen** — reuse the same
  fire-then-poll pattern as approvals? Recommend yes (POST returns when done; UI
  shows a spinner; triage retry covers transient failures).
