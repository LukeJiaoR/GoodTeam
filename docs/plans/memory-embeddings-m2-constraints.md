# Dispatch constraints — M2 embeddings (prepended to the worker prompt)

These are hard, non-negotiable rules. The full task is the backend handoff
(`memory-embeddings-m2-backend.md`, inlined as your task). If anything below
conflicts with the task, these win.

## Lane (whitelist — touch ONLY these four files)
- `src/goodteam_os/memory.py`
- `src/goodteam_os/workers.py`
- `pyproject.toml`
- `tests/test_memory.py`

Do NOT create or modify any other file. Explicitly off-limits: `frontend/`,
`src/goodteam_os/web.py`, any `docs/**` (including `docs/current-progress.md` and
every `docs/plans/*.md`), `README.md`, `.github/`.

## Git
- Do NOT commit, branch, or push. The human lands after CI.
- If you stage anything, `git add` ONLY the four whitelisted files — NEVER
  `git add -A`. The worktree accumulates gitignored `.goodteam/` runtime state;
  keep it out of staging entirely.

## Behavioral invariants (the feature is opt-in and CI-safe)
- `RoleMemoryStore(project_root)` with NO embedder MUST keep the exact current
  BM25 behavior, byte-for-byte. `embedder=None` ⇒ BM25. Never auto-detect
  fastembed inside the store.
- Do NOT touch the WAL write path: `append_memory_event` must not call the
  embedder. Embeddings are a lazy, rebuildable projection off `events.jsonl`.
- `_event_id` stays unchanged (stable `mem:` handles).
- The whole test suite MUST pass on `[dev]` alone (no fastembed installed). Tests
  use a deterministic fake embedder and download nothing. Do NOT `pip install
  fastembed` to make tests green.
- fastembed is an OPTIONAL extra (`[embeddings]`), never added to `dev` or base
  `dependencies`.

## Finishing (your only "finish" action)
Report in chat: files changed, final `COSINE_FLOOR` value + rationale, fake
embedder design, the exact fastembed API you verified (note any deviation from the
handoff snippet — do not guess silently), and the `pytest` + `ruff` results. Do
NOT write summary/walkthrough docs, do NOT update any progress log, do NOT run any
"finishing"/"verification" auto-skill.
