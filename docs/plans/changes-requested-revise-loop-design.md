# Design — changes_requested → feedback-aware revise loop

Status: **founder-gated 2026-06-06** ("出设计→派工"). Builds on the PRD authoring loop (#86/#87).
Role chain: architect (this) → backend (codex) + frontend (agy) → review → pre-review → land.

## Problem (verified)
A gate decision of `changes_requested` is a **dead-park**: `decide_approval` (web.py ~1328) has
branches only for `approved` (advance), `takeover+approved` (generate PRD), and `prd/rfc+rejected`
(auto-regenerate from scratch). There is **no `changes_requested` branch**, so the run just sits at
`revise_prd` / `revise_rfc` with `approvals[gate]=changes_requested` — nothing regenerates, and the
reviewer's "what to change" is never captured or fed to a worker. (Contrast: `rejected` auto-
regenerates the artifact and returns to await-approval.)

## Goal
Make `changes_requested` a closed loop: **capture the reviewer's change note → trigger a
feedback-aware revision of the artifact (reusing the existing worker path) → return to
`await_<gate>_approval`** for re-review. Symmetric with `rejected`, but (a) gated on
changes_requested and (b) the worker is told exactly what to change.

## Decision (founder framing: "触发生成新的 PRD")
Auto-revise on changes_requested (like `rejected`), but **feedback-aware**: the regenerated artifact
incorporates the reviewer note, then goes back to `await_<gate>_approval` (the human re-reviews the
revised artifact at the gate — the human gate is preserved). Scope: `prd` and `rfc` gates (the two
that already have a regenerate path). Other gates unchanged for now.

## Backend (src/goodteam_os/graph.py + web.py + state.py)
1. **Capture the note.** `/api/approve` accepts an optional `note` (string) in the body. When the
   decision is `changes_requested`, persist it on state: add `change_requests: dict[str, str]` to
   `AgileDevState` (state.py, `field(default_factory=dict)` + setdefault on load) and set
   `state.change_requests[gate] = note.strip()`.
2. **New decide_approval branch** (web.py, mirror the rejected branch): 
   ```
   elif approval in {"prd", "rfc"} and approval_decision == ApprovalDecision.CHANGES_REQUESTED:
       state.change_requests[approval] = note.strip()        # capture
       state.approvals[approval] = ApprovalDecision.PENDING   # so gate_next_node → await
       regenerate_before_node = state.next_node
       if approval == "prd":
           state = run_until_next_gate(state=..., project_config=...)
       else:
           state = regenerate_rfc_review_gate(state=..., project_config=...)
       append_action(... action=f"{approval}_revised", ... )  # record, include note context
   ```
   After regeneration `gate_next_node` (with approvals reset to pending) yields
   `await_<gate>_approval`. Clear `state.change_requests[gate]` after the worker has consumed it (or
   keep it on the action log for provenance — keep the action-log record; clear the live field).
3. **Feed the note to the worker.** `prd_worker_task(state)` (used by `run_until_next_gate`) and the
   RFC equivalent must append the reviewer feedback when `state.change_requests.get(gate)` is set:
   > "A reviewer requested changes to the current {ARTIFACT}. Revise it to ADDRESS this feedback,
   >  preserving the parts that are fine: <note>"
   So the regeneration is a targeted revision, not a blind from-scratch rewrite.
4. Keep response shape + existing approved/rejected branches unchanged. Tests: changes_requested with
   a note → artifact regenerated, `next_node == await_<gate>_approval`, `approvals[gate]` pending,
   the note appears in the worker task_text (assert via the fake-worker `seen["task_text"]`), and the
   action log records the revision.

## Frontend (frontend/src/App.jsx)
1. **Request Changes captures a note.** In the approvals panel, the "Request Changes" button
   (~line 4492) reveals/uses a **feedback textarea** ("What needs to change?"). On submit, POST
   `/api/approve { project_key, approval, decision: 'changes_requested', note }`. Require non-empty
   note (or allow empty but encourage). Keep dark-mode readable (text-zinc-900 dark:text-zinc-100).
2. After submit, the existing state refresh shows the run back at `await_<gate>_approval` with the
   revised artifact. Optionally surface the submitted note in the gate/action history.

## Acceptance / smoke
- BE: `pytest` + `ruff` green; new tests as above.
- FE: `npm run lint` + `npm run build` green.
- Live (VPSSSH, which is currently parked at prd changes_requested): Request Changes with a note →
  a revised PRD is generated → gate returns to await_prd_approval → re-approve works. The Workflow
  Graph shows prd back to pending/await (not stuck orange).

## Lane split (file-disjoint, parallel)
- **BE → codex:** `graph.py` + `web.py` + `state.py` (+ tests). 
- **FE → agy:** `frontend/src/App.jsx` only.
Contract pin: `/api/approve` gains optional `note`; on `changes_requested` the BE regenerates and
returns to `await_<gate>_approval`. FE sends `note`; no other shape change.
