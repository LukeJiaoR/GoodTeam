# FE track — clickable mem: handle → event drill-down

Implement the frontend half of the Memory event drill-down. The full shared
contract is in your ## Constraints section (the source of truth). This file is
the FE-specific work order.

## Lane
Only touch `frontend/src/App.jsx`. Do NOT touch `src/` or `tests/` (that is the
parallel BE track). Do NOT create or modify any other file (no docs, no progress
log, no summary/walkthrough). Do NOT commit/branch/push.

## Work
In the Memory panel (already renders `role.latest_retrieval.relevant_events[]`
with `id`/`text`/`source`/`horizon`/`score`):
1. Make each `mem:<id>` handle a clickable control (button/link styled to match
   the existing Duolingo aesthetic).
2. On click, lazily `GET /api/memory/event?project=<KEY>&id=<that mem id>` and
   show the returned full event in a modal or inline expander: full `text`,
   `role`, `horizon`, `timestamp`, `source`.
3. Graceful states: loading while fetching; on non-200 (e.g. 404/offline) show a
   small "couldn't load this event" message — never white-screen; allow closing
   the modal/expander.
4. Reuse existing state/fetch patterns already in App.jsx (it already lazy-fetches
   `/api/memory`). If you add a state-in-effect, match the existing
   `// eslint-disable-next-line react-hooks/set-state-in-effect` convention.

## Verify (the dispatch will also run this)
`npm --prefix frontend ci && npm --prefix frontend run build && npm --prefix frontend run lint` must pass.

Report exactly which parts of App.jsx you changed and how each state is handled.
