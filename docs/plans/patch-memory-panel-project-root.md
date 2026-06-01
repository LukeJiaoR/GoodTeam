# Patch handoff — Memory panel empty (project_root resolution) (Codex)

Cold-start, self-contained. Implementer: **Codex (`gpt-5.3-codex`)**. Lane =
backend only (`web.py` + `tests/test_api.py`).

## Symptom (live-verified)

`GET /api/memory?project=DEMO` returns `200` with `{"roles": []}` even though role
memories exist on disk under the target repo's `.goodteam/memory/roles/`
(architect/implementation/qa/ui). The Memory panel shows empty for **any** project
whose run has not crossed the takeover/adoption gate — not just DEMO.

## Root cause (verified against current code — treat as ground truth)

The four read sites in `web.py` resolve the memory root from `context_packet_path`:

```python
project_root = Path(state.context_packet_path).parents[2] if state is not None and state.context_packet_path else None
```
at **`web.py:3522`** (dashboard renderer), **`web.py:4907`** (`/api/state`),
**`web.py:4933`** (`/api/memory`), **`web.py:4945`** (`/api/memory/event`).

- `build_memory_overview(project_root, key)` (`web.py:1428`) returns
  `{"project_key": key, "roles": []}` when `project_root is None`.
- `state.context_packet_path` is set in **exactly one place**: the adoption path,
  `graph.py:1261` (`state.context_packet_path = str(memory_path)`), reached via
  `run_adoption_until_next_gate`. A project that hasn't gone through takeover
  (fresh, seeded, init flow, or pre-takeover) has `context_packet_path == ""`
  → `project_root = None` → empty panel, **even though memory is on disk**.
- There is **no** `project_config.parent.parent` fallback in the current code
  (if you read that elsewhere, it's stale — the `else` branch is `None`).

### Where memory actually lives (the write-side root to mirror)

Both project memory and **role** memories are written under
`resolve_project_runtime_root(repos)` (`graph.py:87`):
- `ensure_project_role_memories` → `RoleMemoryStore(resolve_project_runtime_root(repos))` (`graph.py:128-135`).
- `ensure_project_memory` → `select_project_memory_root` → `resolve_project_runtime_root` first (`graph.py:101-108`, `76-84`).

`resolve_project_runtime_root` (read it — do not assume):
- collects `repos.values()` that are existing dirs;
- if **>1 repo and they share a single common parent** → returns that **parent**;
- otherwise returns the **first** repo dir;
- `None` if no repo dirs exist.

So the correct memory root is `resolve_project_runtime_root(target["repos"])`, **not**
`repos["app"]` and **not** `context_packet_path`-derived. (When `context_packet_path`
*is* set, `.parents[2]` happens to equal this same root — which is why the fix is
"resolve it directly and independently of the gate.")

## The fix (lane = `web.py` only for the change)

Add one helper and route all four sites through it.

```python
def _resolve_memory_root(workspace: Path, project_key: str, state) -> Path | None:
    """Resolve the project's memory root the same way the write side does:
    from the project config's repos via resolve_project_runtime_root. Falls back
    to the context_packet_path-derived root only if the config is unavailable.
    Independent of whether the run has crossed the takeover gate."""
    config = _project_config_for_key(workspace, project_key) if project_key else None
    if config is not None and config.is_file():
        target = load_project_target(config)
        root = resolve_project_runtime_root(dict(target.get("repos", {})))
        if root is not None:
            return root
    if state is not None and state.context_packet_path:
        return Path(state.context_packet_path).parents[2]
    return None
```

- Import `resolve_project_runtime_root` and `load_project_target` from `.graph`
  (confirm the exact import path — `load_project_target` is already used in
  `web.py`; `resolve_project_runtime_root` lives in `graph.py:87`). Reuse
  `_project_config_for_key(workspace, key)` (already in `web.py`, see ~`web.py:619`/`832`).
- Replace the inline `project_root = ...parents[2]...` at **4907**, **4933**,
  **4945**, and **3522** with `project_root = _resolve_memory_root(workspace, project_key, state)`.
  - Confirm each call site has `workspace`, the project key, and `state` in scope
    (the `/api/*` handlers do; the dashboard renderer at 3522 — check it has the
    project key; if not, pass what it has and keep its current behavior as the
    fallback). **Do not change any call site's surrounding behavior** beyond the
    root resolution.
- **Do not** touch `graph.py`, `memory.py`, `frontend/`, or any other file.

### Gotchas
- Keep `build_memory_overview` and `/api/memory/event` logic unchanged — only the
  `project_root` *value* feeding them changes.
- `resolve_project_runtime_root` returning a **common parent** for multi-repo
  projects is correct and intended — do not "simplify" it to a single repo.
- The fallback chain order matters: config-resolved root **first**, then the
  `context_packet_path` derivation, then `None`. Never regress a currently-working
  (post-takeover) project.

## Tests (`tests/test_api.py`) — this is the part CI was missing (§0.3)

The existing memory tests build paths directly and never exercise real
`project_root` resolution, so CI stayed green while the panel was broken. Add a
test that goes through the resolver with an **empty `context_packet_path`**:

- Build an isolated workspace with a project config whose `repos` point at a temp
  target dir; seed role memories under
  `<runtime_root>/.goodteam/memory/roles/<role>/...` (use `RoleMemoryStore` +
  `append_memory_event`, mirroring existing seeding helpers in the test suite).
- Drive `/api/memory?project=<KEY>` (same harness the existing `test_api.py`
  memory tests use) with a state whose `context_packet_path` is `""`.
- **Assert `roles` is non-empty** and contains the seeded role(s). Before the fix
  this returns `{"roles": []}`; after, it returns the seeded roles.
- Add a multi-repo variant if cheap: two repo dirs sharing a parent → memory
  seeded under the **parent** → still resolved (guards the common-parent branch).
- Keep all existing `test_api.py` tests green (post-takeover projects with a set
  `context_packet_path` must still resolve identically).

## Verify (must pass before you report)

```bash
PYTHONPATH=src python -m pytest tests -q       # all green, incl. the new test
ruff check src/goodteam_os/ tests/             # clean on touched files
```

## Report back (your only "finishing" action)

State: the helper as landed, which 4 sites you routed through it (and any call site
whose scope forced a deviation), the new test(s) and that they were red before /
green after, and the `pytest` + `ruff` results. Do NOT commit/branch/push, do NOT
write summary docs, do NOT touch any progress log. Land is the human's.
