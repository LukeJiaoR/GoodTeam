# Design — Open Questions: structured answer → targeted refine loop (Method 2)

Status: **founder-gated 2026-06-06** (chose Method 2). Builds on the PRD authoring loop (#86/#87)
and the changes_requested feedback loop (#90/#91). Reuses the refine → proposed → diff → accept infra.
Role chain: architect (this) → backend (codex) + frontend (agy) → review → pre-review → land.
**Dispatch AFTER #90/#91 land (overlaps web.py/graph.py/App.jsx).**

## Problem
PRDs contain a `## Risks And Open Questions` section (render_prd_markdown, graph.py:445; the worker
also writes risks/open-questions). Today the FE gives it ZERO special handling — it's just prose in
the markdown preview / raw text in the Markdown Editor. So "open questions" are a one-way artifact:
the AI asks, but the human can only answer by hand-editing raw markdown. No structured "see the
questions → answer each → answers flow back" loop. That's the unfriendly interaction.

## Goal
Turn Open Questions into a closed loop: **parse the section into a structured answerable list → user
answers each inline → "Apply answers with AI" feeds the Q&A as guidance to a targeted refine →
produces a proposed revision that resolves the questions → human reviews the diff → accept.** Reuse
the existing refine/proposed/diff/accept machinery; do NOT build a parallel one.

## Backend (src/goodteam_os/graph.py + web.py + tests)
1. **Parse open questions.** Add `extract_open_questions(content: str) -> list[str]` (graph.py):
   find a heading matching `Open Questions` (incl. `Risks And Open Questions`), collect the bullet
   list items beneath it until the next heading. Best-effort + tolerant (skip empties, strip
   bullet markers). Pure function, unit-tested.
2. **Expose the questions.** `GET /api/open-questions?project=<key>&artifact=<key>` →
   `{ "artifact": <key>, "questions": [str, ...] }` (reads the current artifact content from
   state.artifacts, runs extract_open_questions). Empty list if none / no section.
3. **Answer → targeted refine (reuse refine_artifact).** Extend `refine_artifact(..., guidance: str = "")`:
   when `guidance` is non-empty, the worker task becomes "Revise the {ARTIFACT} to incorporate the
   following answers, RESOLVING the corresponding open questions and removing them from the Open
   Questions section: <guidance>" (instead of the generic "deepen"). Add
   `POST /api/answer-open-questions { project_key, artifact, answers: [{question, answer}] }` that:
   builds a guidance string from the answered pairs (skip blank answers), reads the current artifact
   content, and calls `refine_artifact(content=<current>, guidance=<built>)`. Returns the SAME shape
   as /api/refine-artifact (`{ base_path, proposed_path, diff_summary }`), so the existing
   `/api/accept-refinement` / `/api/discard-refinement` flow promotes/drops it unchanged.
   Add both new paths to the same-origin write allowlist (answer-open-questions; open-questions is a
   GET).
4. Tests: extract_open_questions on a PRD with/without the section (and the "Risks And Open
   Questions" variant); answer-open-questions builds guidance containing the answers and produces a
   proposed revision; blank answers are skipped.

## Frontend (frontend/src/App.jsx only)
1. In the artifact view (the Markdown Editor area, ~line 3998), when the open-artifact has open
   questions (fetch `GET /api/open-questions`), render a structured **"Open Questions" panel**: one
   card per question (the question text) + an **answer textarea** under it. Keep dark-mode readable.
2. An **"Apply Answers with AI"** button → POST `/api/answer-open-questions { project_key, artifact,
   answers }` (only the answered ones). Show a loading state.
3. On success, reuse the EXISTING proposed-revision panel from #87 (proposed text + diff +
   Accept/Discard) — i.e. set the same `proposedRevision` state the refine flow uses, so the user
   reviews the diff and Accept promotes it (no new accept UI).
4. After accept, re-fetch open questions (resolved ones disappear).

## Acceptance / smoke
- BE: `pytest` + `ruff` green; new tests above.
- FE: `npm run lint` + `npm run build` green.
- Live: open a PRD that has open questions → the panel lists them with answer fields → answer one →
  Apply Answers → a proposed revision appears (diff) that incorporates the answer and drops the
  question → Accept → the question is gone from the artifact + the panel.

## Lane split (file-disjoint, parallel — AFTER #90/#91 land)
- **BE → codex:** graph.py + web.py (+ tests).
- **FE → agy:** frontend/src/App.jsx only.
Contract pin: `GET /api/open-questions → {questions:[str]}`; `POST /api/answer-open-questions
{project_key,artifact,answers:[{question,answer}]}` → same shape as /api/refine-artifact; reuse
accept/discard-refinement + the FE proposedRevision panel.

## Out of scope
RFC/other-artifact open questions (PRD first; generalizes trivially via artifact_key); making open
questions a hard gate blocker (that was Method 3, not chosen).
