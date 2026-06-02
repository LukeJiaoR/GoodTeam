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
- **Isolation**: run each concurrent worker in its **own git worktree** off the project repo
  (not the shared checkout), then **merge** results back deterministically after the level
  completes (or, if the team accepts disjoint domains — ui→`frontend/`, impl→`backend/` — gate
  on a path-domain check via the existing collision-detection and skip the merge). The
  worktree-per-worker + merge is the robust option; the disjoint-domain assumption is the
  cheap option but must be enforced, not assumed.
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

## 5. Backlog seed
- Implement 3a (declarative failure edges) — safe increment.
- Implement 3b (parallel worker fan-out with worktree-per-worker isolation + merge) — SUPERVISED.
