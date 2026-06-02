# PRD + architecture — message bus + debate artifacts (Moat #1, the compounding layer)

> Stage: product_requirement + architect. PRD/RFC gate. This is the **moat-value**
> half of Moat #1 — the coordination artifacts that compound and that a competitor can't
> copy (product-vision §6.2/§6.3). Its sibling, `docs/plans/graph-engine-prd.md`, builds
> the *executor*; this builds the *content* that rides on it. **Read the positioning note
> in that PRD first.** Grounded in a read-only code pass (2026-06-02); claims cite file:line.

## 1. Why this, and why it's the higher-leverage half

product-vision §6.3 is explicit about where the defensibility lives: **"every debate,
every triage decision, every expert brief becomes structured training signal… A frontier
model alone cannot replicate that."** The moat is not the graph executor — it's the
*coordination evidence* the team produces and accumulates. That evidence:

- compounds with usage (each run adds debates/decisions to a searchable corpus),
- is owned and portable (our ledger, not a vendor's),
- and is the audit primitive that wins regulated buyers (§6.2: "the audit primitive that
  wins regulated industries").

The graph-engine rewrite produces **none** of this on its own — it's plumbing. This PRD
produces the part that matters, and (key point, §6) it does **not** depend on that rewrite.

## 2. Current reality (grounded)

- **Gates are single-reviewer and binary.** `decide_approval` (web.py:555) sets
  `state.approvals[gate]` to one of `approved / changes_requested / rejected`;
  `gate_next_node` (nodes/review_gate.py:3-12) maps that to the next node. The only audit
  trace is one `approval_decided` action-log row (web.py append_action call ~599). There is
  **no challenger/responder trail, no quorum, no rationale capture** — exactly §6.2's
  diagnosis.
- **Agents communicate only by writing `result.md`.** Workers write
  `.goodteam/workers/jobs/<id>/result.md` (workers.py:446,647) and downstream roles read
  it. There is no structured ask/assert/reject/propose; no addressing; no threading.
- **A backlink pattern already exists.** `_write_back_provenance` (workers.py:606) and
  `_write_back_memory` (workers.py:570) already fan a completed job into the memory WAL via
  `append_memory_event(role_key, horizon=…, text=…, source="<job>/result.md", …)`
  (memory.py:199, dedup built in). We reuse this to make coordination evidence compounding.
- **Greenfield:** no `messages/`, `debate`, `quorum`, `advisor`, or `committee` references
  exist anywhere in `src/goodteam_os/`. This is additive, not a rewrite.

## 3. Non-negotiable principle: own the ledger, render projections

This is the same principle as memory (events.jsonl) and the workflow (StateStore /
action-log.jsonl). Applied here:

- **The message bus is its own append-only JSONL WAL, run-scoped.** It is the owned source
  of truth for inter-agent communication. It is **distinct** from the memory WAL (role
  knowledge, role-scoped, 3 horizons) and from StateStore (workflow state / action-log).
  Three ledgers, three concerns — do not merge them.
- **The human-readable debate `.md` is a DERIVED projection** of the relevant messages, not
  a separate source of truth. (Vision §6.2 sketches `artifacts/runs/<run>/debate/<gate>.md`
  as a file; we keep that as a *rendering* of bus messages, so there is one truth and the
  doc is rebuildable. This is a deliberate tightening of the vision's loose phrasing.)
- **Compounding happens by fan-OUT, not by co-location.** Selected high-signal outcomes
  (a final debate decision, an accepted `propose`) are fanned into the *memory* WAL via the
  existing `append_memory_event` path so they become searchable across runs. Raw chatter
  stays in the run-scoped bus.

## 4. Scope

**In (M1 — the minimum that compounds):**
1. **Message bus substrate** — a run-scoped append-only ledger + a small read/write API for
   structured messages (`ask / assert / reject / propose`), addressed and threadable.
2. **One structured debate round at ONE high-stakes gate** (recommend `architecture`/`rfc`,
   or `release`) — debate is a *protocol over the bus*, not a second mechanism: a challenger
   role posts `ask`/`reject` messages, the owner posts `assert`/`propose` rebuttals, a final
   decision is recorded. The human arbitrates with the structured argument visible.
3. **Memory fan-out** — the final decision + accepted proposals fan into the memory WAL
   (reusing `_write_back_provenance`'s pattern) so they compound and are citable later.
4. **A derived debate projection** (`.md`) rendered from the bus for the gate, surfaced in
   the existing approval UI so the reviewer sees the argument, not a bare binary.

**Out (explicitly — later increments, see §7):**
- Full quorum voting (`2 of {architect, security, product}`), multi-round debate trees.
- Auto-subscription / advisor routing, the triage agent rework, ephemeral expert synthesis
  (§6.2 items 3 & 5) — separate PRDs.
- Telegram/web live subscription to the bus (§6.2) beyond the single debate projection.
- Any change to the graph engine. M1 runs on **today's** if/elif ladder.

## 5. Design (M1)

### 5.1 Message schema (one bus event)
Append-only JSONL row, mirroring the memory-WAL discipline (stable identity fields):
```
{ "id", "run_id", "ts",
  "from_role", "to": ["role"|"broadcast"],
  "kind": "ask"|"assert"|"reject"|"propose",
  "gate": "<gate or empty>",          # set when the message is part of a gate debate
  "body": "<text>",
  "refs": ["<artifact path or job source>"],   # e.g. "<job>/result.md", "prd"
  "in_reply_to": "<message id or empty>" }
```
`id` = hash over identity fields (run_id, ts, from_role, kind, body, in_reply_to) — same
dedup philosophy as `_event_id`. Storage: `state/projects/<KEY>/messages/<run_id>.jsonl`
(run-scoped, under the owned StateStore tree). Append via a single
`append_message(...)` writer (failure-safe, dedup, like `append_memory_event`).

### 5.2 Debate protocol (over the bus, at one gate)
A gate may opt in via config `debate: { enabled: true, challenger_role: "<role>" }`.
When a run reaches that gate:
1. The owner's artifact (e.g. the RFC) is already written. The **challenger role** is
   dispatched read-only to produce 1–3 `ask`/`reject` messages (structured objections),
   `gate=<gate>`, `refs=[<artifact>]`.
2. The **owner role** is dispatched to post `assert`/`propose` rebuttals `in_reply_to` each.
3. A **decision record** is written (the gate stays human-arbitrated in M1): the human sees
   the rendered debate and calls `decide_approval` as today. The decision message
   (`kind:"assert"`, `body:"DECISION: …"`) is appended, and `decide_approval`'s existing
   `approval_decided` action-log row gains a `debate_ref` pointer.
4. **Fan-out:** the decision + any accepted `propose` are written to the memory WAL
   (`append_memory_event`, horizon=mid/long, source=`messages/<run_id>.jsonl#<id>`).

Note M1 keeps the **human at the gate** — debate *informs* the decision, it does not
auto-decide. Quorum/auto-arbitration is M2+. This keeps the risky "machine approves itself"
property out of the first increment.

### 5.3 Public surface (additive, stable)
- New: `append_message(...)`, `read_messages(run_id, …)`, `render_debate(run_id, gate)`.
- New: `GET /api/messages?run=<id>` and the debate projection surfaced in the existing
  approval panel (App.jsx approval UI). No change to `decide_approval`'s signature; it gains
  an optional `debate_ref` in the action row only.
- Unchanged: every existing gate that does **not** opt into debate behaves exactly as today.

## 6. Why this does NOT depend on the graph-engine rewrite

The bus is a JSONL ledger + a writer/reader; the debate is a dispatch protocol that runs
*inside the current gate flow* (`decide_approval` already exists and stays). Nothing here
needs conditional edges, a DAG, or LangGraph. When the graph engine lands (sibling PRD
Step 2/3), debate nodes become first-class graph nodes and the bus becomes the edge
substrate — but that's an upgrade, not a prerequisite. **Sequence by moat value: this can
start now, in parallel with or ahead of the engine.**

## 7. Incremental path
- **M1 (this PRD):** bus substrate + one human-arbitrated debate round at one gate + memory
  fan-out + a debate projection in the UI. Additive, opt-in, reversible.
- **M2:** multi-round debate; quorum config (`N of {roles}`) with the gate auto-resolving
  only on unanimous low-stakes, human-required otherwise; advisor subscription.
- **M3:** triage agent consumes the bus (failures become `reject` messages routed to a
  classifier); ephemeral expert synthesis posts briefs onto the bus. (Aligns the §6.2
  items 3 & 5 onto the same substrate.)

## 8. Risks + open questions
- **Cost/noise:** every debate = 2 extra dispatches per opted-in gate. Mitigate: opt-in per
  gate, cap challenger messages (1–3), CI/tests use fake workers (no real model).
- **Which gate first?** `rfc`/architecture (high design leverage, the architect already
  produces the artifact) vs `release` (highest stakes, best audit story). Recommend `rfc`
  for M1 — most design value, lowest blast radius. Resolve before build.
- **Projection vs source tension:** confirm the team accepts the bus-JSONL-is-truth /
  `.md`-is-derived decision (§3) rather than vision's literal `debate/<gate>.md` file.
- **`security` gate** has no stage runner (see graph-engine-prd.md §7) — do **not** attach
  the first debate to it until that gate's lifecycle is resolved there.

## 9. Recommendation
Build **M1** as the next Moat #1 increment, in **parallel with** the graph engine's Step 1
(disjoint files: this is new `messages` ledger + dispatch protocol + a UI panel; that is a
`graph.py` refactor). M1 is the first thing on the roadmap that produces a **compounding,
defensible artifact** — and it does it on today's workflow, without waiting for the engine.
Start narrow: one gate (`rfc`), one debate round, human still arbitrates. Then widen via
M2/M3. This is the half of Moat #1 that actually builds the moat.
