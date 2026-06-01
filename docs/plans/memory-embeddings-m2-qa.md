# QA plan — M2 embeddings

QA is **independent of Claude** and runs in **CI** (workflow §0.3). Everything
scriptable here is a `pytest` test in `tests/test_memory.py` that the existing
`.github/workflows/ci.yml` backend job already executes on every push/PR — no new
CI job needed. The one thing CI can't own (a real model download) is a manual,
non-gating local check.

Contract under test: `memory-embeddings-m2-prd.md` §3 acceptance + §6 gotchas.

---

## A. CI-gated (deterministic, no network, no model download)

These run under `PYTHONPATH=src python -m pytest tests -q` with only `[dev]`
installed — i.e. **fastembed is absent**, exactly like CI. All use the injected
**fake embedder**; none import fastembed.

| # | Check | Pass criteria |
|---|---|---|
| A1 | **No-embedder regression** | `RoleMemoryStore(root)` (default) returns results byte-identical to pre-change on the existing fixtures. The whole pre-existing `test_memory.py` retrieval suite stays green, unchanged. |
| A2 | **Semantic win** | With the fake embedder, a query lexically disjoint from but semantically grouped with one event ranks it in top-K; the same data via `embedder=None` (BM25) does not surface it (or ranks it lower). Proves the feature. |
| A3 | **Return shape unchanged** | Result dicts have exactly `{id, score, horizon, text, source, timestamp}`; `id` starts `mem:` and equals `_event_id`. |
| A4 | **Stable handle on embedding path** | Same query twice → same `mem:<id>`. |
| A5 | **Cache is a pure projection** | After retrieval, `roles/<role>/embeddings.jsonl` exists; delete it and retrieve again → identical results (regenerates). |
| A6 | **Model-change invalidation** | Cached rows whose `model` != current embedder's model are ignored/re-embedded, not reused. |
| A7 | **Guards preserved** | Empty query (`""`/whitespace) → `[]`; empty corpus → `[]`; `horizons=(...)` filter still applied — on the embedding path too. |
| A8 | **Optional-dep boundary** | `pytest` passes with only `[dev]` (no fastembed). This is implicit in CI but call it out: if the fastembed import isn't `try/except`-guarded, CI goes red. |
| A9 | **Lint** | `ruff check src/goodteam_os/ tests/` clean on touched files. |

If any A-check fails, CI is red → not landable. That is the gate.

## B. Manual local acceptance (NOT gating — real model, downloads weights)

Run once on a dev machine to confirm the real backend works end-to-end. Never in
CI (it would download ~hundreds of MB).

```bash
pip install fastembed                  # pulls onnxruntime + the bge-small ONNX model on first embed
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from goodteam_os.memory import RoleMemoryStore, default_embedder
import tempfile

root = Path(tempfile.mkdtemp())
s = RoleMemoryStore(root, embedder=default_embedder())
s.ensure_role_memory("architect", "Architect", "Demo")
s.append_memory_event("architect", horizon="mid-term",
    text="better-sqlite3 native addon ABI drift breaks tests when Node version changes.",
    source="job-1/result.md")
s.append_memory_event("architect", horizon="mid-term",
    text="Telegram approval channel masks sensitive data before sending messages.",
    source="job-2/result.md")

# Lexically DISJOINT from the sqlite event, semantically the same failure:
res = s.retrieve_relevant_events("architect",
    query="the database binary stopped loading after I upgraded the runtime", top_k=2)
print([r["text"][:40] for r in res])
assert res and "better-sqlite3" in res[0]["text"], "semantic retrieval failed"
print("OK: semantic retrieval surfaced the sqlite event with no shared tokens")
PY
```

**Pass:** the sqlite event is retrieved first despite sharing essentially no tokens
with the query (BM25 would miss it). **Evidence:** paste the printed output into
the dispatch report. Confirm `embeddings.jsonl` was written under the temp role dir
and that a second run reuses it (no re-embed).

## C. Reviewer (Claude) final diff review — before human merge

Not QA, but the gate before landing. Check the diff against the 8 PRD gotchas:
WAL untouched (no embedder call in `append_memory_event`); `embedder=None` never
auto-detects; cache fully rebuildable; model-change invalidation present; return
shape byte-identical; guards preserved; no new *required* dep; tests inject a fake
embedder and download nothing. Confirm the only `workers.py` change is the one
constructor line.

## What is explicitly NOT tested here

Cross-project store, user-prefs, failure library, conflict/staleness, citation
*rendering* — all out of scope this increment (PRD §2). No live-LLM-worker run is
needed (retrieval is deterministic given an embedder; the worker just consumes the
packet).
