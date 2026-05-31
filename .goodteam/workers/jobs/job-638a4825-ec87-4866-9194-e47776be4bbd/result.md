Implemented backend drill-down for memory events.

Changed files:
- [src/goodteam_os/memory.py](/private/tmp/goodteam-dev/src/goodteam_os/memory.py)
- [src/goodteam_os/web.py](/private/tmp/goodteam-dev/src/goodteam_os/web.py)
- [tests/test_api.py](/private/tmp/goodteam-dev/tests/test_api.py)

Added:
- `RoleMemoryStore.find_event(self, event_id: str) -> dict | None`
- `build_memory_event_detail(project_root, event_id)`
- `GET /api/memory/event`
- Tests for:
  - full event retrieval by derived `mem:` id
  - 404 on unknown id
  - 400 on missing id

Verification:
- `env PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py -q`
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/`

Unresolved risks:
- None from the implemented scope. The route still resolves `project_root` the same way as `/api/memory`, so it depends on initialized state/context packet paths.

## Learned
- Event ids are derived only by recomputing `_event_id(event)` from each stored event payload.
- `ruff` was available in the project venv even though it was not on the shell `PATH`.

## Caveats
- The drill-down endpoint returns 404 when the event exists nowhere under `.goodteam/memory/roles/`.
- The lookup scans role event logs linearly, matching the current storage model.

## Decisions
- Kept the new route’s project-root resolution identical to `/api/memory`.
- Returned only the event fields in the shared contract, with `role` sourced from the matched role directory.

## Open Questions
- None.