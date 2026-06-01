# PRD + architecture — memory engine upgrade (Moat #2 substance)

> Stage: product_requirement + architect. PRD/RFC gate. Founder: "memory is what
> matters most; today's memory is text that's easy to lose." Grounded in a
> read-only research survey (codex, 2026-06-02) — claims below carry file:line.

## 1. Intent (why)

Compounding memory is GoodTeam's deepest moat (vision §7): every run should make
the next sharper, and that's not copyable from a model release. The plumbing is
half-right but not yet trustworthy. The research survey establishes the real
state — NOT "all text so it's lost" (the WAL is sound), but four concrete defects:

1. **Projections drift and don't self-heal.** `events.jsonl` IS append-only truth
   (memory.py:225-227), and `rebuild_role_memory` can deterministically replay it
   (memory.py:348-369) — but no production path ever calls rebuild. `.md`/`index.json`
   are mutated in place on every append (memory.py:227, 412-431) and `read_memory_index`
   returns the on-disk file without revalidating against the WAL (memory.py:254-258).
   So an external/concurrent/erroneous write (cf. the CONTENTOPS App.jsx clobber) can
   silently corrupt the projection and nothing notices. **This is the real "easy to
   lose."**
2. **Retrieval degrades.** `summarize_markdown` truncates to top-8 headings / top-12
   bullets / 600 chars (memory.py:25-40) → noise at scale. The M2 embedding/BM25 path
   exists (retrieve_relevant_events, memory.py:274-313) but the web memory overview
   calls build_pickup_packet WITHOUT a query, so it shows summaries, not retrieval
   (web.py:1452; memory.py:344-346).
3. **No governance.** Only `action="add"` (memory.py:205-210); no conflict/supersede/
   staleness; dedup exists only in worker write-back (workers.py:557-563), not in the
   core append API. Contradicting facts persist forever.
4. **No grounded citations.** Retrieval yields stable `mem:` handles (memory.py:433-445)
   but artifacts/state record only paths, never which mem: event was used
   (state.py:23, artifacts.py:8-12). The "trustable memory" claim has no audit link.

## 2. Users & value

- **Operator / design partner:** memory they can trust — projections that can't
  silently corrupt, retrieval that returns signal not noise, and artifacts that cite
  the exact memory they drew on. This is the difference between "a workflow that
  writes notes" and "a system that compounds knowledge."
- **GoodTeam itself (self-host):** the failure library + decisions sediment reliably
  across runs (ties to triage's write-back).

## 3. Principle (non-negotiable)

**Own the ledger, pluggable engines.** `events.jsonl` is the sole source of truth.
Everything else (horizon .md, index.json, embeddings.jsonl) is a DERIVED projection
that must be rebuildable from the WAL and must never be trusted over it. Retrieval/
embedding engines are swappable; the WAL is not.

## 4. User-facing acceptance

- A corrupted/edited projection is **detected and auto-rebuilt from the WAL** on
  read — the pickup packet a worker gets can never be silently-stale relative to the
  events log. (Today: drift is undetected.)
- Worker retrieval uses the **embedding/BM25 path** (signal), not the
  summarize-truncation; summarize_markdown is demoted to display-only.
- A fact can be **superseded** (a newer event marks an older one obsolete) without
  breaking append-only — contradictions resolve instead of piling up.
- When a worker uses a memory fact, the artifact/result **records the `mem:` id(s)**
  it relied on — a grounded, auditable citation.
- All existing memory tests stay green; no real model needed in CI (embedding path
  has a deterministic fallback already).

## 5. Architecture / key decisions (decision record — review these)

1. **WAL-checksummed projections, auto-rebuild on drift.** `index.json` records a
   cheap integrity marker (e.g. event count + hash of events.jsonl). `read_memory_index`
   / `build_pickup_packet` compare it to the live WAL; on mismatch, call the existing
   `rebuild_role_memory` automatically instead of trusting the stale file. (Reuses
   rebuild_role_memory — already written, just never invoked in prod.) Rejected:
   trusting the on-disk projection (status quo, the corruption hole); rejected:
   rebuilding on every read (wasteful — only on checksum mismatch).
2. **Retrieval = embedding/BM25 by default; summarize = display only.** Route the
   worker pickup packet through retrieve_relevant_events (already there), and stop
   feeding summarize_markdown truncation into worker decisions. Keep summarize for
   the human-readable horizon view. (No new dep — M2 embedder is optional with a
   deterministic BM25/overlap fallback, memory.py:7-10,47-55.)
3. **Supersede, don't delete (append-only governance).** Add a `supersedes: <mem-id>`
   field to events (still action="add", still append-only); retrieval/projection
   treat a superseded event as obsolete. This gives conflict/staleness handling
   WITHOUT violating the WAL's append-only nature. Core append API also gains the
   dedup that today only lives in worker write-back. Rejected: physical
   delete/update (breaks the immutable ledger).
4. **Grounded citations.** When build_pickup_packet returns relevant_events with
   mem: ids, thread those ids through so a worker's result/state can record "used
   mem:abc, mem:def". Minimal: a field on the job/result; the dashboard already
   renders mem: handles (the drill-down we shipped). Rejected: full provenance graph
   (overkill for phase 1).
5. **Phased, test-first, no engine lock-in.** Each decision is independently
   landable and unit-testable (drift→rebuild, retrieval-routing, supersede, citation)
   with the fake/deterministic paths → all CI-gateable. We do NOT adopt an external
   memory framework (Mem0/Zep/etc., see prior-art note) — those are pluggable engines
   behind our owned WAL, a later option, not this upgrade.

## 6. Scope

**In:** the four decisions above as phased tracks, all over the existing
RoleMemoryStore + WAL, deterministically testable.

**Out (later):** cross-project / global memory store (vision §7.2 remainder);
an external memory engine (Mem0/Zep) behind the WAL; LLM-based memory summarization;
temporal/graph memory. Phase here = make the LOCAL per-role memory trustworthy
(no-drift) + signal-retrieval + governed + cited.

## 7. Tracks (issued after this gate; research already done)
- **A. drift-proof projections (Codex):** WAL integrity marker in index.json +
  auto-rebuild-on-mismatch in read_memory_index/build_pickup_packet (reuse
  rebuild_role_memory). Tests: edit/corrupt a projection → next read rebuilds it
  to match the WAL.
- **B. retrieval routing (Codex):** worker pickup packet uses retrieve_relevant_events;
  summarize_markdown demoted to display. Tests: pickup packet carries embedding/BM25
  results, not truncated summary, for a real query.
- **C. supersede + core dedup (Codex):** `supersedes` field + append-API dedup;
  retrieval/projection honor it. Tests: a superseding event hides the obsolete one.
- **D. grounded citations (Codex):** thread mem: ids from retrieval into the worker
  result/state; (dashboard already shows handles). Tests: a job that used memory
  records the mem: ids.

Sequencing: A first (it's the "easy to lose" fix the founder flagged + lowest risk,
reuses rebuild_role_memory). B, C, D follow; each independent.

## 8. Open questions (resolve at gate)
- **A's integrity marker:** event-count + sha256(events.jsonl) in index.json, compared
  on read? (cheap, deterministic) — recommend yes.
- **B:** any caller that legitimately wants the summary (not retrieval) for workers?
  Survey says web-overview uses no-query summaries for DISPLAY (fine, keep); only the
  worker pickup path switches to retrieval. Confirm no other worker path relies on
  summary.
- **C:** who emits `supersedes` — only explicit write-back, or also a heuristic? Recommend
  explicit-only in phase 1 (no heuristic contradiction detection yet).
- **D:** record citations on the job status (like failure_class) or in the artifact
  markdown? Recommend job status + result, minimal.

---

## Gate decision (2026-06-02): APPROVED

Founder approved all 4 decisions + all 3 open-question recommendations as written:
- D1 drift-proof projections (WAL-checksum + auto-rebuild via rebuild_role_memory) — **approved**
- D2 retrieval routing (worker pickup → embedding/BM25; summarize = display only) — **approved**
- D3 supersede-not-delete + core append dedup — **approved**
- D4 grounded citations (mem: ids on job status/result) — **approved**
- Q1 integrity marker = event-count + sha256(events.jsonl) in index.json, compared on read — **yes**
- Q2 switch ONLY the worker pickup path to retrieval; keep web-overview no-query summary for display — **yes**
- Q3 `supersedes` emitted explicitly only in phase 1 (no heuristic contradiction detection) — **yes**

Track order: A (drift-proof, lowest risk, reuses rebuild_role_memory) → B → C → D,
each independently landable + deterministically testable. External memory frameworks
remain out of scope (pluggable behind the WAL, later).
