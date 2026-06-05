# Wave 2 #1 — Dispatch-artifacts → memory provenance (design)

Status: **DESIGN / founder-gate** · Owner role: architect (Claude) · Date: 2026-06-04
Research: `/tmp/w2-provenance-research-result.md` (codex, read-only, cited).

## Charter
Persist the dispatch chain — **PR ⇐ contract ⇐ research ⇐ review/land** — as run-linked,
durable records written back to memory. Serves both moats (the graph leaves an auditable
trail; memory compounds from real run outcomes). Founder priority: self-driving first.

## What already exists (don't rebuild)
- `WorkerJobStore.run_job` completion hook (`workers.py:587`) already calls two write-backs:
  - `_write_back_memory` (`workers.py:623`) — parses `result.md` `## Learned/Caveats/Decisions/
    Open Questions` → role memory events, source `<job_id>/result.md`.
  - `_write_back_provenance` (`workers.py:659`) — one short-term event: `Dispatched job <id>
    (run <run_id>, adapter=…, model=…) for task: <excerpt>. Outcome: <status>.`, source
    `<job_id>/provenance`.
- `append_memory_event` (`memory.py:199`) — additive JSONL WAL + projection + dedup. The
  charter's "write-back to memory" surface is already the right primitive.
- `record_gate_decision` (`debate.py:181`) — precedent: additive memory event with a source
  backlink, no bespoke table.

So the **forward half** (dispatch → contract → result) is *already captured at write time.*

## The real gap (dissent vs. the research's literal "smallest increment")
The research's smallest increment = enrich the `_write_back_provenance` text with artifact
paths + changed files. I recommend **against that as increment 1**, for two reasons:

1. **It dies with the worktree.** `.goodteam/` is fully gitignored (`.gitignore:18`). A code
   track is dispatched into a throwaway worktree (`goodteam-dev-<name>`); the worker writes its
   provenance/result events into **that worktree's** `.goodteam/memory/`. `land-pr.sh` runs
   `git worktree remove "$WORKTREE" --force` **before** merge (`land-pr.sh:56`). So for exactly
   the tracks we most want to remember (the ones that landed), today's provenance write-back is
   **captured and then deleted.** Enriching the text produces a *richer record that still dies.*
2. **The back half of the chain is never written at all.** `land-pr.sh` has zero memory
   awareness — it records no review outcome, no PR↔run/job link, no land decision anywhere
   durable. The chain breaks precisely at review/land, the highest-value link.

## Proposed increment 1 — Intercept & Air-drop (founder-endorsed)
Don't restructure `_write_back_provenance`; rewrite `land-pr.sh`'s **lifecycle** so it steals
the provenance out of the doomed worktree before the kill, then air-drops it onto main *after*
the merge lands. Two small, additive, sequential tracks. No new store, no schema break; reuses
`append_memory_event` / `RoleMemoryStore`.

### Track P-BE — a CLI seam to append a run-linked memory event (codex) — lands first
- Add `goodteam_os.cli memory-append` subcommand: thin wrapper over
  `RoleMemoryStore(project_root).append_memory_event(role, horizon, text, source)`.
- Lane: `cli.py` + `tests/`. **Does NOT touch `workers.py`** (the local-edit file) or
  `memory.py` logic (reuse as-is). Read-only on git. (land-pr writes durably *through* this
  seam — never hand-rolls JSONL in bash, so it inherits dedup + projection refresh.)
- Acceptance: idempotent via existing dedup; bad horizon/role → nonzero exit + clear message;
  pytest + ruff green.

### Track P-SH — `land-pr.sh` Intercept & Air-drop (codex) — depends on P-BE
Rewrite the land sequence into three phases around the existing merge:
1. **Extract (before `git worktree remove`):** copy the worktree's newest provenance/result
   memory events + the run/job link (best-effort read of `.goodteam/workers/jobs/*/status.json`
   for `run_id`/`job_id`) into a host staging area (e.g. `/tmp/land-provenance-<PR>/`).
2. **Merge (unchanged):** CI gate → worktree remove → squash-merge → sync main. Land of code is
   always higher priority than memory write.
3. **Air-drop (after merge completes + main HEAD advances):** via the P-BE CLI, write into the
   **main checkout's** `.goodteam/memory/` — (a) the staged forward-half events (copy-forward),
   and (b) one new land event: role `release`, horizon `mid-term`, source `land/pr-<PR>`, text
   citing PR #, head branch, CI=pass, pre-review present (y/n), squash-merge commit SHA, and
   `run_id`/`job_id` if discovered (omit silently if absent).
- **Graceful fallback:** any extract/air-drop miss or failure → `warn`, never `raise`/abort. The
  merge must never be blocked by a memory write.
- Lane: `scripts/land-pr.sh` only. Preserve every existing flag + the T2 stash/restore trap.
- Acceptance: `bash -n` passes; **hard test** (`tests/`, scratch sim — do NOT merge a real PR):
  mock a fake provenance file in a worktree, run the land flow against a scratch
  main-equivalent, assert (i) the event survives into the main path's
  `.goodteam/memory/roles/release/events.jsonl` and (ii) the worktree is cleanly removed.

## Explicitly deferred (named, not silently dropped)
- Preserving original `/tmp` task/constraints **source paths** into `job.json`
  (`dispatch.py:182` drops `task_source`) — nice-to-have audit, low value now.
- Durable **pre-review report** capture (lives in `/tmp` + throwaway worktree,
  `pre-review.sh:179`) — fold in once P-SH proves the land-event path.
- A first-class **human review-decision** record beyond CI/pre-review presence — needs a
  decision-capture surface; revisit after increment 1.

## Verification
- BE: `PYTHONPATH=src .venv/bin/python -m pytest tests -q` + `ruff check` green.
- Integration (no real merge): scratch-dir sim of `land-pr.sh` showing the event written to a
  *separate* (main-checkout-equivalent) memory dir, surviving a simulated worktree removal.
- Regress each track through the triple-check (Claude review → codex pre-review → CI) →
  `land-pr.sh` (human land gate). P-BE lands first (P-SH depends on the CLI seam).

## Decision needed (founder gate)
Scope of increment 1 — see the question posed alongside this doc.
