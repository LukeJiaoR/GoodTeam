# Graph engine Step 2/3 — implementation design (resolves the open questions)

> Stage: architect. Follows `docs/plans/graph-engine-prd.md` (Step 1 landed as #36's
> declarative authoritative table). This doc RESOLVES the PRD's §7 open questions and the
> safety design for parallel execution, so the build is well-scoped. **The build itself is a
> SUPERVISED session** — this doc is the autonomous-safe deliverable (analysis + design);
> the code is not landed here. Grounded in `graph.py` (read 2026-06-03).

## 1. Finding: "Step 2" (engine adapter) is largely redundant — don't adopt LangGraph
The PRD's Step 2 was "drive the table with LangGraph (or equiv): checkpointer→StateStore,
human gates→interrupt/resume, conditional edges, retry." Re-examined against what landed:
- **Interrupt/resume already exists**: gates are `await_<gate>_approval` node-names;
  `decide_approval` resumes. That IS interrupt/resume.
- **Declarative transitions already exist** (#36): `GATE_TRANSITIONS` + `WORKFLOW_NODES`,
  consumed by `decide_approval` + the driver.
- **Checkpointer→StateStore already exists**: StateStore writes current.json + checkpoints +
  action-log.jsonl.
So adopting LangGraph would re-introduce a heavy dependency (we removed it in #34) to
re-implement capabilities we already have, against the "own the ledger" principle.
**Decision (resolves §7 engine-choice): do NOT adopt LangGraph. Keep the in-house table+driver.**
The only genuinely-missing Step-2 capability is **declarative failure/error routing** (today a
worker failure hand-dead-ends to `next_node="blocked"`; triage retry is hand-built). That is a
small, safe, additive enhancement (see §3a) — not an engine swap.

## 2. Finding: "Step 3" (parallel fan-out) is unsafe as-is — needs worker isolation
`run_implementation_workers` (graph.py:894) creates ui + implementation + qa jobs and runs
them **sequentially** (`return [store.run_job(job["id"]) for job in jobs]`) in ONE shared
`runtime_root` (the project repo). The naive parallelization (just `ThreadPoolExecutor` over
the three) is **unsafe**:
- **Shared-tree write races**: ui and implementation both run agentic CLIs with
  `--dangerously-bypass-approvals-and-sandbox` in the same working dir → concurrent writes
  (package.json, lockfiles, shared config) can clobber/corrupt.
- **Dependency ordering**: qa VERIFIES implementation's output, so qa must run AFTER impl —
  it cannot be a peer in a flat parallel set.
- The existing dispatch collision-detection (`dispatch.py` `file_collisions`) only REPORTS
  overlaps post-hoc; it does not PREVENT concurrent corruption.

## 3. Recommended build (supervised), in two safe increments

### 3a. Declarative failure edges (small, safe — do first)
Extend `WORKFLOW_NODES` so each node may declare `on_error` / `on_blocked` targets and a
`retry` policy; the driver/worker-runner consults the table instead of the hand-coded
`next_node="blocked"`. Behavior-preserving by default (default `on_error="blocked"`), existing
tests as the regression net. This is the real, safe slice of "Step 2 conditional edges" — and
it makes failure routing auditable + declarative. Lane: workflow_table.py + graph.py + tests.

### 3b. Parallel fan-out WITH isolation (the real Step 3 — the hard part)
Design for safe concurrency:
- **Dependency model**: declare the impl-stage as a small DAG — `{ui, implementation}` have no
  edge between them; `qa` depends on both. The driver runs a *level* concurrently, then the
  next level. (Generalizes to advisor/committee nodes later — vision §6.2.)
- **Isolation** — ⚠️ **CORRECTION (2026-06-03):** the original "git worktree per worker" idea
  is INVALID. The `runtime_root` is NOT a git repo — `resolve_project_runtime_root` (graph.py)
  just picks a repo dir, nothing `git init`s it, and the codex worker passes
  `--skip-git-repo-check` precisely because it isn't git. So `git worktree add` is not
  available. Revised isolation options:
  - **(a) copy-dir snapshot per worker**: snapshot `runtime_root` → a temp dir per concurrent
    worker, run the worker there, then copy changed files back (merge). Works without git, but
    cost scales with runtime_root size (cheap for fresh init_project; expensive for large
    adopt-existing repos), and the merge-back needs a conflict rule (disjoint-domain or
    last-write + collision report).
  - **(b) enforced disjoint domains**: only parallelize workers whose write domains are
    declared disjoint (ui→`frontend/`, impl→`backend/`), run them in the SHARED root
    concurrently, and FAIL the level if the post-hoc collision-detection reports any overlap.
    Cheaper (no copy), but relies on the domain declaration being correct + the workers
    obeying it.
  - There is no clean git-worktree option. This materially raises 3b's cost/complexity vs the
    moderate value (parallelizing ~3 workers saves ~1 worker's runtime). **Reassessment below.**
- **Aggregation**: the returned `list[dict]` keeps the same shape/order (ui, impl, qa) so
  downstream `format_*_evidence` is unchanged.
- **Failure**: a level fails closed (qa not run if a builder failed) via the 3a failure edges.

**Why supervised**: 3b changes worker *execution semantics* (concurrency + isolation + merge)
in the workflow core — exactly the large-diff, behavior-shifting change where a review blind
spot does max damage. It needs a human at the gate (PRD §5) + a live smoke (real workers in an
isolated workspace) before land.

## 4. Recommendation / sequencing
1. **3a (declarative failure edges)** — safe, additive, autonomous-landable via the triple
   check; do whenever. Modest value (auditable failure routing).
2. **3b (parallel fan-out + isolation)** — the real moat-leverage (faster runs, true team
   parallelism) but a **supervised build**: pick the isolation strategy (worktree-per-worker
   merge [robust] vs enforced disjoint-domain [cheap]) with a human, implement, live-smoke,
   present, land. **Recommended isolation: worktree-per-worker + merge** (robust, reuses git;
   no fragile path assumptions).
3. **Do NOT** adopt LangGraph (§1) — and update pyproject's "LangGraph-based" description to
   reflect the in-house engine when 3a/3b land.

## 4a. Reassessment after the git-worktree finding (2026-06-03)
With git-worktree isolation off the table, 3b's options are copy-dir (costly/conflict-prone)
or enforced-disjoint-domains (cheaper but assumption-heavy) — both materially more complex
than first scoped, for **moderate** value (parallelizing 3 workers ≈ saves one worker's wall
time). **Honest recommendation: DEFER 3b.** Its risk/complexity now clearly outweighs the
payoff at the current scale (3 fixed workers). 3b becomes worthwhile when the worker set is
larger/dynamic (the real DAG with advisors/experts — vision §6.2), at which point the
isolation cost is justified and the design should be revisited with the copy-dir-vs-disjoint
decision made deliberately. Until then, parallel execution is not worth the workflow-core risk.

## 5. Backlog seed
- **3a (declarative failure edges)** — safe, additive, autonomous-landable. The recommended
  near-term graph increment IF graph work is wanted now.
- **3b (parallel worker fan-out)** — DEFERRED (see §4a): revisit when the worker set is
  larger/dynamic; decide copy-dir vs enforced-disjoint-domain isolation then (NOT git-worktree
  — runtime_root isn't git). SUPERVISED.
