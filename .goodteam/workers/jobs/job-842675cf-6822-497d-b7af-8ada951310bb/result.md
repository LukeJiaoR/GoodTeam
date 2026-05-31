Implemented in [frontend/src/App.jsx](/private/tmp/goodteam-dev/frontend/src/App.jsx) only.

Changed parts:
- Added `memoryEventDetail` state to hold the selected event, loading flag, error string, and fetched payload.
- Added `handleOpenMemoryEvent(eventId)` to lazily call `GET /api/memory/event?project=...&id=...`.
- Replaced each plain `mem:<id>` label in the Memory panel with a clickable button styled to match the Duolingo-like UI.
- Added a closeable modal overlay that shows:
  - loading spinner while fetching
  - `couldn't load this event` on non-200 / offline
  - full `text`, `role`, `horizon`, `timestamp`, and `source` on success

State handling:
- Loading: `memoryEventDetail.loading === true` renders the fetch spinner in the modal.
- Error: failed fetch sets `memoryEventDetail.error` and shows the small inline failure message.
- Success: `memoryEventDetail.data` renders the full event details.
- Close: the X button clears `memoryEventDetail` to `null`.
- Stale response guard: completion handlers only commit results if the in-flight `eventId` still matches the current detail state.

Verification:
- `npm --prefix frontend ci`
- `npm --prefix frontend run build`
- `npm --prefix frontend run lint`

All passed.

Unresolved risks:
- Modal uses click-to-close only; no Escape-key handling or focus trap yet.
- If the backend returns an unexpected payload shape, the modal falls back to `not recorded`/empty defaults rather than failing. 

## Learned
- The Memory panel already had the right lazy-fetch pattern, so the drill-down could stay local to `App.jsx`.

## Caveats
- I did not run a browser smoke test; verification was limited to install/build/lint.

## Decisions
- Used a modal instead of an inline expander so the full event is easier to scan without disrupting the Memory card layout.

## Open Questions
- None.