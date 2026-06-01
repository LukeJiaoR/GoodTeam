# PRD + architecture — end-to-end demo / first-design-partner path

> Stage: **product_requirement + architect**. PRD/RFC gate — review before
> per-track implementation prompts are issued. Direction chosen by the founder:
> shift from "founder hand-drives the tools" to "someone else can run it."

## 1. Intent (why)

GoodTeam's plumbing is strong (dispatch, CI gate, memory, release/dev self-host,
adapters). But the *sellable artifact* — §10 GTM's "single 30-minute demo: idea →
PRD → working web app → release on staging, with full audit trail" — does not yet
exist as a **reproducible, someone-other-than-the-founder-can-run-it** path. The
full chain has only ever been driven by the founder with tacit knowledge
(CONTENTOPS / AgentsMesh footprints). This PRD defines the thinnest slice that
turns the proven pieces into a launch-ready demo + the first design-partner
onboarding path.

## 2. Users & value

- **OSS-launch viewer** (primary, week 1): watches the 30-min demo (screencast or
  live) and believes "idea → shipped app with an audit trail, by a team of agents."
- **First design partners** (the 10–50): clone, configure auth once, and run the
  same path on *their own* simple idea — reaching a working app + staging release
  without the founder on a call.
- Value: converts "a useful workflow the founder operates" into "a product a
  stranger trusts and reproduces" — the precondition for design-partner traction.

## 3. The demo, concretely (user-facing acceptance)

A new user, starting from a clean machine, can in ~30 minutes:
1. Install + configure **one** real worker backend (codex or claude) — a single
   documented auth step, verified by `doctor` showing the backend `ready`.
2. `goodteam-os init` + `start "<a simple app idea>"` → reach the **PRD gate**.
3. Approve PRD → RFC/plan gate → **approve** → implementation + QA run (real
   workers, via dispatch) → review gate → **release gate** → staging.
4. End state: a **working app** (builds + its own tests pass) and a **staging
   release** (served/deployed locally is acceptable for the demo; remote SSH is
   the AgentsMesh-style stretch), with every gate decision, artifact, worker
   result, and the **memory written back** visible in the dashboard as a coherent
   **audit trail** (artifacts + approvals + `mem:` citations + evidence).
5. Empty/failure states are legible: if a worker fails, the dashboard shows why
   and what to do — it does not dead-end silently.

Acceptance for *us*: the path is captured as a **scripted, pinned, repeatable
launch** (one command brings the demo up to a known-good starting point; the run
itself is real-worker but on a hardened, pinned idea so it reliably completes),
plus a **README "30-minute demo" walkthrough** a stranger can follow.

## 4. Gap analysis (what exists vs what this needs)

| Piece | Today | Gap to close |
|---|---|---|
| First-run to PRD (fake) | ✅ README Quick Start | none |
| Real worker auth | adapters + `doctor` readiness/hints exist | one crisp, documented "set up ONE backend" step + a green `doctor` check; reduce to a single happy path |
| Gate approvals by a non-founder | dashboard Approvals tab + `decide_approval` + Telegram | verify the dashboard approve flow is self-explanatory end-to-end (PRD→RFC→release) with no CLI tacit steps |
| Idea → working app (real workers) | driven manually (CONTENTOPS) via dispatch | a **pinned, hardened demo idea + target** that reliably completes with real workers; wire the run to use `dispatch` at impl/QA |
| Staging release | AgentsMesh ssh_compose proven; local serve exists | a **local** staging target for the demo (no remote infra needed to see "released") |
| Audit trail view | artifacts/approvals/memory panel/evidence all exist separately | a coherent "this is the trail" presentation (or a doc pointing at the existing panels in order) |
| Reproducible launch | `dev-up.sh` = fake seed only | a `demo-up.sh` (or mode) that brings up the **real** demo to a known starting point, isolated, idempotent |
| Walkthrough | README Quick Start stops at PRD | a README "30-minute demo" section covering the full chain incl. approvals + what to look at |

## 5. Scope

**In:** the scripted real-worker demo path on a pinned simple idea; the single
auth happy-path doc; the gate-approval UX smoke (dashboard-only, no tacit CLI);
local staging for the demo; the README "30-minute demo" walkthrough; an
audit-trail presentation (reuse existing panels, ordered).

**Out (this round):** remote/multi-service staging (AgentsMesh is the stretch
demo later); the autonomous collaboration graph (Moat #1 triage/debate — separate
mainline); cross-project memory (Moat #2 remainder); a hosted/SaaS version.

## 6. Architecture / key decisions (decision record — review these)

1. **Real workers, pinned idea — not fake, not free-form.** A working app needs
   real LLM workers (nondeterministic, costly). We make it *reproducible* by
   pinning the idea + a hardened target scaffold so the run reliably completes,
   not by faking it. (Rejected: fake workers — can't produce a working app;
   free-form idea — unreliable for a launch demo.)
2. **Local staging for the demo.** "Released on staging" is demonstrated by a
   local served/built app + passing target tests + evidence, so a viewer needs no
   cloud. Remote SSH (AgentsMesh path) is a later stretch demo. (Rejected:
   requiring remote infra — kills the 30-min clean-machine promise.)
3. **Dashboard-only approvals.** The demo driver approves every gate in the web
   UI; no `decide_approval` CLI step in the happy path. Any tacit CLI need is a
   bug to fix. (This is the "someone else can run it" test.)
4. **Reuse, don't rebuild.** dispatch (impl/QA), CI (verify), memory panel +
   drill-down (audit), evidence/staging reports all exist. This round is
   *packaging + hardening + a pinned demo*, not new moat infra.

## 7. Tracks (issued after this gate is approved)

- **A. demo launcher + pinned target (Codex):** `scripts/demo-up.sh` — isolated
  workspace, real worker backend, pinned demo idea, drives to the PRD gate and
  prints the dashboard URL + the exact approve-then-watch steps. Idempotent;
  never touches real configs.
- **B. auth happy-path + doctor polish (Codex):** one documented "configure ONE
  backend" flow; `doctor`/`init` surface a single green readiness line for the
  chosen backend; trim stale multi-backend confusion in onboarding docs.
- **C. gate-approval UX smoke (live, Playwright — assigned to Gemini):** drive
  PRD→RFC→release purely via the dashboard on the demo workspace; confirm the
  audit-trail tabs (Artifacts/Approvals/Memory) read coherently; flag anything that
  requires a CLI/tacit step. Verification-only (it does not edit product code), so
  Gemini is a good fit + plays to its UI-interaction strength — dispatch it with the
  §0.4 hardened "封口" block (single-file/no-write whitelist, skill-disable,
  report-only) since this is a read/observe task. Uses the webapp-testing
  (Playwright) skill. **Blocked until the demo produces the correct product** (the
  idea-fidelity bleed must be fixed first — see current-progress 2026-06-01).
- **D. README "30-minute demo" walkthrough (Claude/docs):** the stranger-followable
  script: install → auth → init → start → approve gates → watch impl/QA → release
  → where to read the audit trail.

Sequencing: B (auth happy-path) + A (launcher) first — they unblock everything;
then C (approval smoke) on the launched demo; D (walkthrough) captures the proven
path last so it matches reality.

## 8. Open questions (resolve at this gate)
- **Pinned demo idea + target shape?** Recommend the README's existing "task
  tracker" (small React+JSON-API app, `delivery_mode: init_project`) — already the
  documented example, small enough to complete reliably.
- **Which single backend is the demo's happy path?** Recommend **codex**
  (`gpt-5.3-codex`) — it's our default implementer and the most-exercised adapter.
- **Live vs recorded for the OSS launch?** Recommend a pinned path that supports
  *both*: live for design partners, screencast of the same path for the launch.
