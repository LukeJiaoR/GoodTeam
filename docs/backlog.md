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

## Real graph engine: replace the hand-rolled "graph" with a true collaboration DAG  (raised 2026-06-02, charter item — co-equal mainline with the memory engine)

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

## code_review role: offload the mechanical pre-review, keep the merge-gate judgement  (raised 2026-06-02, charter item)

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

## Self-host the role chain: sediment Claude's manual orchestration into GoodTeam  (raised 2026-06-02, charter item)

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

## Agent discovery + per-role agent configuration UI  (raised 2026-06-01)

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

- **PRD generation is a deterministic template, doesn't use a real LLM** — the
  `product_requirement` role is mapped to gpt-5.5 in models.yaml but the PRD is
  produced by a fixed template (zero model call). Decide whether PRD should be
  LLM-generated for real quality. (Noted 2026-06-01.)
- **Moat #1 triage / self-correction** — worker failures dead-end into `blocked`
  with no retry/triage (the 403/token-refresh demo failures, npm 504, etc. eat
  time). Add failure classification + bounded auto-retry + escalation. The highest-
  leverage remaining Moat #1 piece (product-vision §6.2).
- **Borrow hcom's edit-collision detection** for dispatch's shared-worktree parallel
  tracks (see `docs/prior-art-multi-agent-coordination.md`).
- **Cross-project memory + grounded citations** — the remainder of Moat #2
  (product-vision §7).
- **Cleanup (resolved 2026-06-02):** the M2 plan docs are committed (verified git-tracked); the `~/.codex-postreset-*` snapshot was already deleted. Nothing pending here.
- **Memory event detail is unreachable via UI navigation** (found 2026-06-03, agy UI-test of #14-FE). The supersede chain + event detail render correctly, but there is no way to OPEN an arbitrary memory event's detail from the Memory tab — the `mem:` drill-down only fires on ids that already appear in rendered text (e.g. retrieved-fact bodies); top-level events aren't surfaced as clickable. Add a memory-event list/search panel (or surface a "details/supersede" affordance on the memory cards) so the supersede info is actually reachable. (agy had to force the detail open via React-Fiber injection to verify the render.)
- **Dispatch `--verify` 127s in fresh worktrees** (found 2026-06-03). `scripts/dispatch-track.sh --verify "...\.venv/bin/python..."` runs from the new worktree, which has no `.venv` → exit 127 → false `needs_human` even when the change is fine (confirmed by running the suite with the main repo's venv). Make verify resilient: point it at the main repo's `.venv`, or create/symlink a venv in the worktree, or detect-and-skip with a clear note. (Currently I re-confirm green manually with the main venv.)
