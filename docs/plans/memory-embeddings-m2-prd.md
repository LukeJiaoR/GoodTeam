# PRD + Architecture — M2 embeddings (cosine retrieval, BM25 fallback)

Status: **draft for review** (Claude planner/architect). Implementer = **Codex
(`gpt-5.3-codex`)**. QA gate = **CI** (see `memory-embeddings-m2-qa.md`).
Roadmap slot: the `embeddings` substep of Moat #2 / M2 (footprint 2026-05-30
"Moat #2 M2 hybrid retrieval"; vision §7.2 "Hybrid retrieval layer").

---

## 1. Intent (why, for whom)

Today role pickup packets are ranked by **BM25 over `events.jsonl`**
(`RoleMemoryStore.retrieve_relevant_events`, `memory.py:250`). BM25 is lexical:
it only matches events that **share tokens** with the task query. A worker whose
task says *"sqlite native addon rebuild after node upgrade"* will retrieve the
`better-sqlite3 ABI drift` event — but a task phrased *"the database binary stopped
loading after I bumped the runtime"* shares almost no tokens and retrieves nothing,
even though it's the same failure.

**This increment adds semantic (embedding) retrieval** so a relevant past fact is
found by *meaning*, not just shared words — with BM25 kept as the deterministic
fallback. This is the compounding-memory moat's quality lever: better recall of
prior facts → fewer rediscovered failures → the §7.3 switching-cost curve.

**For whom:** every dispatched worker (the consumer at `workers.py:355`), and
indirectly the human watching the Memory panel, who sees more on-target
`relevant_events`.

## 2. Scope

**In scope (this increment only):**
- A **pluggable embedder** abstraction + one reference local backend.
- A **rebuildable embedding projection** cache derived from `events.jsonl`.
- **Cosine top-K** retrieval inside the *existing* `retrieve_relevant_events`,
  with **BM25 as fallback** when no embedder is configured/available.
- Wire the embedder into the **one** production retrieval site (`workers.py`).

**Explicitly OUT of scope** (later M2 substeps — do NOT build here):
- Cross-project global store (`~/.goodteam/global/`).
- User-preference layer, failure-pattern library.
- Conflict/staleness (`supersedes`, `last_referenced_at`).
- Grounded-citation *rendering* of `mem:` handles in artifacts (handles already
  exist; rendering is its own track).
- True hybrid score fusion (RRF). We do **embeddings-primary, BM25-fallback**
  (binary), matching vision §7.2 wording — not a weighted blend.

## 3. User-facing acceptance

1. With an embedder configured, a task query that is **semantically related but
   lexically disjoint** from a stored event retrieves that event in the top-K,
   where BM25 alone returns it lower or not at all.
2. With **no** embedder (the default, and CI), retrieval behaves **exactly as
   today** — identical results, same `mem:` handles, same return shape. No
   regression, no new required dependency.
3. The return contract of `retrieve_relevant_events` /
   `build_pickup_packet(...)["relevant_events"]` is **unchanged**: a list of
   `{id, score, horizon, text, source, timestamp}` with stable `mem:<id>`
   handles. (`score` semantics change from BM25 score → cosine similarity; it was
   never contractually a fixed scale, but note it.)
4. First run with no credentials still works (the embedder is **optional**; absent
   ⇒ BM25). No model download happens in CI.

---

## 4. Architecture decision record

Each decision is anchored to a repo hard-principle. **Flagged `CONFIRM` items are
the ones a reviewer/human should veto now if they disagree — they set scope.**

### D1 — Pluggable `Embedder`, injected, default off `CONFIRM`
`RoleMemoryStore.__init__(self, project_root, embedder=None)`. `embedder` is an
optional callable `(list[str]) -> list[list[float]]`. **`None` means "no
embeddings, use BM25"** — it does NOT auto-detect.

- **Why:** honors *"own the ledger, pluggable engines"* — retrieval is a swappable
  projection behind one seam. Critically, **explicit injection makes every test
  host-independent**: a dev who happens to have the model wheel installed cannot
  silently flip the BM25 tests onto the embedding path. This is the direct lesson
  from the CI-red codex-`which` masking (footprint 2026-05-31 "CI LANDED"): the
  dev environment must not mask what CI sees. Auto-detection inside the store
  would reintroduce exactly that masking.
- Production wiring (`workers.py`) passes `embedder=default_embedder()`
  explicitly; everywhere else (tests, web overview) defaults to `None`/BM25.

### D2 — Embeddings are a **lazy, rebuildable projection**, not a write-time side effect `CONFIRM`
Embedding happens **lazily at retrieval time** into a sidecar cache, NOT inside
`append_memory_event`.

- **Why:** M1's #1 gotcha was *"single WAL write path; never couple it to anything
  heavy/failable."* Vision §7.2 says "embedded at write time," but write-time
  embedding would load a model inside the sacred append path and could fail a WAL
  write. Deliberate, principled deviation: the WAL stays pure and owned; the
  embedding cache is a projection that is **always rebuildable from
  `events.jsonl`** (delete the cache file ⇒ it regenerates). Same relationship
  `index.json` already has to the events.
- Consequence: a brand-new event is embedded the first time *any* retrieval runs
  after it's written, not at write. Acceptable — retrieval is where it's needed.

### D3 — Storage = `embeddings.jsonl` sidecar, keyed by stable event id + model `CONFIRM`
Per role dir: `roles/<role>/embeddings.jsonl`, one line per event:
`{"id": "mem:<12hex>", "model": "<name>", "dim": <int>, "vector": [floats]}`.

- **Why:** plain-text, inspectable, owned — matches the JSONL WAL ethos. Keyed by
  the existing stable `_event_id` so retrieval reuses cached vectors and only
  embeds **new** events. The `model` field invalidates the cache on model change
  (skip/re-embed rows whose `model` differs). Vision says `embeddings.parquet`;
  we choose JSONL to avoid pulling `pyarrow`/`pandas` (heavy) for corpora of
  hundreds–thousands of events. **Parquet noted as a future swap** if corpus size
  ever demands it — the projection shape makes that a drop-in.

### D4 — Reference embedder = `fastembed` (onnxruntime), as an **optional extra** `CONFIRM`
Add an optional dependency group `[embeddings]` (NOT in `[dev]`, NOT in CI):
`fastembed` providing `bge-small-en-v1.5` (384-dim).

- **Why:** `fastembed` is **onnxruntime-based — no `torch`** (sentence-transformers
  pulls hundreds of MB of torch). It runs **fully local/offline after first model
  download**, satisfying the vision's "small *local* model (bge-small)" and the
  local-first / data-owned moat (memory text is never sent to an external service
  — note: an API embedder would *publish* memory text, conflicting with the moat,
  so it's rejected as the default).
- **Caveat to pin for the implementer:** fastembed downloads the ONNX model from
  HuggingFace on first use (~hundreds of MB, cached under the user's HF cache).
  That is fine for opt-in local use but is why it is **never** in CI — CI runs the
  BM25 path and the fake-embedder tests, downloading nothing.
- `default_embedder()` returns a fastembed-backed callable **if the wheel is
  importable, else `None`** (same graceful-degrade pattern as `BM25Okapi is
  None`). Wrap the import in `try/except ImportError`.

### D5 — Keep a relevance gate; don't return weak matches
BM25 path keeps its token-overlap gate unchanged. Embedding path applies a
**cosine floor** (e.g. drop candidates below a small positive threshold) so a
query doesn't drag in every event at moderate similarity. Then rank surviving
candidates by cosine desc, take top-K.

- **Why:** preserves the existing "don't pollute the packet with irrelevant facts"
  property. The exact threshold is an implementation detail the backend doc pins;
  it must be chosen so the fake-embedder QA is deterministic.

### D6 — Backward-compat is contractual
`retrieve_relevant_events` keeps its signature, its empty-query/empty-corpus
guards, its `horizons` filter, and its exact return dict shape with `mem:`
handles. The FE Memory panel, `find_event` drill-down, and
`build_pickup_packet` all depend on this. The embedding path is **purely internal**
to the function.

---

## 5. Affected surface (verified read-only)

| File | Change |
|---|---|
| `src/goodteam_os/memory.py` | `Embedder` seam on `RoleMemoryStore.__init__`; `default_embedder()`; `_embed_events` (cache read/write); cosine path inside `retrieve_relevant_events`; BM25 path unchanged. |
| `src/goodteam_os/workers.py:344` | `RoleMemoryStore(self.project_root)` → pass `embedder=default_embedder()`. The **only** runtime retrieval site. |
| `pyproject.toml` | add `[project.optional-dependencies] embeddings = ["fastembed>=0.3"]`. NOT in `dev`. |
| `tests/test_memory.py` | new fake-embedder cosine tests; ensure existing BM25 ranking tests stay on the BM25 path (default `embedder=None`). |
| `docs/...` | this PRD + the two handoff docs. |

`web.py build_memory_overview` reads **stored** `role_pickup_packet.json` —
it does not recompute retrieval, so it needs **no change**.

## 6. Review-guidance gotchas (the reviewer's checklist before landing)

1. **`embedder=None` must mean BM25, never auto-detect.** If the store sniffs for
   fastembed itself, the BM25 tests become host-dependent (the masking trap). Auto
   detection lives ONLY in `default_embedder()`, called ONLY by production wiring.
2. **WAL untouched.** `append_memory_event` must not import or call the embedder.
   Grep the diff: nothing embedding-related inside the append path.
3. **Cache is a pure projection.** Deleting `embeddings.jsonl` must fully
   regenerate on next retrieval. No data lives only in the cache.
4. **Model-change invalidation.** A cached row whose `model` != current model is
   ignored/re-embedded, not blindly reused.
5. **Return shape byte-identical.** Same keys, same `mem:<id>` derivation
   (`_event_id` unchanged). Diff the dict construction against today's.
6. **Empty-query / empty-corpus / `horizons` guards preserved** on the embedding
   path too (tests at `test_memory.py:197`, `:233` must pass unchanged).
7. **No new *required* dep.** `fastembed` is optional; `pytest` with only `[dev]`
   installed must pass (the CI condition). If the import isn't guarded, CI goes
   red — that's the §0.3 gate doing its job, but we want green-first here.
8. **Determinism in tests.** New embedding tests inject a tiny deterministic fake
   embedder (e.g. hashed bag-of-words → fixed vector); they must NOT import
   fastembed or download anything.

## 7. Sequencing

PRD/architect (this doc) → **optional GPT-5.5 planner pass** refining
`memory-embeddings-m2-backend.md` (Claude reviews per §0.2) → Codex implements →
**CI is the QA gate** (`memory-embeddings-m2-qa.md`) → Claude diff-review vs this
record's 8 gotchas → human merges. Dispatch via `goodteam-os dispatch` into an
isolated dev worktree (release/dev bootstrap), single backend track.
