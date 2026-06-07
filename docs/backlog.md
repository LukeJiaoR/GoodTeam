# Backlog — ideas not yet scheduled

Durable list of feature ideas / follow-ups that aren't on the active plan yet.
Promote an item to a PRD under `docs/plans/` when it's picked up. Newest first.

## ✅ RESOLVED 2026-06-07 — Dispatch artifacts (task contract / research / review) as first-class audit  (raised 2026-06-02, charter item)

**DONE across 2026-06-04/07 increments.** The durable audit chain now exists:
dispatch jobs retain `task_source`, `constraints_source`, optional
`research_report_source`, and an archived `research-report.md`; `land-pr.sh`
archives pre-review reports and optional `--review-rationale`; and
`.goodteam/provenance/pr-<PR>/manifest.json` links PR ⇄ jobs ⇄ contract ⇄ research
⇄ pre-review ⇄ review rationale ⇄ `land/pr-*` and `review/pr-*` memory sources.
The remaining question is not audit retention; it is whether/when to ingest full
research/review document text into role memory, which should be a separate
retrieval-budget-aware product decision.

_Original note (historical):_

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
- ~~**Checkpoint filename collision on node revisit**~~ **RESOLVED 2026-06-04 (#69).**
  `StateStore` checkpoints now use sequence-suffixed filenames (`start-001.json`,
  `start-002.json`, ...), and `latest_checkpoint_path` / project-scoped equivalent resolve the
  newest checkpoint. Covered by `tests/test_state.py::test_state_store_disambiguates_revisited_checkpoint_nodes`
  and project-scoped companion.
- ~~**Dashboard progress stepper is hardcoded, not table-derived**~~ **RESOLVED 2026-06-04 (#71).**
  The legacy dashboard stepper is derived from `GATE_TRANSITIONS` / `WORKFLOW_NODES` via
  `_dashboard_stepper_table_order()` and `_dashboard_stepper_steps()`. Covered by
  `tests/test_web.py::test_dashboard_stepper_order_follows_workflow_table_happy_path`.
- ~~**Dispatch `--verify` 127s in fresh worktrees**~~ **RESOLVED 2026-06-03 (PR #46).**
  `scripts/dispatch-track.sh` now idempotently links `$WORKTREE/.venv/bin -> $ROOT/.venv/bin`
  after worktree setup, so a relative `.venv/bin/python` resolves to the main venv (deps
  included); `.venv` is gitignored so it cannot leak into a commit. Verified empirically.
- ~~**Dispatch can leave a ZOMBIE job (status=`running`, empty `result.md`, NO completion
  notification) when the wrapper/worker dies mid-run**~~ **RESOLVED 2026-06-04 (#70).**
  Worker jobs now heartbeat while running, `WorkerJobStore.list_jobs()` reconciles stale running
  statuses, dead PIDs become `failed` with `failure_class=stuck` / `disposition=abandoned`, and
  live stale PIDs are surfaced as `possibly_stuck`. Covered by `tests/test_workers.py` stale/dead
  running-job cases.
- ~~**`GET /api/memory/cross` has no web UI**~~ **RESOLVED 2026-06-03 (PR #55).** Added a
  cross-project search card to the Memory tab (role + query + scope[+k]); scope defaults to "all"
  → resolves to the actual project-key list to satisfy the endpoint's required non-empty scope;
  ranked project-qualified hits, race-guarded.
- **Web UI is NOT yet a complete control surface for the self-hosting/orchestration loop** —
  🟡 **PARTIAL 2026-06-07.**
  (UI-coverage audit, 2026-06-03). The end-user product loop (idea→PRD→RFC→impl→staging→release→
  retro) is ~90% web-driven; but GoodTeam's own dev/orchestration toolchain
  (`dispatch-track.sh`, `land-pr.sh`, `graduate-release.sh`, `smoke`) was mostly CLI-only — the
  §6.2 "GoodTeam develops itself from its own UI" endgame. Landed slices: Dev Loop observability,
  web `Dispatch a track`, land-readiness / copy land command, and `Run Dry Smoke`. Remaining:
  graduate/tag panel and any server-side land/merge path remain gated; land is intentionally
  human-invoked.
- ~~**G3 pickup filter is slightly over-broad**~~ **RESOLVED 2026-06-04 (#68).**
  Pickup filtering now keys on the `worker_failure:` source marker rather than stripping human
  notes that merely start with `FAILURE [...]`. Covered by
  `tests/test_memory.py::test_pickup_packet_preserves_human_failure_heading_short_term_event`.
- **G3c — global cross-project failure-pattern library (hit-count)** — ✅ **LIBRARY RESOLVED
  2026-06-07 (`a93a693`)**, routing-readiness measurement added 2026-06-07.
  `FailurePatternLibrary` now stores
  signature/classifier/disposition hints, hit count, roles/nodes/sources, timestamps, and
  deterministic lookup; worker failure triage records patterns; `goodteam-os failure-patterns`
  emits repeated-pattern candidate metrics without changing routing. Applying pattern matches to
  alter routing remains a separate gate once candidate quality is validated.
- **G3 LLM triage agent** (deferred from the G3 design). G3a ships a DETERMINISTIC classify→route table covering known classes. Add a model-driven triage agent only for the `unknown`/ambiguous long tail, measured against the §12 hit-rate — don't run a model to route failures a table already handles.
- ~~**`land-pr.sh` ff-pull fails when a merged PR touches `workers.py`**~~ **RESOLVED 2026-06-04 (#66).**
  `scripts/land-pr.sh` now stashes/restores local tracked edits around `git pull --ff-only`, with
  trap-based restore on failure.
- ~~**agy CLI actually supports `--model` + `agy models`**~~ **RESOLVED 2026-06-06.**
  `DEFAULT_AGY_MODEL` is env-overridable via `GOODTEAM_AGY_MODEL`, `_agy_build_command` emits
  `--model` when non-auto, and `/api/agents` can use live `agy models` labels when available.
- ~~**Delivery Mode is narrow**~~ **RESOLVED 2026-06-05/06.**
  `DELIVERY_MODES` now includes static site, CLI tool, library, data pipeline, mobile app,
  feature increment, and custom standards. CLI, backend validation, React UI, and legacy renderer
  use the shared catalog.
- ~~**Air-drop land event references the newest job = the pre-review `code_review` job, not the
  implementation job**~~ **RESOLVED 2026-06-06.** `land_provenance_extract` now prefers non-review
  implementation jobs over newer `code_review` jobs. Covered by
  `tests/test_land_provenance.py::test_land_provenance_extract_prefers_non_review_job_over_newer_code_review`.
- ~~**`/api/fs/list` 403 permission branch untested at route level**~~ **RESOLVED 2026-06-06.**
  Covered by `tests/test_api.py::test_api_fs_list_route_maps_permission_error_to_403`.
- ~~**Legacy f-string dashboard renderer's delivery-mode select is stale**~~ **RESOLVED 2026-06-06.**
  `render_delivery_mode_options()` generates options from `DELIVERY_MODES`; covered by
  `tests/test_web.py::test_render_delivery_mode_options_uses_full_delivery_mode_catalog`.
- ~~**Detail-view repo paths read the wrong layer (latent, shows empty)**~~ **RESOLVED 2026-06-06.**
  Detail view reads `startContext.project_report?.repos?.<name>?.path`; covered by the frontend
  contract in `tests/test_web.py`.
- ~~**`pre-review.sh` can write an EMPTY report under heavy concurrent runs**~~ **RESOLVED
  2026-06-07 (`ab9df71`).** The helper ignores empty/failed reports, falls back across adapters on
  auth/provider failures, and now binds report/status selection to the current invocation marker so
  concurrent or back-to-back `code_review` jobs cannot copy another run's report.

## ✅ RESOLVED 2026-06-06 — Worker Routing Configuration: Simple / Advanced modes

Simple/Advanced worker routing landed in `frontend/src/App.jsx`: function buckets apply role
overrides, then switch users into Advanced review before save. Covered by
`tests/test_web.py::test_frontend_contract_has_simple_advanced_worker_routing_modes`.

## ✅ RESOLVED 2026-06-06 — Tiny: FilePath tooltip is unreadable

`FilePath` tooltip now uses `w-max max-w-[28rem] whitespace-normal break-words`; the visible chip
can still break long paths while the hover tooltip no longer collapses into a one-character column.

## i18n / multi-language support  (raised 2026-06-06, founder asked; DEFERRED — founder agreed)

Founder asked whether to add multi-language support. **Decision: defer.** Current state: the FE has NO i18n setup (no framework/locales/deps); `frontend/src/App.jsx` carries **260+ hardcoded English strings** (~228 JSX text nodes + 31 placeholders, plus toasts/templates), essentially all-English (only ~7 Chinese strings mixed in). Why defer: (1) the UI is mid-redesign (theme v1 pending) and features/copy are changing fast — extracting strings to i18n keys now means re-translating on every copy change (churn); (2) still single-user dogfooding — i18n pays off only with real users in a target locale; (3) it is a cross-cutting refactor of the single-file App.jsx that would conflict with every other FE track; (4) the real prerequisite is an unmade product decision: target market / primary language (Chinese-first? English-first? bilingual? more?). **Do it AFTER** the theme + core UX stabilize, as ONE clean pass: pick the framework (react-i18next or a light context), extract strings → keys, ship `zh`/`en` (or whatever the market needs) locale files + a language switcher placed alongside the theme switcher (same preferences surface). Medium–Large. Gating decision: target market / primary language.

## Raised 2026-06-05 (b) — product-use round 2 (founder using the dashboard)

- **PRD edit → LLM deepen → re-review loop** (founder, 2026-06-05; MAINLINE, design-first). Today
  editing a PRD/artifact and saving just persists the file: `handleSaveArtifact` POSTs to
  `/api/artifact` (content write) + refetches state — there is NO LLM follow-up. Founder wants:
  on save of a human-edited artifact, the LLM reads the edit, *deepens/refines* the doc, and routes
  it back for review (a refine-on-edit loop). Needs: a backend trigger on artifact save (or an
  explicit "Refine with AI" action) → dispatch the owning role with the edited doc + a "deepen,
  don't restart" instruction → write the refined version as a new artifact revision → surface a
  review/diff in the approvals UI. Design-first (research → design → founder gate → dispatch).
  Touches web.py (`/api/artifact`), graph/roles (a refine task), App.jsx (Save→Refine UX + diff).
  Medium–Large; high product value (closes the human-in-the-loop authoring loop).
  **Deferred to next round per founder ("等这次完全整理完再做").**
- **Theme System v1 — pick winner + productionize** (founder, 2026-06-05; DESIGN DONE, prototype
  built, NOT landed). Two switchable light themes (Vivid = refined Duolingo/Wise, Minimal =
  Claude-web) + semantic token layer + type scale + persisted switcher were prototyped on the
  home + project-detail slices and live-smoked (branch **`feat/theme-proto`**, worktree
  `../goodteam-dev-theme-proto`). Design: `docs/plans/theme-system-v1-design.md`. Remaining once
  founder picks a winner: (1) bump prototyped body/label text off the 11px floor to `text-sm/base`
  (13.5–15px) — the real "Duolingo-clarity" fix (agy leaned on `text-2xs`, clarity gain was modest);
  (2) full conversion of the remaining ~837 hardcoded hex + ~113 sub-11px usages to tokens across
  all screens (also systematically kills any remaining dark-mode-invisible text); (3) add dark mode
  for the winning theme (data-theme × prefers-color-scheme). FE → agy (model `Gemini 3.5 Flash
  (Medium)`). **Awaiting founder's Vivid-vs-Minimal pick.**
- ~~**Worker picker shows "claude" twice**~~ **RESOLVED 2026-06-06.** Agent discovery collapses
  aliases to canonical backend rows (`claude`, `agy`, etc.) while keeping aliases accepted on input.
  Covered by `tests/test_workers.py::test_discover_installed_agents_dedups_backend_aliases` and
  `/api/agents` tests.
- **codex pre-review channel was auth-down (403 Forbidden) this session** — 🟡 **PARTIAL
  2026-06-07.** `pre-review.sh --adapter` now falls back codex → claude → agy on auth/provider
  failure, detects 403/auth distinctly, ignores empty failed reports, and binds report selection to
  the current invocation marker. Remaining: actual Codex account/auth recovery is external.
- ~~**agy `--model` env-driven default works — productize it**~~ **RESOLVED 2026-06-06.**
  `GOODTEAM_AGY_MODEL` is committed, `_agy_build_command` emits `--model` when non-auto, and live
  `agy models` labels are surfaced when available.
- ~~**Role prompt template hardcodes "Use model gpt-5.3-codex"**~~ **RESOLVED 2026-06-06.**
  Worker prompts no longer hardcode the Codex model for non-Codex adapters; adapter/model are
  resolved from the actual job.
- ~~**sync_stage_tasks can leave stale `artifact_refs` on existing stage tasks**~~ **RESOLVED
  2026-06-06.** `sync_stage_tasks` now replaces and clears stage `artifact_refs`; covered by
  `tests/test_tasks.py::test_sync_stage_tasks_replaces_and_clears_stage_artifact_refs`.
- ~~**Workflow Graph never highlights the current GATE node + ignores non-approved decisions**~~
  **RESOLVED 2026-06-06.**
  (founder, 2026-06-06; "新项目 Workflow Graph 数据感觉被污染" → NOT pollution, verified). The run
  overlay is correctly project-scoped (VPSSSH showed its own run_id/current_node/approvals; fetch
  has a `graphRequestSeq` race guard). Landed fix maps runtime gate node ids to graph ids and colors
  approved / changes_requested / rejected / pending decisions distinctly; static transition labels
  remain possible-edge labels, not project state.
- ~~**New-project creation should git-init the repo (+ optional GitHub remote)**~~ **RESOLVED
  2026-06-06 (`bab42f9`).** New project creation supports default-on git init with initial commit,
  optional GitHub remote creation/push using `GITHUB_TOKEN`/`GH_TOKEN`, UI toggles, PAT guidance,
  and surfaced warnings. Existing repos are not touched.
- ~~**Workflow stages don't materialize as Agile Board tasks**~~ **RESOLVED 2026-06-06.**
  Real workflow stages now seed/sync Agile Board tasks, including stage artifact refs and status
  transitions. Covered by `tests/test_tasks.py` stage sync cases and web board tests.
