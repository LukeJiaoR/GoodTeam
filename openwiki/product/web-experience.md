---
type: Product Surface
title: Web experience and API boundary
description: The React product experience and Python HTTP boundary that display GoodTeam project state, readiness, and workflow controls without moving authority into the browser.
tags: [product, frontend, react, api, routes]
---

# Web experience and API boundary

GoodTeam’s UI is a separate React application backed by the Python server. The frontend calls `/api/*`; the backend retains authority for workflow transitions, readiness capability, filesystems, approval decisions, and state persistence. This means the UI **surfaces** [the control plane](../architecture/control-plane.md) and **presents** [project readiness](../workflows/project-readiness.md), rather than independently deciding either.

## Runtime and build model

- Backend: `goodteam_os.cli serve` starts the Python HTTP server, normally on port 8788 for direct development use.
- Frontend development: Vite runs at `127.0.0.1:8790` and proxies `/api` to `127.0.0.1:8788`; configuration is in `frontend/vite.config.js`.
- Production-style local serving: build `frontend/dist` with `npm --prefix frontend run build`; the backend resolves and serves the built SPA. `GOODTEAM_FRONTEND_DIST` can select an external build. Missing/invalid builds produce an actionable diagnostic rather than silently rendering the retired legacy UI.

The frontend is React 19 with React Router, Vite, Tailwind, and test tooling described in `frontend/package.json`. Server asset resolution is in `src/goodteam_os/frontend_assets.py`; HTTP/SPA handling is in `src/goodteam_os/web.py`.

## Routes and product surfaces

| Route | Purpose |
| --- | --- |
| `/` | Existing multi-project/legacy dashboard. |
| `/?project=<PROJECT_KEY>` | Existing project-focused workflow dashboard. |
| `/projects` | Product project list and directory-based import. |
| `/projects/:projectKey/home` | Product Project Home with facts, preparation, actions, and workflow eligibility. |

`frontend/src/app/AppRouter.jsx` defines the product router. The server permits SPA fallback for the supported product paths so direct navigation resolves to the React application.

The product routes are additive: the project readiness endpoint can bridge a ready project back into the legacy `/?project=…` workflow view while a dedicated run-workspace product surface is still evolving.

## Project loading and readiness presentation

`ProjectsPage` retrieves projects and groups them by attention, active work, preparation, readiness, and legacy state. `LoadProjectPanel` submits an absolute directory; the server responds with the canonical project/home identity.

`ProjectHomePage` fetches its state from the server and polls only while readiness is running. `ReadinessCenter` displays step status, attempts, timestamps, artifacts, errors/recovery, and retry paths; `ReadinessInputPanel` submits structured confirmations or command choices. The page disables workflow start from the server’s `capabilities`/reason field.

This behavior matters because [project readiness](../workflows/project-readiness.md) is an explicit safety gate: client-side display must not become a competing state machine.

## API ownership and error contract

`frontend/src/features/projects/projectApi.js` maps requests to the project API and URI-encodes project/step IDs. `web.py` implements the handlers and returns the normalized project error envelope:

```json
{
  "code": "…",
  "message": "…",
  "details": {},
  "recovery": "…",
  "request_id": "…"
}
```

The backend also serves broader dashboard, review, worker, and execution surfaces. When adding a UI action, locate the authoritative backend transition first; do not infer permission from rendered state alone.

## Change guide and checks

Start in:

- Routing/layout: `frontend/src/app/{AppRouter.jsx,ProductLayout.jsx}`.
- Project UI: `frontend/src/features/projects/`.
- API client: `frontend/src/features/projects/projectApi.js`.
- API/asset server: `src/goodteam_os/{web.py,frontend_assets.py}`.
- Localization: `frontend/src/i18n/locales/{en.js,zh-CN.js}`.

Validate a UI/API change with the appropriate mix of:

```sh
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
```

Use backend API/web tests as well when changing a contract. The [operations and quality guide](../operations/operator-and-quality.md) places browser-visible, persisted, or adapter-connected work in Tier 4, which adds isolated live/Playwright evidence to subsystem testing.
