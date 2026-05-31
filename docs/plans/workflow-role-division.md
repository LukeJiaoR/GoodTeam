# Reference — workflow & role division (retro + playbook)

A reference for future multi-agent tasks in this repo. It captures how the first
"planner → split implementation → QA" round actually ran, where it under-used
roles, and a fuller role map to borrow from next time. GoodTeam itself defines
~13 roles; dogfooding that role model on GoodTeam's own development is the point.

## 0. Operating mode (default collaboration convention)

This is the standing default for this repo, regardless of who opens the session:

- **Claude is the planner / architect / reviewer / QA-analyst / retro role.**
  Claude investigates code (read-only), defines the contract, reviews diffs,
  finds bugs, and **produces role-targeted, copy-paste-ready prompts** for the
  implementing agents.
- **The human dispatches** each prompt to the matching agent (Gemini = frontend,
  Codex = backend, …) and lands the change manually.
- **Claude does not edit implementation code by default.** Exception: only when
  the human explicitly says "you fix it" / "直接改" (e.g. a tiny, low-risk
  cleanup). Even then, prefer handing it off once the workflow is mature.
- **Claude still does read-only investigation and verification** to make each
  prompt accurate — this is where the value is (e.g. catching that a constant was
  render-scoped and unavailable in a handler, so the FE prompt got it right on
  the first try). Investigate/verify = Claude; edits = the role's agent.

Why: routing edits through role agents exercises and hardens the real multi-agent
workflow, and surfaces process gaps faster than Claude doing everything inline.

## 0.1 Worker autonomy & the safety model

When a coding-agent worker (Codex / Claude Code / Gemini CLI) is dispatched, run
it **autonomously** — it should NOT stop to ask the human for per-command shell
permission (`Requesting permission for: nc -zv localhost 8788`, etc.). That
interactive prompting does not scale and is the wrong place for human attention.

- Codex CLI: `--dangerously-bypass-approvals-and-sandbox`
- Claude Code: `--dangerously-skip-permissions`
- Gemini CLI: YOLO / auto-approve mode (`--yolo` / `--approval-mode=yolo`, or its
  settings)

(GoodTeam's own dispatched workers already do this — see the bypass flags in
`workers.py` `CLI_BACKENDS`.)

**The safety model shifts — you approve outcomes, not keystrokes:**

```
per-command approval (off)  →  isolated workspace (contained blast radius)
                               + lane boundary (which dirs the agent may touch)
                               + post-hoc review/QA gates (catch mistakes after)
```

- **Only run autonomous when isolation is in place** (e.g. `dev-workspace/`, an
  isolated target repo). Never YOLO an unprotected real repo — there, per-command
  approval is the safety net.
- The human's attention belongs at **gate-level approvals** (the review/QA/release
  decisions on *outcomes*), not at individual tool calls.
- This is the manual form of what Moat #1 automates: once GoodTeam dispatches
  workers itself, they run autonomous by design and the human only sees gates.

## 0.2 Reduce hallucination stacking across agents

In a pipeline (architect → planner → implementer → QA), an upstream agent's
vague reference or unstated assumption gets treated as fact downstream, and each
layer adds its own — hallucinations **compound**. The defense is to pin
**verified, specific facts** at every handoff so the next agent works from ground
truth, not "it probably works like X."

- **Pin cross-component interface semantics — this is where agents hallucinate
  most.** Exact flag arity/type, how a prompt is delivered (stdin vs arg),
  what a function returns, a field that's always empty, the actual run-loop
  mechanism. Don't write "use `gemini -p`"; write "`gemini -p` takes a `[string]`
  value, and `_run_cli_worker` feeds the prompt via stdin — reconcile them."
- **Tag each fact verified vs unverified.** State "verified: X does Y (checked
  via `<command>`)" so downstream treats it as ground truth; mark "UNVERIFIED —
  implementer must confirm via `<command>` before relying on it" for anything not
  actually checked. Never let an assumption travel un-flagged.
- **The reviewer's first job is to find where an upstream agent assumed instead
  of verified** — that's the seam where a hallucination entered and would
  otherwise propagate.
- **Worked example (this is why the rule exists):** the auto-dispatch plan
  faithfully copied the PRD's Gemini flags but never reconciled `gemini -p`
  (a value-taking option) with the shared stdin prompt-feed → the adapter would
  have been dead-on-arrival. Root cause: the *architect* knew both facts but
  didn't pin the interface seam in the spec, so the planner filled the gap with
  a plausible-but-wrong assumption. Fix the spec, not just the plan.

## 0.3 When the reviewer can stand in for QA (fewer round-trips)

Not every feature needs a separate QA-agent pass. Right-size it:

- **Backend / pure-logic features (no UI, no browser, no live-integration
  surface):** the **reviewer (Claude) may stand in for QA** — self-verify
  (`pytest -q` + `ruff`), run the QA plan's script(s) + an integration check,
  then land. Flow collapses to **Codex implements (with its own tests) → Claude
  review-with-QA → land** — no separate QA-agent round-trip. (This is how
  auto-dispatch landed: 252 tests + a live fake-track dispatch run + isolation
  check, all by the reviewer.)
- **A separate, independent QA pass IS required when** the feature has a UI /
  browser surface, a live-integration/deploy surface, or is high-stakes /
  irreversible. There the independent eyes + browser/live verification earn the
  extra round.

Tradeoff to be honest about: folding QA into review trades independent
verification for speed; only do it where the blast radius is small and unit
coverage is strong. When in doubt, run the independent QA.

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
- Patch handoff: `patch-project-config-routing.md`, `patch-worker-backends-save-role-filter.md`
- (TODO) PRD/intent stub, architecture-decision stub — add when a task warrants.

## Writing a good role prompt (checklist + skeleton)

Hard rules distilled from the rounds so far — a prompt that an agent gets right
on the first pass:

1. **Cold-start self-contained.** Assume zero memory of any prior conversation.
2. **Pin the workspace.** Absolute repo root + the agent's working dir + which
   files it edits.
3. **Declare the lane boundary.** "Only touch X; do NOT touch Y" (the other
   track, tests, the other plan docs).
4. **State the shared contract** (API shape / types) so parallel tracks don't
   diverge; note fallbacks if the other side isn't merged yet.
5. **Give the precise change**, not just the goal — file + ~line + the exact
   snippet — and pre-flag scope/contract gotchas you found during investigation
   (e.g. "this constant is render-scoped, read it from state instead").
6. **Setup + verification commands** the agent can run, and concrete acceptance.
7. **Commit policy.** Usually "do not commit" — the human lands after QA.
8. **Point to the full doc** (`docs/plans/...`) for detail.

Copy-paste skeleton:

```text
You're doing <FE bug fix | BE feature | cleanup> in the GoodTeam repo.
Repo root: /Users/luke/WorkSpace/codeAi/GoodTeam
Only touch <dir>. Do NOT touch <other dir>/tests/other plan docs. Do not commit.

Setup: <venv / npm install line>

Background: <symptom + root cause, 2-4 lines>

Fix: <file path ~line> — <exact snippet>. Gotcha: <scope/contract trap you found>.

Verify: <build/lint/test commands> pass. Report what you changed.
Full detail: docs/plans/<doc>.md
```

## The one-line takeaway
We proved the **planner → split-implement → QA** spine works with three model
families. The next increment is to add the **product_requirement**, **code_review**,
and **release** gates as first-class steps so review findings and "is it safe to
land" stop being ad-hoc — which is also a live rehearsal of the role model
GoodTeam sells.
