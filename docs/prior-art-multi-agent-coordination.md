# Prior art — multi-agent coordination (reference, not a plan)

Reference notes on external projects that solve pieces of what GoodTeam does, so
future technical-path decisions can compare against existing work instead of
rebuilding. Like the memory-engine survey (Mem0 / Letta / Zep-Graphiti), this is
a "borrow ideas / don't reinvent" cheat-sheet — not a commitment to adopt.

## hcom — https://github.com/aannoo/hcom

**What it is.** A single Rust binary that gives AI-agent CLIs (Claude Code,
Gemini, Codex, OpenCode, Antigravity) a shared **communication layer** across
terminal sessions — agents message each other, watch each other's transcripts /
file edits / status, and spawn subagents. Hook-based, local-first:
`agent → hooks → SQLite db → hooks → other agent`. No background daemon. Messages
can arrive mid-turn (injected between tool calls) or wake an idle agent.
**Collision detection (concurrent file edits) is on by default.** Optional
cross-device MQTT relay (XChaCha20-Poly1305), whose enrollment model is explicitly
"total trust" (any peer can launch/kill agents).

**How it relates to GoodTeam.** Different layer, not a competitor:
- hcom = a *coordination/comms substrate* (peer agents talk).
- GoodTeam = a *workflow OS*: role graph + human-approved **gates** +
  **compounding memory** (the two moats). hcom has no gates, no workflow, no
  memory WAL/retrieval.
- Both are local-first and both kill the manual copy-paste relay between agent
  CLIs — which is exactly what GoodTeam's `dispatch` (Moat #1 phase 1) does via an
  orchestrator-driven fan-out + per-track `verify`, vs hcom's peer-to-peer bus.

**Worth borrowing (ideas, mapped to our open problems):**
1. **Collision detection for concurrent edits.** Directly addresses our known
   limitation: parallel `dispatch` tracks share one working tree (`changed_files`
   is whole-tree), so we currently require file-disjoint tracks or per-track
   worktrees (release/dev bootstrap). hcom's built-in collision detection is a
   lighter alternative/complement — consider adding edit-collision detection to
   dispatch.
2. **Mid-turn messages / waking idle agents.** Richer than our batch
   "fan-out → wait → collect". Enables FE↔BE *negotiation* as an alternative to
   our "pin the contract up front in `--constraints-file`" approach (which has
   worked well — see the drill-down round). Useful when a contract can't be fully
   pinned ahead of time.
3. **Hook-based integration (no CLI modification).** Cleaner than our
   subprocess + prompt-file assembly; validates the adapter approach. Worth
   studying if our subprocess wiring gets brittle.

**Where it diverges / cautions (dissent):**
- **The relay's "total trust" model contradicts our §0.1 safety model**
  (approve outcomes not keystrokes; contained blast radius via isolation + gates).
  If hcom is ever used, single-machine local mode only — never the cross-device
  relay.
- It's **peer-to-peer dialogue**; we're **orchestrator-driven + independent CI
  gate**. Adopting hcom wholesale would dilute the gate model, which is our moat.

**Recommendation:** borrow ideas, don't adopt wholesale. Highest-value steal =
**edit-collision detection in dispatch**. hcom could in principle be a pluggable
*coordination engine* under "own the ledger, pluggable engines", but GoodTeam's
value (gate-graph + compounding memory) is precisely what hcom does not provide,
so we'd sit on top, not hand over the workflow.
