# Reference — workflow & role division (retro + playbook)

A reference for future multi-agent tasks in this repo. It captures how the first
"planner → split implementation → QA" round actually ran, where it under-used
roles, and a fuller role map to borrow from next time. GoodTeam itself defines
~13 roles; dogfooding that role model on GoodTeam's own development is the point.

## What this round actually used (4 effective roles)

| Effective role | Who | Output |
|---|---|---|
| planner + architect + reviewer (collapsed) | Claude | the two track plans, the contract, the review |
| frontend | Gemini | the React panel in `frontend/src/App.jsx` |
| backend | Codex | `/api/project-config` + `update_worker_backend_config` |
| qa | (assigned agent) | runs `worker-backends-panel-qa.md` |
| orchestrator | the human | dispatches agents, decides sequencing |

## Roles GoodTeam defines but we skipped / collapsed

GoodTeam's roster (`models.yaml`, `role_prompts.json`): `orchestrator`,
`research`, `product_requirement`, `architect`, `review`, `planner`, `ui`,
`implementation`, `frontend`, `backend`, `qa`, `code_review`, `release`,
`retro_memory`.

- **product_requirement** — skipped. We jumped straight to a technical plan with
  no short PRD/intent doc ("why this feature, for whom, acceptance from a user's
  POV"). Fine for a small internal panel; risky for anything user-facing.
- **architect** — folded into the planner. The interface contract *was* the
  architecture, but there was no standalone decision record (e.g. "one endpoint
  vs two" — which is exactly the smell the patch now fixes; an architect pass
  would likely have caught it up front).
- **code_review** — done ad-hoc by the planner after the fact, not as a first-
  class gate. It still found a real smell (`/api/project-config` routing). Making
  it a standing role means such findings surface *before* merge, not after.
- **release** — none. No integration/release gate; "is it done + safe to land"
  was implicit.
- **retro_memory** — done by hand (these `docs/plans/*` and
  `docs/current-progress.md` updates ARE the retro/memory role, performed
  manually). This is the same gap GoodTeam's own thesis (Moat #2) calls out:
  lessons live in a human's edits instead of compounding automatically.
- **research / ui / orchestrator** — research/ui weren't needed here;
  orchestration is the human, which is fine.

## Recommended fuller division for the next task

Scale the number of roles to the task's stakes. A good default for a real
feature:

1. **product_requirement** (Claude, 1 short doc) — intent + user-facing
   acceptance, before any plan. Half a page.
2. **architect** (Claude) — the interface contract **and** a 3-bullet decision
   record (the choices + why), so reviewers can check intent vs build.
3. **planner** (Claude) — split into per-track handoffs (as we did:
   `*-backend.md` / `*-frontend.md`), each cold-start self-contained.
4. **frontend** (Gemini) / **backend** (Codex) — implement to the contract,
   in parallel, staying in lane.
5. **code_review** (Claude or a dedicated agent) — a standing review pass against
   the contract + a smell/security scan, **before** QA, producing patch docs
   (template: `patch-project-config-routing.md`) for anything found.
6. **qa** (any agent w/ browser tooling) — the live end-to-end run
   (template: `worker-backends-panel-qa.md`), verification-only, evidence-backed.
7. **release** (human + Claude) — gate: all green, then commit/land + archive the
   plan docs.
8. **retro_memory** (Claude) — capture lessons into `docs/current-progress.md`
   and refine the templates. (Eventually automate via Moat #2 write-back.)

## Templates produced so far (borrow these)
- Per-track plan: `worker-backends-panel-backend.md`, `worker-backends-panel-frontend.md`
- QA / live integration: `worker-backends-panel-qa.md`
- Patch handoff: `patch-project-config-routing.md`
- (TODO) PRD/intent stub, architecture-decision stub — add when a task warrants.

## The one-line takeaway
We proved the **planner → split-implement → QA** spine works with three model
families. The next increment is to add the **product_requirement**, **code_review**,
and **release** gates as first-class steps so review findings and "is it safe to
land" stop being ad-hoc — which is also a live rehearsal of the role model
GoodTeam sells.
