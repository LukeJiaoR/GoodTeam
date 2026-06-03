# Collaboration-DAG engine — architect design (the Moat #1 execution-layer mainline)

> Stage: architect. Follows `graph-engine-prd.md` (Step 1 = declarative table, LANDED #36)
> and `graph-engine-step2-3-design.md` (Step 2 LangGraph = rejected; parallel-exec = deferred).
> Grounded in `graph.py` + `workflow_table.py` (read 2026-06-03). This scopes "collaboration
> DAG" (product-vision §6.2) into safe, incrementally-landable steps behind the stable public
> API. The structural step (D2) is a SUPERVISED build; D1 is triple-checkable.

## 0. Status reconciliation (so we build the right thing, not a re-run)
- **Declarative happy-path + approval routing: DONE (#36).** `workflow_table.py` already has
  `GateTransition{gate, on_approved_next, on_changes_requested, on_rejected}` and
  `WorkflowNode{name, run_guard_gate, runner_fn}`; the `security` gate is resolved (noop
  runner). So conditional *approval* edges are already table-data.
- **Engine choice: do NOT adopt LangGraph** (prior call, stands) — interrupt/resume = gates +
  `decide_approval`; checkpointer = StateStore. An in-house DAG driver keeps "own the ledger."
- **Moat #1 artifact layers already SHIPPED this session** (#35–#45: message bus, debate +
  quorum, memory supersede/citations, cross-project retrieval). They do NOT depend on this
  engine. **This mainline is the executor/plumbing, not new moat content** — be honest about that.
- **Parallel fan-out (old 3b): DEFERRED** — `runtime_root` is not git (worktree isolation
  invalid) and value≈one worker's wall-time at 3 fixed workers. Stays deferred (see D3).

## 1. What "collaboration DAG" actually means against the LANDED code
Two concrete gaps separate today's *declarative linear chain* from a *DAG* (§6.2 = nodes=tasks,
edges=dependencies + comm channels):
1. **Failure/error edges are NOT declarative.** A runner failure hand-dead-ends to
   `state.next_node = "blocked"` (graph.py:773, 831) or `raise RuntimeError(f"... [{failure_class}]")`
   (1266, 1329). The worker result already carries `failure_class`; nothing routes on it as table
   data. The hand-built triage lives outside the table.
2. **The structure is a chain, not a DAG.** Every gate maps to exactly one `on_approved_next`;
   `run_implementation_workers` is `[store.run_job(j) for j in jobs]` (graph.py:951). No node can
   declare *multiple dependencies* (fan-in), so the table literally cannot express an
   advisor/committee/expert node feeding a decision — the §6.2 collaboration primitive.

## 2. Target architecture (in-house DAG over the owned ledger)
Generalize `workflow_table.py` from a linear table to a **DAG spec**, executed by a
**topological-order driver**, behind the unchanged public API (`run_until_next_gate`,
`advance_approved_workflow`, `decide_approval`, `start_workflow` — PRD §3). StateStore stays
the sole owner of state (AgileDevState); the driver computes transitions, StateStore records them.
- **Typed edges**: extend the table so each node/gate can declare `on_error` / `on_blocked`
  targets + a bounded `retry` policy, in ADDITION to the existing approved/changes/rejected edges.
- **Node dependencies (fan-in)**: a `WorkflowNode` may declare `depends_on: [node,...]`; the
  driver runs a node only once its deps are satisfied. Linear chain = the degenerate case
  (each node depends on its single predecessor) → **behavior-preserving by default**.
- **Execution stays SEQUENTIAL** (topological order). No parallelism in this mainline → no
  shared-`runtime_root` write races. Parallel level-execution is a later opt-in (D3).
- The already-built **message bus / debate** become the comm-channel content on advisor/committee
  edges once the DAG can express them — the engine makes them *structural* instead of bolted on.

## 3. Increments (ordered; each independently landable behind the stable API)

### D1 — Declarative failure/conditional edges + bounded retry  (SAFE — build first)
Extend the table: gates/nodes declare `on_error` (default `"blocked"`), `on_blocked`, and a
`retry: {max, on_classes:[...]}` policy; the driver/worker-runner consults the table instead of
the hand-coded `blocked`/`raise`. Fold the existing `failure_class`-based triage into this
declarative policy (classifier stays in workers; ROUTING becomes table data). **Behavior-preserving
by default** (default `on_error="blocked"`, retry max=0) → the existing `test_graph_sample` /
`test_web` transitions are the exact regression net. Value: auditable + declarative failure
routing, triage consolidated out of bespoke branches. Lane: `workflow_table.py` + `graph.py` +
tests. **Autonomous-landable via the triple-check.**

### D2 — DAG structure: node `depends_on` + topo driver + FIRST advisor/committee node  (SUPERVISED)
Add `depends_on` to `WorkflowNode`; replace the linear `workflow_node(next_node)` walk with a
topological driver that fires a node when its deps are satisfied (sequential). Prove the
generalization with ONE real non-linear node — the natural candidate is wiring the **already-built
debate** as a committee/advisor node feeding the `rfc` (or `staging`) gate: the debate node
depends on the producing node, runs read-only, and its output is the comm-channel evidence the gate
sees. This is the real chain→DAG leap and the first place §6.2 "edges = comm channels" becomes
literal. **Supervised**: it changes the driver's core walk (large-diff, behavior-shifting) — human
at the gate + a live smoke (real workers, isolated workspace) before land, per PRD §5.

### D3 — Parallel level execution  (DEFERRED — unchanged from prior call)
Once the driver is topological, *optionally* execute independent nodes in a level concurrently.
Still gated on the unsolved isolation problem (`runtime_root` not git → copy-dir vs
enforced-disjoint-domains; see `graph-engine-step2-3-design.md` §3b/§4a). Risk>value at 3 fixed
workers; revisit when the worker set is larger/dynamic. **Not in this mainline.**

## 4. Honest scope check (reviewer dissent, on the record)
At the current scale — 3 fixed implementation workers + a fixed 6-gate pipeline — the DAG's payoff
is **auditability + triage consolidation (D1)** and the **capability to express advisor/committee
nodes (D2)** so the already-shipped debate/bus attach structurally. It is **not** speed (that's D3,
deferred) and it is **not** new moat content (that already shipped #35–#45). D2 is worth doing
because it converts the debate from a bolted-on side-call into a first-class graph node and unlocks
dynamic per-run plans later — but it should be sized honestly as foundation/enablement, not as the
moat itself. If a higher-value target appears, D2 can wait; D1 stands on its own (removes real
hand-coded failure branching).

## 5. Recommendation
1. **Build D1 now** (declarative failure/conditional edges + retry) — safe, behavior-preserving,
   triple-checkable, immediate value (auditable routing + triage consolidation).
2. **Present D2 for the founder's supervised gate** (the topo driver + first advisor node is the
   chain→DAG leap; needs a human + live smoke).
3. **Keep D3 deferred** until the worker set is larger/dynamic.
4. Update `pyproject.toml` description (still says "LangGraph-based") when D2 lands the in-house driver.

## 6. Open questions (resolve as part of D1/D2)
- **D1**: do retries re-dispatch the SAME job id or a fresh one? (Provenance: prefer a fresh job
  linked to the prior via the dispatch-artifacts provenance idea; for D1, simplest correct =
  bounded re-run recorded in the action-log.) Decide in the D1 spec.
- **D2**: does an advisor/committee node block its gate (hard dep) or advise-only (soft, gate can
  proceed without it)? Recommend **advise-only first** (the debate informs the human at the gate;
  it does not auto-block) — matches the "human always arbitrates" principle from the debate work.
- **D2**: topological cycle/error handling — the driver must reject a table with cycles at load
  time (fail fast), not at runtime.
