# D2 — collaboration at the gate vs the topo-driver rewrite (architect design)

> Stage: architect. Follows `graph-engine-collaboration-dag-design.md`. Grounded in an OFFLOADED
> read-only research survey (codex, 2026-06-03, cited file:line) — see the D2 research report.
> **D2 is the SUPERVISED step; this doc is for the founder's gate.** It revises the D2 framing
> based on what the research found.

## What the research changed in my recommendation
The original D2 was "`depends_on` + topological driver + first advisor node." The survey shows the
topo-driver rewrite is **premature and riskier than it looked**, and the moat-visible win can be
had additively without it:

1. **There is nothing non-linear to walk yet.** Only `advance_approved_workflow` loops, advancing a
   single scalar `state.next_node` via `workflow_node(next_node)` (graph.py:710-718). The pipeline
   is strictly linear. A topo driver + `depends_on` would add fan-in machinery whose ONLY consumer
   is the one advisor/committee node we'd add — i.e. building a DAG engine for a graph that is
   "linear + 1." (Same logic that deferred parallel-exec 3b.)
2. **Two real risks the rewrite must carry** (research-found):
   - **Checkpoint filename collision**: checkpoints are keyed by `state.current_node` only
     (state.py:68,75) → revisiting a node in one run overwrites its checkpoint. A topo driver that
     revisits nodes breaks the audit trail unless this is fixed first.
   - **Dashboard stepper desync**: the progress stepper uses a HARDCODED 13-step node order
     (web.py:4040-4057), NOT the table. Any reorder/new node silently fails to render. (Note: the
     workflow-graph viz we just shipped #49/#50 IS table-derived — so we'd have two
     differently-sourced views; reconcile before adding nodes.)
3. **The moat headline is "collaboration," not "topology."** product-vision §6.2's defensible part
   is committee/debate at gates — which we ALREADY built (#37-#43) and which attaches via the bus
   (`run_gate_debate` → gate-filtered `/api/messages`, debate.py:15, web.py:5183). Making that a
   first-class part of the gate flow is the visible win; the executor's internal walk shape is not.

## D2a — debate as an advise-only committee at the gate  (DO NOW; additive, low-risk)
Wire the already-built debate into the gate flow as an **advise-only** committee step:
- When the driver produces a gate (e.g. `rfc`, optionally `staging`), it MAY trigger
  `run_gate_debate(...)` (read-only; the existing entry point) so the challenge/rebuttal messages
  exist on the bus for that gate BEFORE the human decides. Human still arbitrates (no auto-decide) —
  consistent with everything we built.
- Declaration: add an additive, defaulted `advisors: tuple[str,...] = ()` (or `debate_at_gate: bool`)
  to the table (frozen-dataclass-additive, MappingProxyType-safe — research §2) so which gates
  convene a committee is DATA, not hardcoded. Empty default = today's behavior (no auto-debate).
- Surfacing: the messages are already gate-filtered and shown in the debate/message UI; OPTIONALLY
  enrich the approval card to show a "committee said" summary (small FE follow-up). No new ledger.
- Risk: low. It's additive, behind the stable API, reuses landed debate + bus + the human gate. No
  driver-loop rewrite, no checkpoint/stepper changes. Triple-checkable.

## D2b — `depends_on` + topological driver  (DEFER until justified)
The real chain→DAG executor leap. Build it when there is genuine fan-in (multiple advisor/expert
nodes feeding a decision, or dynamic per-run plans — vision §6.2), which makes the topo machinery
pay for itself. Prerequisites it must resolve first (research-found, currently latent):
- Fix checkpoint keying to not collide on node revisit (include an attempt/sequence suffix).
- Reconcile the hardcoded dashboard stepper with the table (single source of truth).
- Preserve the full transition regression net (research §6 enumerates the exact tests).
**Supervised, and not yet** — same "risk>value at current scale" call as 3b.

## Recommendation
1. **Build D2a now** (debate-as-committee-at-gate, advise-only, `advisors` table flag) — the
   moat-visible "collaboration DAG" behavior, additive + triple-checkable. This is the honest
   highest-value next step.
2. **Defer D2b** (topo driver) until there is real fan-in to justify it + its two prerequisites are
   fixed.
3. **Backlog now (research-found, independent):** (a) checkpoint filename collision on node revisit;
   (b) dashboard stepper hardcoded vs table (desync risk now that a table-derived graph view exists).

## The honest meta-point
"Graph-engine collaboration DAG" sounds like it demands a new executor. The grounded reading is the
opposite: we already shipped the collaboration *content* (bus/debate/memory) and a *declarative*
table + failure edges (#36/#48); the highest-leverage remaining move is to make collaboration
first-class at the gate (D2a), not to rewrite the walk for a graph that is still linear. I'd rather
say that than build an impressive-but-idle topo engine.
