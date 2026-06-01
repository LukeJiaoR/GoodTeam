# Backend handoff — M2 embeddings (Codex)

Cold-start, self-contained. Implementer: **Codex (`gpt-5.3-codex`)**. Lane =
backend only. Full rationale: `memory-embeddings-m2-prd.md` (read §4 decisions +
§6 gotchas). QA: `memory-embeddings-m2-qa.md`.

---

## Workspace

- Repo root: `/Users/luke/WorkSpace/codeAi/GoodTeam`
- Dispatched into an **isolated dev worktree** (release/dev bootstrap). Work there.
- **Only touch:** `src/goodteam_os/memory.py`, `src/goodteam_os/workers.py`,
  `pyproject.toml`, `tests/test_memory.py`.
- **Do NOT touch:** `frontend/`, any other `docs/plans/*.md`,
  `docs/current-progress.md`, `README.md`, the FE Memory panel, `web.py`.
- **Do NOT commit, branch, or push.** Report what you changed. The human lands
  after CI. When you `git add`, add ONLY the four files above — never `git add -A`
  (the worktree accumulates `.goodteam/` runtime state; it's gitignored, keep it
  out of any staging).

## Setup

```bash
python -m venv .venv && . .venv/bin/activate   # if not already
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest tests -q        # baseline: should be all green
```

Do **not** `pip install fastembed` to make tests pass — the tests must pass on
`[dev]` alone (that's the CI condition). Use the fake embedder for tests.

## Background (verified facts — treat as ground truth)

- Retrieval today: `RoleMemoryStore.retrieve_relevant_events(role_key, query,
  *, top_k=5, horizons=None)` at `memory.py:250`. It tokenizes the query, reads
  events via `self._read_events(role_key)`, optionally filters by `horizons`,
  scores with `_bm25_scores` (BM25 if `rank_bm25` importable, else token-overlap),
  **gates candidates on token overlap**, ranks, returns a list of dicts:
  `{id, score, horizon, text, source, timestamp}` where `id` comes from
  `self._event_id(event)` → `"mem:" + sha256(...)[:12]`.
- It is consumed in exactly **one** runtime place:
  `workers.py:355`, `role_memory_store.build_pickup_packet(role, query=task)`,
  where the store was built at `workers.py:344` as
  `RoleMemoryStore(self.project_root)`.
- `_event_id` (`memory.py:411`) hashes the **whole event dict** — keep it
  unchanged so `mem:` handles stay stable (FE panel + `find_event` depend on them).
- `BM25Okapi` is imported under `try/except ImportError` → `None`
  (`memory.py:7`). **Mirror this exact pattern for the embedder.**

## What to build

### 1. Pluggable `Embedder` seam (`memory.py`)

- Change `RoleMemoryStore.__init__(self, project_root)` →
  `__init__(self, project_root, embedder=None)`. Store `self.embedder = embedder`.
  **`embedder=None` MUST mean "no embeddings, use the existing BM25 path."** Do
  NOT auto-detect fastembed inside the store. (This keeps tests host-independent —
  see PRD gotcha #1; it's the same masking class that turned CI red last round.)
- `embedder` is a callable `(list[str]) -> list[list[float]]` returning one vector
  per input text, same order.

- Add a module-level `default_embedder()`:
  ```python
  def default_embedder():
      """Return a fastembed-backed callable, or None if the optional wheel is
      absent (callers then fall back to BM25). Local/offline; never network-sends
      memory text to a third party."""
      try:
          from fastembed import TextEmbedding
      except ImportError:
          return None
      model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
      def embed(texts):
          return [list(map(float, v)) for v in model.embed(list(texts))]
      embed.model_name = "bge-small-en-v1.5"   # used for cache invalidation
      embed.dim = 384
      return embed
  ```
  Verify fastembed's real API before relying on it (`TextEmbedding(...).embed(iterable)`
  yields numpy arrays). If a flag/name differs, fix it and note it in your report —
  do not guess silently (§0.2).

### 2. Embedding projection cache (`memory.py`)

- `embeddings_path(self, role_key) -> Path` → `self.role_root(role_key) /
  "embeddings.jsonl"`.
- `_embed_events(self, role_key, events) -> list[list[float]] | None`:
  - If `self.embedder is None`: return `None`.
  - Determine current model name: `getattr(self.embedder, "model_name", "embedder")`.
  - Read `embeddings.jsonl` into `{id: vector}` **only for rows whose `model`
    matches the current model** (ignore stale-model rows).
  - For events whose `_event_id` is missing from the cache, call
    `self.embedder([texts...])` once (batch), append new rows
    `{"id", "model", "dim", "vector"}` to `embeddings.jsonl`.
  - Return vectors aligned to the `events` list order.
  - Cache is a pure projection: deleting the file must fully regenerate it.

### 3. Cosine path inside `retrieve_relevant_events` (`memory.py`)

Keep the **entire existing function contract**: same signature, the
`if not query_tokens: return []` guard, the `horizons` filter, the empty-events
guard, the return dict shape, top_k slice.

Branch internally:
```python
vectors = self._embed_events(role_key, events)   # None when no embedder
if vectors is not None:
    qvec = self.embedder([query])[0]
    scored = [(self._cosine(qvec, v), event) for v, event in zip(vectors, events)]
    candidates = [(s, e) for s, e in scored if s >= COSINE_FLOOR]   # relevance gate
    ranked = sorted(candidates, key=lambda p: p[0], reverse=True)
else:
    # existing BM25 + token-overlap-gate path, unchanged
    ...
```
- Add `_cosine(a, b)` (pure Python: dot / (norm*norm); return 0.0 if either norm
  is 0). No numpy dependency required in the hot path beyond what fastembed returns.
- `COSINE_FLOOR`: a small positive constant (e.g. `0.0`–`0.1`) chosen so the fake
  embedder QA is deterministic and unrelated events are excluded. Pin the value
  and document it inline. Build result dicts with the **same keys/derivation** as
  the BM25 branch (factor the dict-building into a tiny helper if it reduces
  divergence risk).
- `score` in the result is the cosine similarity on this path (float). That's fine
  — `score` was never a fixed scale.

### 4. Wire production (`workers.py`)

- At `workers.py:344`, change
  `RoleMemoryStore(self.project_root)` →
  `RoleMemoryStore(self.project_root, embedder=default_embedder())`
  (import `default_embedder` from `.memory`). This is the only runtime wiring.
  With fastembed absent (CI, default install) `default_embedder()` returns `None`
  → BM25, so **nothing changes in CI**.

### 5. Optional dependency (`pyproject.toml`)

```toml
[project.optional-dependencies]
dev = [ ... unchanged ... ]
embeddings = ["fastembed>=0.3"]
```
Do not add fastembed to `dev` or to the base `dependencies`.

### 6. Tests (`tests/test_memory.py`) — TDD, deterministic, no downloads

- **Fake embedder** helper (deterministic, no fastembed): map each text to a fixed
  vector, e.g. a small hashed bag-of-words over a fixed vocabulary, so that
  semantically-grouped test strings are nearer in cosine than unrelated ones.
  Give it `.model_name = "fake"`, `.dim = <n>`.
- New tests:
  - **Semantic retrieval:** with the fake embedder, a query that is
    lexically disjoint but semantically grouped with one event ranks that event
    top-K, where the BM25 path (same data, `embedder=None`) would not. This is the
    proof of the feature.
  - **Cache is a projection:** after a retrieval, `embeddings.jsonl` exists; delete
    it, retrieve again, results identical (regeneration).
  - **Model-change invalidation:** a cached row with a different `model` is not
    reused (e.g. retrieve with fake-embedder-A, then fake-embedder-B → B's vectors
    drive the result, A's rows ignored).
  - **Stable `mem:` handle** preserved on the embedding path (same assertion style
    as `test_retrieved_event_carries_stable_mem_handle`).
  - **Default-off:** `RoleMemoryStore(root)` (no embedder) returns byte-identical
    results to today on the existing fixtures.
- **Existing BM25 tests must stay green and on the BM25 path.** They construct
  `RoleMemoryStore(project_root)` with no embedder → already BM25. Do not change
  their expectations.

## Verify (must pass before you report)

```bash
PYTHONPATH=src python -m pytest tests -q            # all green on [dev] alone
ruff check src/goodteam_os/ tests/                  # clean on touched files
```
Optional local-only semantic sanity (do NOT make it a test, downloads a model):
`pip install fastembed` then a one-off script constructing
`RoleMemoryStore(root, embedder=default_embedder())` and showing a lexically
disjoint query retrieves the right event. Report the observation; don't commit it.

## Report back (your only "finishing" action)

State: which files you changed, the final `COSINE_FLOOR` value + why, the fake
embedder design, the exact fastembed API you verified (and any deviation from the
snippet above), and the two `pytest` / `ruff` results. Do not write summary docs,
do not touch the progress log, do not commit.
