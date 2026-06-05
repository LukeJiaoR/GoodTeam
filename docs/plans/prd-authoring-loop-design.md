# PRD Authoring Loop — design (#1 edit→deepen→re-review + #2 stages→Agile Board tasks)

Status: **draft, pre founder-gate** (2026-06-06). Two tightly-related backlog items in the same
human-in-the-loop authoring area, designed together; can ship as two file-disjoint tracks.

## Problem
1. Editing a PRD and saving (`/api/artifact` POST → `save_artifact`) only writes the file +
   refetches state — **no LLM follow-up**. The founder wants: human edits the PRD → LLM reads the
   edit, *deepens/refines* it (not restart-from-idea) → routes a result back for review.
2. The Agile Board reads a per-project task ledger, but the REAL workflow never creates tasks
   (`ensure_project_task_ledger` writes an EMPTY ledger; `create_task` is called only in smoke.py).
   So writing a PRD (and every stage) leaves **no board trace**.

## Existing seams (reuse, don't reinvent)
- `generate_artifact_via_worker(state, workspace, project_config, role, artifact_key, task_text, …)`
  (graph.py:1695) — dispatches a role worker, runs the job, returns the produced markdown. The
  backbone for refine.
- `generate_prd` (graph.py:1746) + `/generate-prd` POST already regenerate the PRD — but from
  `state.idea` via `prd_worker_task`, **ignoring the human's hand-edits**. Refine needs to feed the
  CURRENT edited artifact instead.
- `save_artifact` (`/api/artifact` POST) — current write path.
- `ArtifactStore.write_markdown(run_id, key, filename, content)` — overwrites the artifact file.
- `LocalTaskStore.create_task(title, description, role, …)` / `update_task(id, status=…)` —
  statuses in `TASK_STATUSES`, board columns via `TASK_BOARD_STATUS_MAP`.
- Approval/review surface (`build_approval_reviews`, the Markdown Editor + preview) — where a
  re-review/diff should appear.

---

## Part 1 — PRD edit → LLM deepen → re-review

### Flow
1. User edits the PRD in the Markdown Editor and clicks **"Refine with AI"** (explicit action, NOT
   auto-on-save — see gate Q1). FE POSTs `/api/refine-artifact { project_key, artifact, content }`
   with the current edited content.
2. BE `refine_artifact`: load state + project_config; dispatch the artifact's owning role
   (`product_requirement` for prd) via `generate_artifact_via_worker`, with a **deepen task**:
   - task_text = "Here is the current human-edited {ARTIFACT}. DEEPEN and refine it — preserve the
     author's intent, structure, and decisions; do NOT restart from the idea or discard their edits.
     Fill gaps, sharpen acceptance criteria, surface risks/open questions, tighten scope. Return the
     improved {ARTIFACT} as markdown only." + the current edited content embedded as the base.
   - context = `worker_context(state)` (same grounding as generate).
3. Write the refined output as a **new revision**, NOT an overwrite (see gate Q2): keep
   `prd.md` (current) + `prd.refined.md` (proposed), and record both refs on state.
4. Surface a **review**: the approvals/editor panel shows the proposed refinement with a diff vs the
   human's version + Accept / Discard. Accept → promote refined → `prd.md` (becomes current) +
   move the PRD task to review/done; Discard → keep the human version.

### Backend
- New `refine_artifact(*, workspace, project_key, artifact_key, content)` in graph.py: persists the
  incoming edited content first (so the human edit isn't lost), then runs the deepen worker, writes
  the proposed revision, returns `{ proposed_path, base_path, diff_summary }`.
- New `/api/refine-artifact` POST in web.py (same-origin write allowlist) + `/api/accept-refinement`
  POST (promote proposed→current).
- Generalize on `artifact_key` so RFC etc. get it free later; ship PRD first.

### Frontend (App.jsx)
- In the Markdown Editor toolbar (next to Save), add **"Refine with AI"** (and keep Save).
- After refine returns, show the proposed revision with a diff + Accept/Discard.

---

## Part 2 — Workflow stages → Agile Board tasks

### Approach
Materialize each workflow stage as a board task, status synced to run state. Stage→task is created
when the stage is first reached and updated as the run advances.

- A small declarative map: stage/node → { title, role }. Stages: product_requirement (PRD),
  architect (RFC), implementation, qa, release (mirror `WORKFLOW_NODES` / the gate chain).
- On node transition (in graph.py where `current_node`/`next_node` are set), ensure the task for the
  active stage exists (`create_task` once, idempotent by a stable external key, e.g. `stage:prd`)
  and set status: active stage → `doing`; stages whose gate has passed → `done`; not-yet-reached →
  `todo`. Use `LocalTaskStore` at the project runtime root (the ledger `ensure_project_task_ledger`
  already creates).
- Idempotency: store a `stage` marker on the task (or external_ref `stage:<node>`) so re-entry
  updates rather than duplicates. Map task status → board column via existing `TASK_BOARD_STATUS_MAP`.
- The PRD-refine loop (Part 1) ties in: the PRD task goes `doing` while editing/refining, → `review`
  on a proposed refinement, → `done` when the PRD gate is approved.

### Backend
- A `sync_stage_tasks(state, store)` helper called at node transitions (and once in
  `ensure_project_task_ledger`/run start to seed the `todo` stages). No new endpoint — the board
  already reads the ledger via `build_agile_board`.

### Frontend
- None required for the board itself (it already renders the ledger). Optional: a stage badge.

---

## Gate decisions (founder — these change the build)
- **Q1 — Refine trigger:** explicit **"Refine with AI" button** (recommended; predictable, no
  surprise token spend on every save) vs **auto-on-save** (every save triggers a refine).
- **Q2 — Revision handling:** keep a **proposed revision + diff + Accept/Discard** (recommended;
  human stays in control, true "re-review") vs **overwrite** prd.md in place (simpler, no diff).
- **Q3 — Board task granularity:** **one task per stage** (PRD/RFC/impl/QA/release), status-synced
  (recommended, matches the workflow) vs **PRD-only** task for now.

## Tracks (after gate)
- **T-A (BE, codex):** `refine_artifact` + `/api/refine-artifact` + `/api/accept-refinement` +
  revisioning in ArtifactStore + tests. (graph.py / web.py / artifacts.py / tests)
- **T-B (BE, codex):** `sync_stage_tasks` at node transitions + seed on run start + tests.
  (graph.py / tasks.py / tests) — file-overlaps T-A in graph.py → **sequence T-B after T-A** or
  split graph.py carefully; likely sequential.
- **T-C (FE, agy):** Markdown Editor "Refine with AI" + proposed-revision diff + Accept/Discard.
  (App.jsx) — depends on T-A contract.

## Acceptance
- Live: edit a PRD → Refine with AI → a deepened proposal appears with a diff → Accept promotes it;
  the PRD task shows on the Agile Board moving todo→doing→review→done across the run.
- `pytest` + `ruff` green; FE `lint`+`build` green.
