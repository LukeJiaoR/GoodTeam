# Implementation Plan — Review Workbench, Increment 1

> Stage: **planner**. Derived from `docs/plans/review-workbench-prd.md` and
> `docs/product-core-adapter-strategy.md`. This plan decomposes the **first
> shippable increment** into one backend track (codex) and one parallel-safe
> frontend track (agy) that meet at a stable action-log contract.

## Founder decisions in force (PRD §13)

- `actor_ref` for Web is **environment-configurable**: read `GOODTEAM_OPERATOR_REF`,
  fall back to `web:local`.
- Approval audit is stored **only** as an enriched `approval_decided` row in the
  append-only `action-log.jsonl`. No separate `approvals/audit.jsonl` projection.

## The shared contract (where the two tracks meet)

Approval decisions already flow:

`decide_approval()` (`web.py:2282`) → `append_action(..., action="approval_decided")`
(`web.py:104`) → `action-log.jsonl` → `build_action_items()` (`web.py:202`) →
`/api/state` payload `action_items` (`web.py:6821`) → FE `ActionsTab` / approvals UI.

Backend Track A **enriches** the `approval_decided` row and the `build_action_items`
projection with these fields (PRD §6):

```
decision_origin   : "web" | "telegram" | "cli" | "api" | "system"
actor_ref         : non-secret string, e.g. "web:local", "telegram:user:111"
review_mode       : "quick" | "step_by_step" | "request_changes_with_notes"
                    | "reject_confirmed" | "system"
review_session_id : string ("" when absent)
client_context    : object of client identifiers only, never secrets
evidence_snapshot : { artifact_refs[], worker_job_refs[], ci_status,
                      smoke_status, qa_status, screenshot_refs[], debate_refs[],
                      blocker_count, provenance_refs[] }
review_items      : [ { id, title, state: "ok"|"flagged"|"skipped", note } ]
note              : string
```

Frontend Track C renders against this contract and **degrades gracefully** for old
rows that lack the fields (treat missing `decision_origin` as `"web"`/unknown,
missing `evidence_snapshot` as empty). FE does NOT block on BE landing first.

## Track A — Backend Approval Audit Foundation (codex)

**Lane:** `src/goodteam_os/web.py`, `src/goodteam_os/telegram.py`,
`tests/test_web.py`, `tests/test_telegram.py`, `tests/test_telegram_review.py`.
Do NOT touch `frontend/`.

**Scope:**

1. Extend `decide_approval(...)` signature with optional, defaulted audit
   metadata: `decision_origin="web"`, `actor_ref=""`, `review_mode=""`,
   `review_session_id=""`, `client_context=None`, `review_items=None`, `note=""`
   (note already exists). Keep every existing positional/keyword caller working
   — all new params default, so the web `/approve` handler and smoke callers stay
   valid without changes.
2. Resolve `actor_ref`: if caller passes one, use it; else if
   `decision_origin == "web"`, default to `os.getenv("GOODTEAM_OPERATOR_REF")` or
   `"web:local"`; telegram/cli/api supply their own.
3. Add `build_approval_evidence_snapshot(*, state, workspace, project_key,
   debate_ref="")` — failure-safe; pulls artifact_refs from `state.artifacts` +
   `approval_artifact_map`, blocker_count from `state.blockers`, debate_refs from
   `debate_ref` when present, worker_job_refs best-effort for the run, and
   ci/smoke/qa as `"unknown"`. Missing data → `"unknown"`/`[]`, never raises.
4. Enrich the `approval_decided` `append_action(...)` call to carry the new audit
   fields. Extend `append_action` to accept + persist them (additive optional
   kwargs; only write keys that are non-empty so old-row shape is preserved).
5. Extend `build_action_items` to pass the new audit fields through to the FE
   projection (so the contract reaches `/api/state`).
6. The telegram wrapper `decide_approval(**kwargs)` (`telegram.py:976`) already
   forwards kwargs — no change needed there, but the four telegram callsites
   (`telegram.py` ~742/764/806/966) must pass `decision_origin="telegram"`,
   `actor_ref`, `review_mode`, `review_session_id`, and `client_context` from the
   session/callback. (Item-level `review_items` from the session can be a small
   follow-up within this track if time allows; otherwise Track B.)

**TDD / acceptance (the PRD's required regression matrix):**

- Web/default: a web decision writes `decision_origin="web"`,
  `actor_ref="web:local"` (or env), `evidence_snapshot` present.
- `GOODTEAM_OPERATOR_REF` set → web `actor_ref` reflects it.
- Telegram quick approve → `decision_origin="telegram"`, telegram identifiers in
  `client_context`, `review_mode="quick"`.
- Telegram step-by-step → `review_mode="step_by_step"` (+ review_items when
  implemented this pass).
- Stale telegram callback still ignored (no audit row written on ignored).
- Web-to-telegram sync still fires and is auditable.
- Existing callers without metadata still pass (backward compat).
- `build_approval_evidence_snapshot` returns failure-safe `unknown`/`[]` on
  missing data, never raises.

**Verify:**
`PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py tests/test_telegram.py tests/test_telegram_review.py -q`,
then full `pytest tests -q`, `ruff check src/goodteam_os/ tests/`, `git diff --check`.

## Track C-read — Web Review Workbench, read-side slice (agy)

**Lane:** `frontend/src/components/tabs/ApprovalsTab.jsx` (+ a focused new
component under `frontend/src/components/` if extraction helps), and the
`tests/test_web.py` frontend-contract test for the new slice. Do NOT touch
`src/goodteam_os/` Python runtime logic.

**Scope (renders from data already available, degrades for missing fields):**

1. **Approval Audit panel** in the Approvals tab right column: read
   `actionItems` (already in `/api/state`), filter `action === "approval_decided"`
   for the current gate/run, and render an audit history list with:
   - origin badge: Web / Telegram / CLI/API / System (from `decision_origin`,
     default "web"/unknown when absent);
   - `actor_ref`, `review_mode`, timestamp;
   - evidence snapshot summary (artifact/blocker/ci/smoke counts) when present;
   - per-item review notes when `review_items` present.
   All fields optional — old rows without audit metadata must still render
   cleanly.
2. **Evidence matrix** (best-effort, from existing approval review data +
   `state`): a compact grid of artifacts / blockers / debate / CI-smoke-qa
   (show `unknown` when not available), with **missing-evidence callouts**.
3. Preserve current approve / request-changes / reject behavior exactly.
4. (Quick win, agy's lane) fix Approvals Finding #22 from the UX audit: disable
   "Submit Request" when `changesRequestedNote.trim() === ''`.

**TDD / acceptance:**

- Frontend contract test asserts the audit panel + evidence matrix render from
  `actionItems`/state and that origin badges + missing-evidence callouts exist.
- `cd frontend && npm ci && npm run lint && npm run build` pass; `git diff --check`.

**Parallel-safety note:** Track C-read renders from `actionItems` which already
exists. It will show the new audit fields automatically once Track A lands, and
shows graceful fallbacks before then. The two tracks do not share files.

## Track B — Telegram Remote Supervision (read-only status) (codex)

**Status:** Track A already landed the Telegram approval audit metadata + item-level
review persistence at all 4 callsites. The complete mobile approval *decision* flow
(approve / request-changes / reject / step-by-step note capture) already exists. So
Track B's remaining NEW work is the **read-only remote supervision commands** (PRD
§8 / §1c) plus light glue.

**Founder decision (2026-06-10):** build Track B on the existing hand-rolled
`telegram.py` transport. Migrating the transport to a mature library
(pyTelegramBotAPI / telebot — the only sync-fit mature option; aiogram and
python-telegram-bot v21 are async-only and would force an async boundary into the
sync codebase) is scheduled as a SEPARATE, independently-tested transport increment,
not bundled into this feature.

**Lane:** `src/goodteam_os/telegram.py`, `tests/test_telegram.py`. Do NOT touch
`web.py` runtime logic (read from its existing helpers), `frontend/`, or docs.

**Scope — add read-only supervision commands to the message branch of
`process_telegram_update`:**

Currently the message branch only handles `reply_to_message` note capture (telegram.py
~696-760); a plain text message is "ignored: not a reply message". Add a command
branch BEFORE that fallback: detect text commands, enforce the existing
`allowed_users` gate, and reply with compact masked summaries. Scope to the
configured `project_key` (single project, as `tg-poll --project-key` is invoked);
multi-project enumeration is a follow-up.

Commands (slash form; exact syntax may be `/gt status` or `/status`, keep it simple):

- **`/gt status`** — active project, current run id, current node, pending approval
  gate (if any), active worker count, blocker count. Source: `StateStore(workspace)
  .read_project_current(project_key)`, `current_approval_gate(state)`,
  `state.blockers`, `WorkerJobStore(root).list_jobs()` (count running for this run).
- **`/gt approvals`** — pending approval gate(s) for the project as compact card(s)
  with the existing quick review buttons. Reuse `current_approval_gate` +
  `send_approval_notification(...)` / the existing approval card keyboard so the
  buttons drive the SAME audited decision path.
- **`/gt workers`** — recent/active worker jobs: id, role, status, elapsed time, and
  a masked last-error excerpt. Source: `WorkerJobStore(root).list_jobs()` filtered to
  the current run; mask every string with `mask_sensitive_data`.
- **`/gt evidence`** — latest evidence status: CI/smoke/QA/release readiness +
  blocker count. Source: `build_approval_evidence_snapshot(...)` for the snapshot
  shape and `build_dev_loop(root)` for PR/CI/release-readiness/warnings; render
  `unknown` where unavailable.

**Hard rules (PRD §9):**
- Every command re-checks the `allowed_users` gate (reuse the existing check).
- Every outbound text runs through `mask_sensitive_data` (paths, IPs, SSH targets,
  tokens, deploy dirs).
- These commands are READ-ONLY: they must not mutate state. Only the existing
  approval callbacks mutate, and they remain guarded (project/run/gate/pending/
  actionable checks unchanged).
- No arbitrary shell. No server-action bypass.

**Resolving the project root for WorkerJobStore / build_dev_loop:** reuse the same
project-config → runtime-root resolution `build_approval_evidence_snapshot` already
uses (load_project_target + resolve_project_runtime_root), or accept the root the
poll loop already knows. Keep it failure-safe: a missing root yields an "unknown"
summary, never an exception that breaks the poll loop.

**TDD / acceptance:**
- `/gt status` for a project with an active run returns masked summary with current
  node + pending gate + counts; unauthorized user → ignored.
- `/gt workers` masks a worker error containing a path/IP.
- `/gt evidence` returns `unknown` fields when no CI/smoke data, never raises.
- `/gt approvals` surfaces the pending gate with working quick-decision buttons that
  still route through the audited `decide_approval` path.
- A non-command, non-reply message still returns the existing "ignored" result
  (backward compat).
- Poll loop (`poll_telegram_approvals`) still processes approvals and is not broken
  by the new command branch.

**Verify:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_telegram.py
tests/test_telegram_review.py -q`, then full `pytest tests -q`, `ruff check`,
`git diff --check`. Commit on a feature branch, push, open a PR, do not merge.

## Track E — Deeper Workbench: decision recommendation + provenance (BE codex / FE agy)

PRD P1 remaining: "decision recommendation: approve / request changes / reject, with
reasons" and "provenance panel: task contract, worker job, pre-review, CI, land/release
record". This is an advisory layer — it NEVER auto-decides; the human gate is unchanged
(strategy: adapter output is evidence, not authority; no auto-approve on green checks).

### Shared contract

BE attaches a `recommendation` object to each actionable review in the
`build_approval_reviews(...)` payload (web.py:3098), and populates
`evidence_snapshot.provenance_refs` (currently always `[]`) from any
`.goodteam/provenance/pr-*/manifest.json` records that exist.

```
recommendation: {
  "verdict": "approve" | "request_changes" | "reject" | "caution",
  "confidence": "high" | "medium" | "low",
  "blocking_count": <int>,
  "reasons": [ { "kind": "blocker"|"missing_evidence"|"open_objection"|"clean",
                 "text": "<masked, human-readable>" } ]
}
```

FE renders the verdict as an advisory badge + reasons list, degrading cleanly when
`recommendation` is absent (old payloads). Parallel-safe like Track C-read.

### Track E-BE (codex)

**Lane:** `src/goodteam_os/web.py`, `tests/test_web.py`. Do NOT touch frontend/ or docs.

Scope:
1. `build_approval_recommendation(*, state, review, evidence_snapshot, workspace) -> dict`
   — pure, deterministic, failure-safe heuristic (NO LLM). Signals:
   - `state.blockers` non-empty → reason kind `blocker` per blocker; verdict at least
     `request_changes`.
   - missing/unknown evidence in `evidence_snapshot` (blocker_count>0; ci/smoke/qa
     `unknown` for release-stage gates) → reason `missing_evidence`; verdict ≥ `caution`.
   - unresolved committee/debate objections (a message `kind` in {ask, reject} with no
     owner `assert`/`propose` reply `in_reply_to` it) → reason `open_objection`;
     verdict ≥ `caution`.
   - none of the above and artifact content present → verdict `approve`, confidence
     from evidence completeness.
   - All reason text masked with the same masking the Telegram layer uses (paths/IPs/
     secrets) — reuse `mask_sensitive_data` from telegram (lazy import) or a shared
     helper; never raises.
2. Attach `recommendation` to each actionable review in `build_approval_reviews`
   (compute it there using the existing `build_approval_evidence_snapshot`).
3. Add a best-effort provenance reader that scans `<project-root>/.goodteam/provenance/
   pr-*/manifest.json`, returns compact refs (pr number, branch, ci_status, merge sha,
   archived job/contract/pre-review paths), and populate `evidence_snapshot
   .provenance_refs` from it. Missing dir → `[]`, never raises.
4. TDD: recommendation returns `request_changes` when a blocker exists; `caution` on an
   unresolved objection; `approve` on a clean artifact; failure-safe on missing data;
   provenance reader returns `[]` when no manifests, parses a sample manifest when present.

Verify: `pytest tests/test_web.py -q -k 'recommendation or provenance or approval'`,
then full `pytest tests -q`, ruff, `git diff --check`. Branch, push, PR, no merge.

### Track E-FE (agy)

**Lane:** `frontend/src/components/tabs/ApprovalsTab.jsx` (+ optionally one new
component), the frontend-contract test in `tests/test_web.py`. ProjectTabRouter already
threads `actionItems`; review data already flows. Do NOT touch src/goodteam_os runtime.

Scope:
1. In the actionable review panel, render the advisory **recommendation badge**
   (approve = success tone, request_changes/caution = warning, reject = danger) with
   confidence and `blocking_count`, plus a **reasons list** grouped by kind. Make it
   visually clearly ADVISORY ("Recommendation — you decide"), not an auto-decision.
2. Render a compact **provenance** summary from `evidence_snapshot.provenance_refs`
   (pr / branch / ci / merge sha / archived artifacts) when present.
3. Degrade cleanly when `recommendation` / `provenance_refs` are absent.
4. Keep approve/request-changes/reject behavior exactly as-is.
5. Semantic theme utilities only; `text-2xs` floor; no raw hex.
6. Frontend-contract test asserting the recommendation badge + reasons + provenance
   render from the review/evidence payload and degrade when absent.

Verify: `cd frontend && npm run lint && npm run build`; `git diff --check`; the
approval contract test. Branch, push, PR, no merge. Hardened lane discipline (git add
only whitelisted, no finishing skills, report in chat).

## Sequencing

1. Dispatch Track A (codex) and Track C-read (agy) in parallel, isolated worktrees.
   — DONE 2026-06-10: PR #195 (BE) + #196 (FE) both merged into main.
2. Track B Telegram remote supervision (this increment) on the existing transport.
3. Separate, independently-tested transport migration to telebot (sync) if/when the
   hand-rolled surface hurts — not bundled with a feature.
4. Deeper workbench (decision recommendation engine, provenance panel) and product
   narrative update (PRD Track D) after.

## Out of scope this increment

- Telegram remote-supervision status commands (`/gt status` etc.) — Track B.
- Decision recommendation engine — later workbench pass.
- Product narrative doc update (PRD Track D) — after the audit contract is stable.
- Any i18n expansion beyond copy touched here.
