# Plan — release/dev self-hosting bootstrap

> Stage: plan (dev-infra). **Pipeline-ahead prep — execute only once auto-dispatch
> has graduated** (you can't orchestrate your dev with the half-built tool).
> Until then this is the ready-to-run plan, not yet executed.

## Why

Once GoodTeam dispatches workers to develop GoodTeam itself, the **orchestrator
must be a stable "release" instance** and the **target must be the "dev" repo
(via a worktree)** — never the half-built dev code running dispatch against its
own live files. This is the self-hosting / bootstrap separation. (For normal
users it does not exist: GoodTeam is the stable tool, their product is the
target. This is only the GoodTeam-on-GoodTeam case.)

## The two instances

- **dev GoodTeam** = the working repo `/Users/luke/WorkSpace/codeAi/GoodTeam`.
  Where humans/agents change code and run the test suite. Also the dispatch
  *target* (via a worktree).
- **release GoodTeam** = a checkout pinned at a **blessed ref** (a tag/commit
  that passed tests + human bless; e.g. `v1.0.0`) with its **own venv**. It is
  the *tool* that runs `goodteam-os serve` / `dispatch`. Pinned and persistent —
  it does NOT move when dev changes.

## Reuse what already exists

`src/goodteam_os/smoke.py` already has the GoodTeam-on-self worktree primitive:
- `ensure_self_worktree(repo_root, target)` → `git worktree add --detach <target> HEAD`
- `ensure_self_worktree_target_is_safe(...)` → target must be **outside** the repo

The release instance is a **pinned, persistent variant** of this: same "isolated
checkout of GoodTeam outside the repo" idea, but at a blessed ref and not torn
down. The dispatch *target* worktree can use the same primitive (detached, from a
dev ref).

## Deliverables

### 1. `scripts/release-up.sh`
Create/refresh and run the stable release orchestrator.
- Location: a sibling dir outside the repo, e.g. `../goodteam-release` (or
  `$GOODTEAM_RELEASE_DIR`).
- Steps (idempotent):
  - if missing: `git -C <repo> worktree add <release-dir> <REF>` (default `REF` =
    latest `v*` tag, fallback a `--ref` flag), then create `<release-dir>/.venv`
    + `pip install -e ".[dev]"`.
  - if present: ensure it's at the pinned `REF` (warn if dev asks to move it —
    moving is `graduate`, below).
  - then run the release tool: `goodteam-os serve --workspace <release-dir>/dev-workspace`
    (or print the `dispatch` usage). Reuse `dev-up.sh`'s seeded-workspace idea so
    the release instance is also "known-alive."
- Flags: `--ref <tag|commit>`, `--reinstall`, `--port`.

### 2. `scripts/graduate-release.sh`
Advance the release pin to a new blessed ref — the **graduation gate**.
- Preconditions (refuse otherwise): dev working tree clean; `PYTHONPATH=src
  pytest tests -q` green; `ruff check` clean; human confirmation (`--yes` or an
  interactive prompt).
- Action: tag the dev commit (e.g. bump `vX.Y.Z` or move a `release` tag), then
  `git -C <release-dir> checkout <new-ref>` + reinstall if deps changed.
- This is the only path that moves the release instance forward. Mirrors the
  `self` smoke "graduate after committing" discipline already in the footprints.

### 3. Doc note
A short README/CLAUDE.md note: "to develop GoodTeam with GoodTeam, run dispatch
from the release instance against a dev worktree; graduate via
`scripts/graduate-release.sh`."

## Ties to auto-dispatch

- The release instance runs `goodteam-os dispatch --project-root <dev-worktree>`.
- Auto-dispatch's **isolation guard** already refuses targets inside a configured
  `projects/*.local.yaml` repo; a dev worktree (outside the repo, not a configured
  target) passes — exactly the intended use.
- So no new safety code is needed here; release/dev is orchestration + scripts.

## Sequencing
1. Land + graduate auto-dispatch first (it is the thing being graduated).
2. Then execute this plan (`release-up.sh` + `graduate-release.sh`).
3. Switch the dev loop to: release-GoodTeam dispatches → dev worktree → review at
   gates.

## Acceptance (when executed)
- `scripts/release-up.sh` brings up a serve/dispatch-capable release instance at
  the pinned ref, with its own venv, outside the repo.
- `scripts/graduate-release.sh` refuses when tests are red or the tree is dirty;
  on green + bless, advances the pin and the release instance reflects it.
- From the release instance, `dispatch` against a dev worktree runs; against a
  configured real target it is refused by the isolation guard.

## Out of scope
- Any product feature (this is dev infra / self-hosting only).
- Changing the worker/dispatch implementation (reuse as-is).
