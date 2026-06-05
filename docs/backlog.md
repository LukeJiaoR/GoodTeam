# Backlog — ideas not yet scheduled

Durable list of feature ideas / follow-ups that aren't on the active plan yet.
Promote an item to a PRD under `docs/plans/` when it's picked up. Newest first.

## Dispatch artifacts (task contract / research / review) as first-class audit + memory  (raised 2026-06-02, charter item)

**Insight (founder):** the task.md contract Claude writes for each dispatch — plus
the research report it's based on and the review decision that lets it land — are
real engineering documents, not scratch. Today they're half-lost: the job dir keeps
task.md/constraints.md/context.md/result.md, but the /tmp contract originals are
deleted, the research report dies with its worktree, and the REVIEW decision/reasoning
lives only in the Claude↔human chat (nowhere on disk). The causal chain "PR ⇐ which
contract ⇐ which research ⇐ why the reviewer passed it" is not retained.

**Why it matters — both moats converge here:**
- Moat #1 (audit trail): a PR should be explainable — what contract dispatched it,
  on what research basis, on whose review judgement. That provenance is currently gone.
- Moat #2 (compounding memory): these task/research/review docs are HIGHER-signal
  memory than the worker's self-reported `## Learned` bullets. Writing them back means
  the next similar task can retrieve "last time we dispatched it like THIS, reviewed it
  like THAT." This is arguably the best thing to feed the upgraded memory engine.

**What exists vs gap:** job dir already persists task/constraints/context/result
(workers.py job protocol). Missing as first-class, run-linked artifacts: (a) the
research report, (b) the review decision + reasoning, (c) a link tying PR ⇄ job ⇄
contract ⇄ research ⇄ review into one provenance record.

**Posture:** record now; implement AFTER the memory-engine upgrade (B/C/D) — then
"write dispatch artifacts back to memory" becomes a natural APPLICATION of the new
engine (grounded citations + retrieval), not a separate silo. Ties to the
"sediment the role chain" charter (the orchestrator/release scripts already capture
the mechanical half; this captures the document/provenance half).

## 🟡 PARTIAL — Real graph engine: replace the hand-rolled "graph" with a true collaboration DAG  (raised 2026-06-02, charter item — co-equal mainline with the memory engine)

**STATUS 2026-06-04:** LANDED — declarative transition table (#36), D1 declarative failure/blocked
edges + bounded retry (#48), D2a debate-as-advise-only-committee-at-gate (#51/#52/#53), workflow-graph
observability endpoint+view (#49/#50). DECISION (committed designs): do NOT adopt LangGraph (in-house
table+driver; langgraph dep removed #34). DEFERRED with rationale: **D2b** topo driver (nothing
non-linear to walk yet; prereqs = checkpoint-collision + stepper-desync fixes) and **D3** parallel
fan-out (runtime_root not git; risk>value at 3 fixed workers) — revisit when the worker set is
larger/dynamic. langgraph hygiene done.


**The uncomfortable fact:** `langgraph>=0.2.0` is in pyproject.toml but
`src/goodteam_os/` imports it ZERO times. `graph.py` (named "graph") is NOT a
LangGraph graph — it's a hand-written linear state machine (`run_until_next_gate`,
`advance_approved_workflow`: a fixed takeover→PRD→RFC→impl→qa→release chain of
if/branches with gate checks, hand-rolled StateStore checkpoints). The langgraph
dependency is currently a lie — it claims an engine we don't use.

**Why this is the source of so many "scheduling" pains** (the ones we hit this
session): failures can only `next_node="blocked"` and dead-end → we hand-built
triage retry that a real engine's conditional edges/retries would give for free;
parallel tracks share one working tree (commingling) because a hand chain has no
fan-out/fan-in primitive; checkpointing/visualization/composability are all
bespoke; adding a role means editing a pile of ifs. This is exactly what
product-vision §6.2 calls out: the V1 fixed serial pipeline must become a true
**collaboration DAG** (nodes=tasks, edges=dependencies + comm channels) — the
Moat #1 core, not a bugfix.

**Decision posture (don't flip the table prematurely):**
- Immediate hygiene: EITHER start using langgraph OR remove the unused dependency —
  right now it misleads anyone reading the code. (Cheap; do soon.)
- Mainline (large, high-risk — touches the whole workflow core): drive the workflow
  with a real graph engine (LangGraph or equivalent) implementing the collaboration
  DAG. Needs its own PRD.
- "Own the ledger, pluggable engines" applies here too: the engine (LangGraph) must
  NOT own our state ledger — same principle as memory (the WAL is ours, the engine
  is swappable). Keep StateStore/events as the owned truth; the graph engine is a
  projection/executor over it.
- Sequencing: co-equal mainline with the memory-engine upgrade; both come AFTER the
  current in-flight work (PRD-real-LLM) wraps. Order memory vs graph-engine when we
  get there.

## ✅ RESOLVED 2026-06-02 — code_review role: offload the mechanical pre-review, keep the merge-gate judgement  (raised 2026-06-02, charter item)

**DONE (#34 `scripts/pre-review.sh`).** Independent codex `code_review` pre-review on each PR
(mechanical report: lane/contract-fields/obvious-bugs/test-sufficiency, file:line) feeds the Claude
merge-gate judgement; the implementing model never gates its own work. Used on every land this
session and repeatedly caught real bugs CI was green on (same-origin bypass, save data-loss,
cross-gate dedup, closure). The judgement+land gate stays human, exactly as the charter prescribed.


**Founder:** review feels mechanical, another model could do it. **Half true** —
split it, because the evidence this session says the judgement half is the only
thing that caught the disasters:
- CONTENTOPS hardcode: CI green, worker exit 0, every automated check passed — only
  reading task.md's intent caught that the RIGHT code built the WRONG product.
- 3 project_root bugs: unit tests green (they built paths directly); review + live caught them.
- `_event_id`-breaks-on-new-field: caught by REASONING over the research report, not by any check.

**The split (mirror how we offloaded `research`):**
| review layer | nature | offload? |
|---|---|---|
| lane respected? touched files outside whitelist? | mechanical (diff filename check) | YES |
| tests cover it? ruff/CI green? | mechanical | already CI |
| contract fields aligned (e.g. mem:id field names)? | semi-mechanical | YES (a codex pass) |
| is this doing the RIGHT thing? idea-bleed / side-effects / architecture violation? | JUDGEMENT | NO — this is the only disaster-catch |

**Mechanism = "pre-review":** after a PR opens, dispatch a codex `code_review` job
that returns a mechanical report (lane check, contract-field alignment, obvious bugs,
test sufficiency, file:line cited). Claude reads that report + makes the
"is it doing the right thing" judgement + the land decision. Same shape as research:
mechanical part offloaded, judgement kept.

**Hard constraint (independence, §0.3):** do NOT let the implementing model review
its own work as the GATE — codex executed the CONTENTOPS error flawlessly and
self-reported success; another codex instance lacks the out-of-band user intent
("they asked for a task tracker") that the reviewer holds. Pre-review can be codex;
the merge-gate judgement stays Claude (or a model that did NOT write the code AND
holds the intent). This is the "judgement layer automates LAST, only once
verification is trustworthy" rule from the role-chain charter.

## 🟡 PARTIAL — Self-host the role chain: sediment Claude's manual orchestration into GoodTeam  (raised 2026-06-02, charter item)

**STATUS 2026-06-04 (founder priority: "以自我驱动为先"):** mechanical layers sedimented —
`dispatch-track.sh` (orchestrator), `pre-review.sh` (code_review mechanical half), `land-pr.sh`
(release merge/cleanup), `commit-docs.sh` (retro). NOW on GoodTeam's OWN surface: **SA-1** Dev Loop
observability panel (sees its own worktrees/PRs/CI/pre-reviews, #57/#58) + **SA-2** "Dispatch a
track" action (starts its own dev work from the dashboard, async + same-origin guard, #59/#60).
REMAINING: **SA-3** land = surface readiness only, merge stays the HUMAN gate by design (do NOT
auto-merge from the server — it'd `gh pr merge` + `git checkout/pull` the server's own repo);
deeper sediment = orchestrator/release as first-class GoodTeam workflow NODES (not just scripts) —
the §6.2 endgame, larger, gated. Sediment-order hard rule still holds: judgement/auto-land LAST.


**Insight:** the steps the planner/reviewer (Claude) performs by hand to ship each
change ARE GoodTeam's own role chain, still run manually. Every manual round is a
live, pressure-tested spec for a role GoodTeam hasn't automated yet. Sedimenting
them turns "Claude hand-drives GoodTeam's development" into "GoodTeam develops
itself" — the Moat #1 endgame (product-vision §6.2).

**Manual step → GoodTeam role → status:**
- write PRD / contract → product_requirement + architect → semi (PRD is a template, no LLM — see below)
- start worktree + write task/constraints + dispatch → planner + orchestrator → MANUAL (Bash by hand) → first sediment = `scripts/dispatch-track.sh`
- review diff, decide mergeable → code_review → MANUAL (this is the core judgement)
- merge + cleanup + sync → release → `scripts/land-pr.sh` landed (mechanical half only)
- write footprint → retro_memory → manual

**Sediment ORDER (hard rule):** mechanical layers first (release's "green + human
ok → merge/cleanup"; orchestrator's "spin worktree + dispatch a track"); the
review/judgement layer LAST, and only once verification is trustworthy. We proved
repeatedly that CI-green ≠ correct (the CONTENTOPS hardcode passed CI clean), so
`release` must NOT auto-merge on green alone — judgement stays human until an
independent verifier (triage classes + CI + maybe an LLM reviewer) is proven. The
hard-won rules these roles must encode (from the manual rounds): review BEFORE
merge; remove worktree before --delete-branch; pin the task contract to avoid
idea-bleed; read the produced artifact before declaring success; `git add` only
whitelisted files never -A; CI-green is necessary not sufficient.

**Process rule reaffirmed (2026-06-02):** Claude must NOT write implementation code
OR scripts itself — those go to Codex via dispatch. Claude = specs/PRD/contracts/
review/footprint; Codex = all code + scripts. (Slipped a few times this session —
demo PRD/README/land-pr.sh were hand-written "because small"; that erodes the
implementation-role boundary that makes the chain work. land-pr.sh kept as a
one-time exception; from here, scripts go to Codex.)

**Process rule added (2026-06-02): INVESTIGATION is also offloadable — Claude was
over-claiming it.** Producing each PRD/decision this session, Claude did the
fact-gathering (grep who-imports-langgraph, how-memory-is-stored, where-render_prd-
is-called) in its own expensive context. That's the `research` role (one of
GoodTeam's 13, never used), not the planner's. Split it:
- **research / fact-gathering → offload** to Explore (read-only fan-out subagent)
  or a codex/gemini "survey current state of X, return facts with file:line"
  dispatch. Facts must be CITED (file:line) so the judgement layer can spot-check —
  the safeguard is verifiable facts, NOT "Claude must read it himself" (Claude
  misreads too: the stale-App.jsx misdiagnosis proved it).
- **judgement / decisions / PRD / contracts → Claude keeps.** Reasoning over the
  facts (e.g. "langgraph unused ⇒ scheduling pain", "WAL sound but projections
  fragile", contract trade-offs) stays with the reviewer/architect.
- **review / merge-gate → Claude keeps.**
- Apply next time on the memory-engine / graph-engine mainlines: run a research
  pass (Explore/subagent) → Claude reads the cited report + decides + writes the
  PRD → Codex implements → Claude reviews. This also starts exercising GoodTeam's
  `research` role for real (same as we're sedimenting orchestrator/release).

## ✅ RESOLVED 2026-06-04 — Agent discovery + per-role agent configuration UI  (raised 2026-06-01)

**DONE (#15 agy backend, Track #4 discovery+config, #63/#65/#64 picker).** Agent discovery
(`discover_installed_agents`/`GET /api/agents` with readiness) + per-role agent selection for the
FULL role set + per-agent **model** picker (curated catalog + codex live `codex debug models` +
"Custom…") + per-role model persistence (`role_model_overrides`). All CLI backends savable. The
original want is fully delivered. (Remaining nit: agy `--model` reconciliation — see below.)

_Original note (historical):_

**Want:** users should not be stuck with hardcoded agent choices. Two pieces:
1. **Discover locally-installed AI agents** — scan PATH (and known install dirs)
   for the agent CLIs GoodTeam can drive (codex, claude/anthropic, gemini, agy/
   antigravity, …), report which are present + auth-ready. No more fixed candidate
   list assumed installed.
2. **Per-role configuration page (dashboard)** — a UI where the user picks which
   agent runs each role (e.g. ui→agy, implementation→codex, qa→claude), instead of
   hand-editing `projects/*.local.yaml`. Writes the `workers:` map.

**What already exists (start a PRD from here, don't rebuild):**
- Per-role routing is REAL: `projects.py` `workers:` map + `graph.resolve_role_adapter`
  dispatch each role to its configured backend.
- Extensible registry: `workers.py` `CLI_BACKENDS` (codex/anthropic/claude/gemini/
  agy/antigravity). Adding a CLI = one `CliBackendSpec` (agy was just added, PR #15).
- Readiness probing: `worker_backend_doctor(mode)` reports CLI-on-PATH + auth
  presence + a hint, per backend.
- An earlier worker-backends config panel exists (`docs/plans/worker-backends-panel-*.md`,
  `/api/project-config`) — but it configures the GLOBAL mode / a fixed role set, not
  free per-role agent selection, and has NO discovery.

**The gap to close:** (a) a discovery function/endpoint that enumerates which
registered backends are actually installed+ready on this machine (extend
`worker_backend_doctor` across all `CLI_BACKENDS` keys, dedup aliases); (b) extend
the config endpoint + dashboard panel to let the user assign any discovered agent to
any role and persist it to the project config's `workers:` map; (c) surface
readiness/auth hints inline so a user knows before a run whether their pick will work.

**Tracks when promoted:** PRD (Claude) → backend discovery+config endpoint (Codex)
→ frontend per-role selector panel (Codex; or agy/Gemini for the UI) → QA.

---

## Other open follow-ups (smaller)

- ~~**PRD generation is a deterministic template, doesn't use a real LLM**~~ **RESOLVED 2026-06-04
  (#62/#61).** Default-flow PRD/RFC now generate via the LLM worker when the role's backend is real
  (adapter ∉ BUILTIN_WORKER_MODES), with graceful template fallback (fake/not-ready/worker-failure);
  zero-cred onboarding preserved.
- **Moat #1 triage / self-correction** — 🟡 **PARTIAL 2026-06-03 (#48 D1, #56 G3a/G3b).** Declarative
  failure edges + bounded retry (D1) + deterministic classify→route dispositions
  (escalate_human/block) + failure capture to memory (G3a/G3b) landed. REMAINING (deferred):
  **G3c** global failure-pattern library (hit-count, §12 north-star) + the **LLM triage agent** for
  the unknown long tail — both await accumulated failure data + own gates (see items below).
- **Borrow hcom's edit-collision detection** for dispatch's shared-worktree parallel
  tracks (see `docs/prior-art-multi-agent-coordination.md`). *(Still open; not needed yet —
  parallel tracks use disjoint worktrees, not a shared tree.)*
- ~~**Cross-project memory + grounded citations**~~ **RESOLVED 2026-06-03 (#45 + #55 + citations).**
  Read-only cross-project fan-out retrieval (`/api/memory/cross`) + the Memory-tab search UI +
  grounded `mem:` citations/supersede landed. (Follow-ons — shared index, fact dedup/promotion,
  read-scope policy — remain, lower priority.)
- **Cleanup (resolved 2026-06-02):** the M2 plan docs are committed (verified git-tracked); the `~/.codex-postreset-*` snapshot was already deleted. Nothing pending here.
- ~~**Memory event detail is unreachable via UI navigation**~~ **RESOLVED 2026-06-03 (PR #47).**
  Added `RoleMemoryStore.list_events` + `GET /api/memory/events` + a per-role "Browse events"
  search panel in the Memory tab that reuses the existing detail modal (superseded events
  flagged, kept not dropped). agy finding closed.
- **Checkpoint filename collision on node revisit** (found 2026-06-03, D2 research). StateStore
  checkpoints are keyed by `state.current_node` only (`state.py:68,75`) → revisiting a node in one
  run overwrites its checkpoint, losing audit history. Must be fixed before any topological/looping
  driver (D2b) that can revisit nodes; add an attempt/sequence suffix.
- **Dashboard progress stepper is hardcoded, not table-derived** (found 2026-06-03, D2 research).
  The stepper uses a hardcoded 13-step node order (`web.py:4040-4057`), NOT `WORKFLOW_NODES`/
  `GATE_TRANSITIONS`. The new workflow-graph viz (#49/#50) IS table-derived → two differently-sourced
  views that can desync; any added/reordered node silently fails to render in the stepper. Reconcile
  to a single source of truth (the table) before adding nodes.
- ~~**Dispatch `--verify` 127s in fresh worktrees**~~ **RESOLVED 2026-06-03 (PR #46).**
  `scripts/dispatch-track.sh` now idempotently links `$WORKTREE/.venv/bin -> $ROOT/.venv/bin`
  after worktree setup, so a relative `.venv/bin/python` resolves to the main venv (deps
  included); `.venv` is gitignored so it cannot leak into a commit. Verified empirically.
- **Dispatch can leave a ZOMBIE job (status=`running`, empty `result.md`, NO completion
  notification) when the wrapper/worker dies mid-run** (found 2026-06-03; a codex UI-coverage audit
  job died — `status.json` frozen at `running`, no heartbeat past its last `updated_at`, process
  gone, `result.md` 0 bytes, and the orchestrator never reported completion/failure). Needs
  heartbeat-timeout detection + status reconciliation so a dead worker is marked `failed`
  (not stuck `running`) and surfaced. Until then: re-check `status.json` mtime/process liveness and
  re-dispatch manually.
- ~~**`GET /api/memory/cross` has no web UI**~~ **RESOLVED 2026-06-03 (PR #55).** Added a
  cross-project search card to the Memory tab (role + query + scope[+k]); scope defaults to "all"
  → resolves to the actual project-key list to satisfy the endpoint's required non-empty scope;
  ranked project-qualified hits, race-guarded.
- **Web UI is NOT yet a complete control surface for the self-hosting/orchestration loop**
  (UI-coverage audit, 2026-06-03). The end-user product loop (idea→PRD→RFC→impl→staging→release→
  retro) is ~90% web-driven; but GoodTeam's own dev/orchestration toolchain
  (`dispatch-track.sh`, `land-pr.sh`, `graduate-release.sh`, `smoke`) is CLI-only — the §6.2
  "GoodTeam develops itself from its own UI" endgame. Candidate UI actions: Run-Smoke button
  (`/api/smoke`), custom-dispatch button, Land-PR action, graduate/tag panel.
- **G3 pickup filter is slightly over-broad** (found 2026-06-03, pre-review on #56). `RoleMemoryStore._normal_pickup_content` strips short-term lines that start with `- FAILURE [` OR carry the `worker_failure:` source. A legitimate human/worker short-term note literally starting with "FAILURE" would be hidden from the pickup packet (display-only — the event is still stored; retrieval already keys on source only). Tighten the pickup filter to key on the `worker_failure:` source marker alone. Low.
- **G3c — global cross-project failure-pattern library (hit-count)** (deferred from the G3 design, 2026-06-03). The §7 compounding payoff: signature → classifier hint → recommended action → hit count, readable across projects, applied on match. Needs a NEW global (non-per-project) store: path resolver + mutable schema + write/update + retrieval (none exists today; cross-project is read-only fan-out). Build AFTER G3b failure-capture accumulates real events; own design + gate. The failure-library hit-rate is the §12 north-star metric.
- **G3 LLM triage agent** (deferred from the G3 design). G3a ships a DETERMINISTIC classify→route table covering known classes. Add a model-driven triage agent only for the `unknown`/ambiguous long tail, measured against the §12 hit-rate — don't run a model to route failures a table already handles.
- **`land-pr.sh` ff-pull fails when a merged PR touches `workers.py`** (found 2026-06-04, during #63 land). The intentional uncommitted local edit `DEFAULT_CODEX_MODEL = os.getenv(...)` (workers.py:20) blocks `git pull --ff-only` whenever a landed PR also modifies workers.py (git refuses to update a file with uncommitted changes, even non-overlapping). Resolved manually via stash→pull→pop (the edit re-applies cleanly since line 20 is untouched upstream). Make `land-pr.sh` stash/restore uncommitted local edits around the pull (or document the model-config as a committed env-overridable default so there's no standing local edit). Recurs for any workers.py-touching PR.
- **agy CLI actually supports `--model` + `agy models`** (found 2026-06-04, CLI probe), contradicting GoodTeam's `_agy_build_command` comment "agy has no --model flag" (workers.py ~240). agy can list models (`agy models`, display labels only, no stable ids) and accept `--model`. Reconcile: GoodTeam could route a model to agy after all; revisit agy's command builder + whether to expose agy models (labels) in the picker.
- **Delivery Mode is narrow** (found 2026-06-05, founder using the product). Only 3 hardcoded modes (`projects.py:13` `mvp`/`init_project`/`multi_service`), all Web-product oriented; each just injects a `DELIVERY_MODE_STANDARDS` text block (`graph.py:25`) into role prompts. Doesn't cover static site/landing, CLI tool, library/SDK, data pipeline/ETL, mobile, ML/notebook, incremental feature on an existing system, content/docs project. Hardcoded in 4 places (projects.py / cli.py / graph.py / App.jsx select). Proposed (founder to pick direction; dismissed for now): preset modes + a `Custom…` free-text standards option, isomorphic to the model picker's catalog+Custom. Medium.
- **Air-drop land event references the newest job = the pre-review `code_review` job, not the implementation job** (found 2026-06-05, first live Air-drop on #74). `land_provenance_extract` picks the newest `.goodteam/workers/jobs/*/status.json` by mtime, but `pre-review.sh` runs a `code_review` job in the same worktree later, so the land event records `dispatch_role=code_review` + that job's run_id/job_id. Archive still captures all job dirs (no data loss); only the event's job linkage is wrong. Fix: pick the implementation job (exclude review roles, or prefer the earliest/non-review job). Low–Medium.
- **`/api/fs/list` 403 permission branch untested at route level** (found 2026-06-05, pre-review on #74). Handler returns 403 for unreadable dirs but only 400/404 are asserted in `tests/test_api.py`. Add a monkeypatch test (force `iterdir`→`PermissionError`), platform-independent (real chmod is flaky under root/CI). Low.
- **Legacy f-string dashboard renderer's delivery-mode select is stale** (found 2026-06-05, pre-review on #76). The canonical React UI (App.jsx) now lists all 9 presets + custom, but the NON-canonical legacy f-string dashboard renderer in `web.py` (~4308 + the add-project dialog ~5305) still hardcodes only mvp/init_project/multi_service. Generate those option lists from `DELIVERY_MODES` to avoid drift — or delete the legacy renderer outright (CLAUDE.md already marks it not-canonical). Low.
- **Detail-view repo paths read the wrong layer (latent, shows empty)** (found 2026-06-05, while fixing #81). `frontend/src/App.jsx:3069/3073/3077` read `startContext.project_report?.project?.repos?.docs|backend|frontend`, but the backend shape is `project_report.repos.<name>.path` (repos is top-level under project_report; `project_report.project` only has name/key/plane_project_id). So the project-detail repo path display has silently been empty. Fix to `project_report?.repos?.<name>?.path`. (The create-form hydration had the same bug and was fixed in #81.) Low.
- **`pre-review.sh` can write an EMPTY report under heavy concurrent runs** (found 2026-06-04, Wave 1). When several pre-reviews ran concurrently, some `/tmp/pre-review-<PR>-report.md` came back empty (the report-copy / newest-result.md selection appears to race across concurrent throwaway worktrees). Re-running sequentially produced correct verdicts. Fix: make the report path/selection robust to concurrency (key the copied result.md to the PR's own job, not "newest"), or serialize. Workaround: run pre-reviews sequentially.
