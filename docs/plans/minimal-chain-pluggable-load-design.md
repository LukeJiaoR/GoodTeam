# Design — Minimal work chain + pluggable load

> Stage: **architect**. Formalizes the founder's framing: GoodTeam should **own a
> minimal work chain** as the stable core, and treat everything else — the full
> greenfield lifecycle, increment/fix work, special capabilities — as **pluggable
> load** on that chain. Grounded in the current code on `main`; file:line cited so
> it's actionable. This doc defines the abstraction and a **non-breaking** migration
> path; it does not change behavior by itself.

## 1. The core claim

The valuable, irreducible thing every piece of work went through — verified by
dogfooding GoodTeam to build GoodTeam — is a five-step loop:

```
intake → dispatch → verify → gate → record →(loop / changes_requested)
入什么    派给谁     活体证据   人决策   提交+审计+记忆
```

The current code **already implements this loop** — but it is *entangled* with a
single hardcoded greenfield chain (`_WORKFLOW_NODES`), so the only way to use the
loop is to run the full PRD→…→release ceremony. The refactor is not "add a second
workflow"; it is **separate the loop (engine) from the chain (data)**, so the same
engine can carry any load profile.

## 2. What already exists (and is correct)

The ingredients are present; they are just at the wrong level of generality.

| Ingredient | Where (current) | Verdict |
| --- | --- | --- |
| Worker registry | `CLI_BACKENDS` dict + `CliBackendSpec` (`workers.py:475`). Comment at `workers.py:784`: *"Adding a new CLI means adding a CliBackendSpec — no new branch."* | ✅ Already a clean plug-in registry. This is the pattern to lift up. |
| Gates as data | `_GATE_TRANSITIONS` (`workflow_table.py:77`) — 6 gates, each `on_approved_next` / `on_changes_requested` / `on_rejected` | ✅ Data, but hardcoded to one chain |
| Nodes as data | `_WORKFLOW_NODES` (`workflow_table.py:119`) — 5 nodes, each `run_guard_gate` + `runner_fn` + `depends_on` | ⚠️ Data, but `runner_fn` is a bespoke function string, and the table is the single greenfield chain |
| Dependency DAG | `depends_on` + `WorkflowExecutionPlan`/`workflow_execution_plan` (`workflow_table.py:29-66`) — topo levels, `scheduler=sequential` | ✅ DAG already extracted (D2 work) |
| Gate decision + advance | `decide_approval` → `advance_approved_workflow` (`graph.py:964+`) — run node, check `depends_on`, guard on `run_guard_gate`, transition | ✅ This IS the minimal-chain loop, entangled |
| Record (audit + memory) | `append_action` (audit-log.jsonl) + memory WAL (`append_memory_event`) + provenance airdrop | ✅ The "record" step, already owned |
| Evidence | `build_approval_evidence_snapshot` (`web.py:413`) | ⚠️ Exists but mostly `unknown` — see foundation gap #2 |

**Conclusion:** the engine and the registries are already here. The work is to
(a) name the minimal chain explicitly, and (b) demote the single greenfield chain
from "the workflow" to "the default load profile."

## 3. The minimal chain (the engine)

The engine is a mode-agnostic loop over a **work item** at a **node**:

```
advance(state):
  node = current node from the active load profile
  1. intake/guard : if node.depends_on gates not satisfied → route to that gate
  2. dispatch     : run node.worker (role + adapter), producing an artifact
  3. verify       : gather evidence (artifact + live smoke/CI/QA) → evidence_snapshot
  4. gate         : if node.run_guard_gate pending → await human/policy decision
  5. record       : on decision, append_action + memory event (+ provenance on land)
  → transition per the profile's GateTransition (approved/changes/rejected)
```

Steps 1/2/4/5 already exist in `advance_approved_workflow` + `decide_approval` +
`append_action`. Step 3 (verify) is the weak one (foundation gap #2). The engine
does **not** know about PRD, RFC, security, staging — those are *data in the
profile*, not branches in the engine.

## 4. Pluggable load

Three things plug into the engine. None of them is new machinery — each generalizes
something that already exists.

### 4a. Load profile = a named bundle of {nodes + gates + DAG}
A profile is exactly what `_WORKFLOW_NODES` + `_GATE_TRANSITIONS` are today, but
**named and selectable** instead of global:

- **`greenfield` profile** = the current table, verbatim → `takeover/prd/architect/
  rfc/security/staging/release/retro`. **Zero behavior change** when this is the
  default. This is the "heavy load."
- **`fix` profile** (light) = a tiny table: `fix_plan → implement → review → qa`
  (the defect-fix-track design). Enters at the implementation level, skips PRD/RFC.
- **`increment` profile** = `design_note → implement → review → qa` for small
  features.

`delivery_mode` (the 10-mode catalog in `projects.py:13`) collapses: a mode now
selects **(load profile + delivery standards)**, not a bespoke prompt path.

### 4b. Node = declared work, not a bespoke runner
Today `runner_fn` points at a hand-written function (`run_architecture_until_gate`,
…). Generalize a node toward **declaration**:

```
Node(name, role, run_guard_gate, depends_on, artifact_contract,
     worker=<from CLI_BACKENDS or a registered node-type>)
```

The existing runners become the **default node implementation** (dispatch a worker
of `role`, write `artifact`). Special nodes (security-scan, db-migration,
perf-audit) become **registered node-types** the same way CLI adapters are
registered in `CLI_BACKENDS` — add a spec, no new engine branch. `runner_fn` stays
as an escape hatch for genuinely bespoke stages during migration.

### 4c. Worker = already pluggable
`CLI_BACKENDS` stays as-is. A node names a `role`; routing maps role→adapter
(existing worker-routing). New agent ⇒ one `CliBackendSpec`. Unchanged.

## 5. What does NOT change (important)

- **The full greenfield lifecycle stays intact** — it is the `greenfield` profile,
  byte-for-byte the current table. We are not deleting PRD/architect/security/
  staging/release. The founder was explicit: do not negate the lifecycle.
- **Gate/approval/evidence/audit/memory machinery is reused unchanged** — both
  modes write the same `approval_decided` audit rows and memory events.
- **The DAG is preserved** — `depends_on` + `WorkflowExecutionPlan` move *into the
  profile* (each profile carries its own topo levels), so D2 work is not lost and
  inter-stage structure survives. The engine stays a thin scheduler over the
  profile's levels.

## 6. Migration path (non-breaking, phased)

- **Phase 0 — name the engine, one profile.** Introduce `LoadProfile` wrapping the
  current `_WORKFLOW_NODES` + `_GATE_TRANSITIONS` as the `greenfield` profile;
  route `advance_approved_workflow` through it. **No behavior change**; the full
  test suite must stay green. Pure structural refactor.
- **Phase 1 — add the `fix` profile.** Light table selectable at `start`. Delivers
  the increment/fix mode and is the substrate the defect-fix-track plugs into.
- **Phase 2 — declarative nodes + node-type registry.** Generalize `runner_fn` to
  `role + artifact_contract`; register special node-types like `CLI_BACKENDS`.
- **Phase 3 — engine services (the 6 foundations, §7).**

Each phase is independently shippable behind the existing gate/PR/CI/human-merge
loop, with a live smoke (per the repo's standing rule that unit tests + CI miss
path/integration bugs).

## 7. Foundation gaps — engine services that serve BOTH modes

These are not mode-specific; they harden the minimal chain for every profile:

1. **Defect → fix-track intake** — first-class `intake` for human-found bugs
   (`docs/plans/defect-fix-track-design.md`). The fix profile is its load.
2. **Live-smoke-as-evidence** — the `verify` step must populate `evidence_snapshot`
   (today ci/smoke/qa are `unknown`). Every real bug this session passed unit
   tests + CI and was caught only by live smoke. Make live smoke a gate input, not
   an afterthought — this is what makes "evidence-backed approval" true.
3. **Dispatch capability preflight** — before `dispatch`, check the adapter can run
   here (auth, model, TTY/PTY for agy, env). Would have caught the agy-PTY, codex
   gpt-5.5, and Telegram-localhost-URL losses.
4. **Operational memory** — "how to run the agents" knowledge (model per adapter,
   agy needs a PTY, rebuild dist after FE) belongs in GoodTeam's memory, not only
   in the operator's. Feeds `dispatch` + preflight.
5. **Build-state awareness** — detect stale `frontend/dist` vs source; the
   stale-build bug recurred again this session.
6. **Cross-project attention view** — "what needs me across all projects," of which
   the TG multi-project context issue is one symptom.

## 8. Why this fits the two moats

- **Multi-agent graph moat:** every change — greenfield or one-line fix — runs the
  *same* audited, gated minimal chain. The moat is "one control plane for all
  delivery," strengthened by removing the bespoke greenfield-only path.
- **Compounding memory moat:** profiles, node-types, and operational knowledge all
  accrete as owned, portable data (the same "own the ledger, pluggable engines"
  principle, now applied to the workflow itself). Adding a capability = adding
  data/specs, not forking the engine.

## 9. Open questions (for the build round)

1. **Profile selection UX** — explicit at `start` (a "what kind of work" picker), or
   inferred (a bug report auto-selects `fix`)?
2. **How declarative can a node get before Phase 2** — keep `runner_fn` for the
   greenfield nodes initially, or convert them to `role + artifact` immediately?
3. **Where the fix-track run lives** — sub-run linked to the project's history, or a
   top-level run with a back-reference (also asked in the defect-fix-track doc).
4. **Evidence verify ownership** — does the engine call the smoke runner, or does a
   `verify` node-type do it? Affects how live smoke populates `evidence_snapshot`.
