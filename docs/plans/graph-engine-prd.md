# PRD + architecture — real graph engine for the workflow (Moat #1 execution-layer foundation)

> Stage: product_requirement + architect. PRD/RFC gate. **This is a DESIGN doc for a
> SUPERVISED session — NOT for unattended execution.** The state-machine replacement
> is large, high-risk, and touches the workflow core; it needs a human at the gate.
> Grounded in a read-only research survey (codex, 2026-06-02) — claims cite file:line.
>
> **Positioning (read this first).** This PRD delivers the *executor* under Moat #1, not
> Moat #1 itself. product-vision §6.2 defines the moat as five things; this PRD only
> addresses the **graph mechanism** — turning the hand-rolled `if/elif` ladder into a real
> graph engine, and eventually a parallel DAG. The four pieces that actually *compound*
> and that a competitor can't copy — the per-run **collaboration plan**, the **inter-agent
> message bus**, **committee/debate at gates**, and **ephemeral expert synthesis** (§6.2) —
> are **OUT of this PRD** and tracked in `docs/plans/message-bus-debate-prd.md`. Those
> artifact layers are mostly additive and **do not depend on this engine rewrite** (the
> triage loop, for example, we already hand-built without one). Don't read "graph engine
> done" as "Moat #1 done" — this is the plumbing: ~20% of the moat, and not the
> highest-leverage 20%. Pay this debt, but don't let it queue ahead of the artifact layers.

## 1. Context / why

product-vision §6.2 calls for the fixed serial pipeline to become a true
**collaboration DAG** (nodes = tasks, edges = dependencies + comm channels). The DAG
*mechanism* — the engine that can express and execute that graph — is what this PRD
builds; the *content* that rides on it (the architect-generated per-run plan, the
message bus, debate, experts) is the moat proper and lives in the companion PRD. Today's
reality (research-confirmed):

- The workflow is a **hand-rolled `if/elif` ladder** keyed on `state.next_node` +
  approval values, NOT a declarative graph (`advance_approved_workflow` graph.py:696-717;
  stage runners `run_until_next_gate` 1179, `run_architecture_until_gate` 720,
  `run_delivery_until_release_gate` 739, `run_staging_until_gate` 801, release 834,
  retro 854). Gates are encoded as `await_<gate>_approval` node-name strings
  (review_gate.py:4-12).
- `langgraph` was declared but imported 0× — removed in PR #34. So the "graph" is in
  name only.
- Consequences we hit this session: failures can only dead-end to `next_node="blocked"`
  (graph.py:761-767) — we hand-built triage retry that a real engine's conditional
  edges/retries give for free; worker jobs run **sequentially** via a list-comp of
  `run_job` (graph.py:941) with no fan-out primitive; adding a stage = editing the
  ladder + node-name strings; no graph visualization/composability.

This PRD specifies how to introduce a real graph engine **without a risky big-bang
rewrite**, preserving the owned ledger.

## 2. Non-negotiable principle: own the ledger, the engine is a swappable executor

The owned source of truth is **`StateStore`** — `state/projects/<key>/current.json`,
`state/.../checkpoints/<run_id>/<node>.json`, and the append-only `action-log.jsonl`
(state.py:55-77; web.py:54-84). (Note: "WAL" is the *memory* engine's term —
events.jsonl — NOT this. The workflow ledger is StateStore.) A graph engine
(LangGraph or equivalent) must be an **executor/projection over AgileDevState**, never
the owner of state. AgileDevState (state.py:15-30: run_id, current_node, next_node,
approvals, blockers, artifacts, ...) stays the persisted truth; the engine computes
transitions, StateStore records them. This mirrors the memory-engine principle.

## 3. Hard constraint: public entry points must stay stable

These are depended on by web.py / cli.py / tests and MUST keep working unchanged so
the swap is invisible to callers (research §7):
- `run_until_next_gate(state, workspace, project_config=None) -> AgileDevState` (graph.py:1179)
- `run_adoption_until_next_gate(...)` (graph.py:1149)
- `advance_approved_workflow(*, state, workspace, project_config=None) -> AgileDevState` (graph.py:696)
- `decide_approval(*, workspace, approval, decision, project_key="")` (web.py:555) + `gate_next_node` semantics (review_gate.py)
- `start_workflow(...)` (web.py:462) dispatching adoption-vs-new
- `tests/test_graph_sample.py` + `tests/test_web.py` encode current transitions — they are the regression net for "engine produces the same transitions."

## 4. Proposed approach — incremental, behind the stable API

**Step 1 (foundation, low risk): make the transition graph DECLARATIVE.**
Extract the `if/elif` ladder into an explicit transition table / node registry: each
node = {name, runner_fn, gate?, on_approved_next, on_changes/rejected}. The existing
stage runner functions become the node bodies; `advance_approved_workflow` /
`run_*_until_gate` become a generic driver that walks the table. **Same behavior, same
StateStore, same public entry points — just data-driven instead of hand-branched.**
This alone removes most "add a stage = edit a ladder" pain and is independently
testable against the existing test_graph_sample/test_web transitions (they must pass
unchanged). NO external engine yet.

**Step 2 (adopt the engine as executor): drive the declarative table with LangGraph
(or equiv).** Build a thin adapter: nodes/edges constructed from the Step-1 table,
the engine's checkpointer delegates to StateStore (engine does not own state),
human gates map to the engine's interrupt/resume, `decide_approval` resumes the
engine. Public entry points wrap the engine. Conditional edges replace the failure→
blocked branch; the engine's retry/edge model can host triage.

**Step 3 (enable the moat): collaboration DAG + parallel fan-out.** Once on the
engine, make `run_implementation_workers` a fan-out (parallel ui/impl/qa where
dependencies allow, reusing the dispatch collision-detection we just shipped),
declare advisor/committee nodes, etc. (vision §6.2). Note the framing carefully: this
step *enables* parallel/owner-advisor execution, but the moat **value** (the compounding
coordination artifacts — debates, triage recipes, expert briefs) is produced by the
companion PRD's layers, which can sit on the engine here *or* on the current ladder.
Step 3 is the multiplier, not the source, of moat value.

## 5. Risk + sequencing (why this is supervised, not overnight)

- **Step 1 is the safe, high-value first move** and could be done semi-autonomously
  (it's a refactor with the existing tests as the exact regression net). Even so it
  touches the workflow core — recommend a human glance at the transition table.
- **Steps 2-3 require a human at the gate**: an engine swap + parallel execution is
  exactly the large-diff, behavior-shifting change where an unsupervised review blind
  spot does maximum damage (this session proved review blind spots exist — the
  CONTENTOPS hardcode passed CI clean). Do them in a supervised session.
- Order: Step 1 (declarative table) → validate transitions unchanged → Step 2 (engine
  adapter) → Step 3 (DAG/parallel). Each step independently landable behind the stable
  public API.

## 6. Scope
**In (this PRD):** the graph *mechanism* — the declarative table → engine adapter →
parallel-DAG path above.
**Out (explicitly):**
- Doing Steps 1-3 right now. This is a plan; implementation is a separate,
  mostly-supervised effort. The unused-dep hygiene (5a) is already done (#34).
- **The Moat #1 artifact layers** — per-run collaboration plan, inter-agent message bus,
  committee/debate, ephemeral expert synthesis (vision §6.2). These are the compounding,
  defensible part and are specified in `docs/plans/message-bus-debate-prd.md`. They are
  mostly additive and do **not** block on this engine; sequence them on their own merits.
- Note: the package `description` still says "LangGraph-based" (pyproject.toml:3) —
  update it when Step 2 actually adopts an engine (or sooner if desired).

## 7. Open questions
**Must resolve as part of Step 1 (the table extraction forces it):**
- **`security` gate has no stage runner.** It exists in the approvals defaults but no
  `run_*_until_gate` advances through it (research caveat). A declarative table makes
  this dangling reference explicit and undeployable as-is, so Step 1 must pick one and
  encode it: **(a)** add an explicit human-only gate node with a no-op runner (gate
  present, no automated stage body — preferred if security review is a real gate we want
  to keep), or **(b)** remove `security` from the approvals defaults (if it was
  aspirational and unused). This is a Step-1 *acceptance item*, not a deferred unknown.

**Resolve before Step 2:**
- Engine choice: LangGraph (matches the original intent + checkpointer model) vs a
  minimal in-house DAG driver (no dep, full control). Step 1 is engine-agnostic, so
  this can be decided after Step 1.
- How much of triage (the failure classifier we shipped) folds into the engine's
  conditional edges vs stays in workers.

## 8. Recommendation
Land **Step 1 (declarative transition table)** as the next graph-engine increment —
it's the foundation, safe-ish, fully covered by existing transition tests, removes the
worst of the hand-rolled pain, and forces the `security`-gate resolution (§7) — ideally
with a human reviewing the extracted table. Defer Steps 2-3 (engine adapter +
DAG/parallel) to a supervised session per §5.

**But do not let Step 1 set the priority of Moat #1.** This PRD is debt repayment +
foundation; it produces no compounding artifact on its own. If the goal is to advance the
*defensible* part of Moat #1, the higher-leverage first move is the companion PRD's
**message bus + debate artifacts** (`docs/plans/message-bus-debate-prd.md`) — those
produce coordination evidence that compounds, and they don't block on this rewrite. Run
the two tracks in parallel; sequence by moat value, not by which is more architecturally
satisfying.
