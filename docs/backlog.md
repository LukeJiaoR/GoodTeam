# Backlog — ideas not yet scheduled

Durable list of feature ideas / follow-ups that aren't on the active plan yet.
Promote an item to a PRD under `docs/plans/` when it's picked up. Newest first.

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
- **Cleanup:** untracked `docs/plans/memory-embeddings-m2-*.md` (M2 plan docs never
  committed) and `~/.codex-postreset-20260601-222619` (codex reset snapshot, deletable).
