# Cross-project memory — design (Moat #2 remainder)

> Stage: architect. Follows `docs/plans/memory-engine-upgrade-prd.md` (per-project memory:
> drift-proof WAL, supersede, retrieval routing, citations — all landed M1-M4). This designs
> the cross-project layer (product-vision §7). Autonomous-safe deliverable = this design; the
> build is a follow-up (additive, can be triple-checked). Grounded in `memory.py` (2026-06-03).

## 1. Today (per-project, owned)
`RoleMemoryStore(project_root)` owns `<project_root>/.goodteam/memory/roles/<role>/` with
`events.jsonl` (the WAL = sole truth), projections (.md/index.json/embeddings.jsonl, rebuildable),
a WAL-integrity marker (auto-rebuild on drift), supersede/`_active_events`, BM25/embedding
retrieval, and citations (jobs record `memory_citations`). Everything is **scoped to one
project_root**. There is NO retrieval across projects.

## 2. Principle (unchanged): own the ledger, cross-project is a read-only projection
Each project's `events.jsonl` stays the **sole owned truth**, written only via
`append_memory_event`. Cross-project memory is a **read-only retrieval/projection LAYER** that
*reads* multiple projects' WALs — it never owns or mutates them. This mirrors the existing
"projections are derived + rebuildable" rule, extended across project boundaries. External
memory frameworks (Mem0/Zep) stay OUT (pluggable behind this layer later).

## 3. First safe increment (additive, no per-project behavior change)
**A cross-project retrieval surface that aggregates over known project roots.**
- **Project registry**: enumerate known project roots (the projects dir / project configs the
  server already knows — reuse `projects.py` `load_project_target` + the projects listing the
  dashboard uses). No new source of truth; just the list of project_roots.
- **`CrossProjectMemory.retrieve(role_key, query, *, scope="all"|[keys], k)`**: run each
  project's existing `RoleMemoryStore.retrieve_relevant_events` (superseded-filtered, the
  landed retrieval) and merge-rank results, each tagged with its `project_key` + the existing
  `mem:` id (so a result is addressable as `<project_key>:mem:<id>`). Pure read; no writes.
- **Grounded citations across projects**: when a worker uses a cross-project fact, record the
  `<project_key>:mem:<id>` in the job's `memory_citations` (extend the existing citation field
  to carry the project qualifier). Keeps the audit trail honest across projects.
- **API + UI (optional, later)**: `GET /api/memory/cross?role=&q=&scope=` → ranked
  cross-project hits; surface in the Memory tab as a "cross-project" retrieval section.

**Out of the first increment** (later, additive): a unified cross-project search index
(vs per-project fan-out — fan-out is simpler + correct first); dedup of identical facts across
projects; promotion of a fact from one project to a shared "library" (vision §6.2 expert
library overlaps here); privacy/scoping controls (which projects may read which).

## 4. Risks / decisions to resolve before build
- **Scope/privacy**: should every project's memory be cross-readable by default? Recommend
  **opt-in scope** (caller passes `scope=[keys]`; default to the current project only unless
  explicitly widened) so cross-project reads are deliberate, not accidental leakage.
- **Performance**: per-project fan-out is O(projects) retrievals. Fine for tens of projects;
  if it grows, add a shared index (deferred). Start with fan-out (simple, correct).
- **Identity**: a `mem:` id is only unique within a project → cross-project ids MUST be
  qualified `<project_key>:mem:<id>` everywhere they cross the boundary (citations, API, UI).

## 5. Recommendation
Build the **§3 first increment** (cross-project read-only fan-out retrieval + project-qualified
citations, opt-in scope) as an additive, triple-checkable track — it does NOT touch the owned
per-project WALs or their write path, so it's lower-risk than the graph-engine parallel work.
Defer the shared index / fact-promotion / privacy-policy pieces to follow-ups. This delivers
the Moat #2 "cross-project" headline (vision §7) on top of the owned, drift-proof per-project
memory already shipped.

## 6. Backlog seed
- Build cross-project read-only fan-out retrieval (`CrossProjectMemory.retrieve`, opt-in scope,
  project-qualified `mem:` ids) + project-qualified citations + optional `/api/memory/cross`.
- Later: shared cross-project index; cross-project fact dedup/promotion; read-scope/privacy policy.
