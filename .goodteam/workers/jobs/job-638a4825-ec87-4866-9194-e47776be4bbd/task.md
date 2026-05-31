# BE track — `/api/memory/event` drill-down endpoint

Implement the backend half of the Memory event drill-down. The full shared
contract + verified backend facts are in your ## Constraints section (the source
of truth). This file is the BE-specific work order.

## Lane
Only touch `src/goodteam_os/memory.py`, `src/goodteam_os/web.py`, and
`tests/test_api.py`. Do NOT touch `frontend/` (that is the parallel FE track).

## Work
1. `src/goodteam_os/memory.py` — add a public lookup on `RoleMemoryStore`:
   `find_event(self, event_id: str) -> dict | None` that enumerates role dirs
   under `.goodteam/memory/roles/`, reads each role's events, and returns the
   event whose recomputed `_event_id(event)` equals `event_id` (else None).
   Reuse the existing `_event_id` / `_read_events`; do not duplicate their logic.
2. `src/goodteam_os/web.py` — add a `build_memory_event_detail(project_root, event_id)`
   helper (next to `build_memory_overview`) and a `GET /api/memory/event` route,
   placed right after the existing `/api/memory` block. Resolve `project_root`
   exactly like the `/api/memory` handler. Return the 200 shape from the contract;
   404 `{"error": ...}` when not found; 400 `{"error": ...}` for missing params.
3. `tests/test_api.py` — add tests mirroring the existing `/api/memory` tests'
   style (use the same `capture_get_handler` + `append_memory_event` seeding):
   - seed an event, retrieve its `mem:` id (via the same derivation), then assert
     `GET /api/memory/event?project=...&id=<that id>` returns the full event;
   - unknown id → 404 `{"error": ...}`;
   - missing `id` → 400.

## Verify (the dispatch will also run this)
`env PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py -q`
and keep `ruff check src/goodteam_os/ tests/` clean.

Report exactly which functions/routes/tests you added.
