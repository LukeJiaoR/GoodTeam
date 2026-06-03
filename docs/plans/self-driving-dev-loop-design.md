# Self-driving dev loop — architect design (Option A: mechanical orchestration on GoodTeam's own surface)

> Stage: architect. Founder priority: "GoodTeam 能自我驱动为先" → Option A (expose dispatch /
> pre-review / land as GoodTeam's own API+UI, human keeps judgment+land gate). Charter:
> "Self-host the role chain" + sediment-order hard rule (**mechanical first, judgment/auto-land
> LAST, only once verification is trustworthy; CI-green ≠ correct**). Grounded in an OFFLOADED
> read-only research survey (codex, 2026-06-03, cited). The mutating actions are SUPERVISED; this
> doc is the autonomous-safe deliverable.

## What's reusable vs must-add (research-confirmed)
- **Dispatch**: reusable python core `dispatch_tracks(project_root, tracks, verify_commands,
  constraints_file, allow_unsafe)` (dispatch.py:116) + `parse_dispatch_track_spec`/`_verify_spec`.
  An `/api` can call it in-process (the CLI is just a wrapper). **Worktree creation is shell-only**
  (`git worktree add` in dispatch-track.sh) → a worktree surface must be added.
  Isolation guard `ensure_dispatch_project_root_is_safe` (dispatch.py:49) already rejects a
  project_root nested under real repos.
- **Observability**: `WorkerJobStore.list_jobs` + the existing `/api/state` `worker_jobs`
  projection already exist (web.py:1472, 5075). Active worktrees / open PRs / CI / pre-review
  status are NOT tracked in python — scripts read them live from `git worktree` / `gh`.
- **pre-review**: a reusable dispatch tail, but PR-lookup/fetch/diff/throwaway-worktree/report-copy
  are shell+gh (pre-review.sh).
- **Land**: **entirely shell+gh, NO python entry.** It runs `gh pr merge --squash --delete-branch`
  (remote), `git worktree remove/prune`, **`git checkout main` + `git pull` on the server's OWN
  checkout**, post-merge pytest (land-pr.sh).

## ⚠️ The danger that reshapes Option A
The server is `ThreadingHTTPServer` with **CORS `*` and NO authorization** on POST routes
(web.py:5250,5387). `land` mutates the **remote** (`gh pr merge`) AND the **server's own working
tree** (`git checkout main`/`git pull`). So a naive "Land button" = any unauthenticated localhost
POST (or browser tab) can merge to the remote and corrupt the dashboard's own repo state. **Land
must NOT run in the dashboard server process.** This is consistent with the founder's choice to keep
the human land gate.

## Increments (risk-tiered within Option A; sediment-order: read → additive → destructive)

### SA-1 — Dev-loop OBSERVABILITY panel  (read-only; DO FIRST; safe + high-value)
A dashboard "Dev Loop" view: in-flight **worktrees** (`git worktree list`), **open PRs + CI status**
(`gh pr list` / `gh pr checks`), **pre-review reports** (link by PR), and **worker jobs** (already
projected). GoodTeam *sees* its own development loop. New read-only `/api/devloop` endpoint
(shells `git`/`gh` read-only) + a FE panel. No mutation, no new exposure beyond read. This is the
honest first rung of "self-driving": situational awareness of its own dev state. Risk: low.

### SA-2 — "Dispatch a track" action  (additive; medium; gated)
API+UI to spin a worktree + dispatch a track, reusing `dispatch_tracks` in-process. GoodTeam can
start its own dev work from the dashboard. Must add: a worktree-management surface (create/reuse a
named worktree off main, the `.venv` symlink the scripts do), keep the `ensure_..._safe` isolation
guard, and a UI confirmation. It runs an agent (codex) — same risk class as the workers the server
already runs; the NEW risk is worktree creation + that it's an unauthenticated POST → require at
least a same-origin/confirmation guard (see SA-0). Risk: medium.

### SA-3 — Land  (destructive; DO NOT auto-execute in-server)
**Recommendation: do NOT expose land as a server action that merges.** Instead, surface
**land-readiness** in the SA-1 panel (CI green + pre-review clean + worktree known) and provide the
exact `scripts/land-pr.sh <PR> --worktree <path>` command (copy/one-click-to-clipboard) for the
human to run. The actual merge + `checkout main`/`pull` stays a human-invoked, out-of-server action
— which IS the judgment+land gate Option A keeps. Auto-execution of land belongs to the deferred
"autonomous" option (C) and needs: out-of-process runner (never the server's checkout), server
auth, CI-trust proof. Risk if done naively: HIGH → deliberately not done now.

### SA-0 — Security prerequisite for ANY mutating action  (gates SA-2)
Before SA-2 (or any POST that creates worktrees / runs agents on demand), add a minimal guard: the
server binds localhost (it does) but CORS is `*` and there's no auth. Add at least a same-origin /
CSRF-style check or a local action token for mutating endpoints, so a random browser tab / local
process can't drive dispatch. Small, but a prerequisite for exposing dispatch.

## Recommendation
1. **SA-1 (dev-loop observability panel)** — do now: read-only, safe, immediately makes GoodTeam's
   self-development visible from its own dashboard. BE read endpoint + FE panel (FE → agy).
2. **SA-2 (dispatch-a-track action)** — next, gated on SA-0 (the mutating-action guard) + a
   worktree surface. Medium risk; reuses `dispatch_tracks`.
3. **SA-3 (land)** — surface readiness only; keep the merge a human-invoked script (the retained
   gate). Auto-land deferred to option C with its prerequisites.
4. Flag (backlog): the dashboard's CORS-`*`/no-auth posture is a broader security gap once any
   mutating self-driving action exists.

## Honest note
"GoodTeam self-drives" at the safe tier = it **sees** its dev loop (SA-1) and can **start** its own
work (SA-2), while the **destructive land stays the human gate** (SA-3) — exactly the founder's
"human keeps judgment+land gate." The naive "add dispatch+land buttons" would have run a remote
merge + `git checkout/pull` from an unauthenticated server against its own repo; catching that is
the point of designing before building.
