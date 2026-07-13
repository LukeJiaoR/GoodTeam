# Reliable Web Startup and Native Path Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple React application assets from the GoodTeam data workspace and restore reliable macOS file/directory selection with the existing Web directory picker as fallback.

**Architecture:** A dedicated Python resolver owns frontend asset discovery and diagnostics; the background service resolves once, records the result, and passes it to the Web server. A dedicated native picker adapter exposes only fixed file/directory operations on local loopback macOS servers. A small frontend chooser utility owns native-first/fallback behavior so Project Root and Context Sources share the same contract.

**Tech Stack:** Python 3.11+ stdlib HTTP server and subprocess APIs, pytest, React 19, browser `fetch`, Node built-in test runner, Vite.

## Global Constraints

- Keep `goodteam restart`, `goodteam stop`, `goodteam status`, port `21001`, host/port/workspace flags, ready-token handshake, PID identity checks, logs, and child cleanup.
- Never resolve React assets from the data workspace or current working directory.
- `GOODTEAM_FRONTEND_DIST` must be expanded, resolved, and validated as a directory containing `index.html`.
- Missing React assets must never silently render the legacy product UI.
- Native selection is limited to `file` and `directory`, uses fixed AppleScript plus argv without `shell=True`, times out, and is disabled outside local loopback macOS GUI use.
- Keep `/api/fs/list`, `/api/fs/mkdir`, `DirectoryPickerModal`, `useDirectoryPicker`, manual path entry, and all existing New/Adopt, Context, Worker, Git, Delivery, integration, staging, i18n, responsive, and theme behavior.
- Treat this as Tier 4: run focused and full backend validation, frontend test/lint/build, and an isolated live service smoke.

---

### Task 1: Frontend asset resolution and service projection

**Files:**
- Create: `src/goodteam_os/frontend_assets.py`
- Create: `tests/test_frontend_assets.py`
- Modify: `src/goodteam_os/service.py`
- Modify: `src/goodteam_os/cli.py`
- Modify: `src/goodteam_os/web.py`
- Modify: `tests/test_service_cli.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `resolve_frontend_dist(*, environ=None, module_path=None, prefix=None) -> Path` and `FrontendAssetsNotFound` with attempted paths and actionable diagnostics.
- Produces: `serve(..., frontend_dist: Path | None = None)`; explicit paths are validated, implicit failures render a diagnostic page instead of legacy HTML.
- Produces: service state key `frontend_dist` and CLI status fields `Running`, `URL`, `PID`, `Workspace`, `Frontend dist`, `Log path`, `Started at`.

- [ ] **Step 1: Write failing resolver and service tests**

Cover valid/invalid overrides, checkout and install candidates, cwd independence, workspace independence, diagnostic content, state projection, CLI output, and a React-index request that cannot fall back to legacy HTML.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_frontend_assets.py tests/test_service_cli.py tests/test_api.py -q -k 'frontend or restart or status or serve'
```

Expected: import/API/assertion failures because the resolver and state field do not exist and the old handler returns legacy HTML.

- [ ] **Step 3: Implement the minimal resolver and wire it once**

Candidate order is explicit override, package-local `frontend_dist`, install-prefix `share/goodteam/frontend/dist`, then source-checkout `frontend/dist`. Background restart resolves before replacing the current service and passes the absolute path to the child. Direct `serve` records a resolver error and returns an actionable HTTP 503 diagnostic on product-page requests.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command and require all selected tests to pass.

### Task 2: Controlled native picker adapter and HTTP route

**Files:**
- Create: `src/goodteam_os/native_picker.py`
- Create: `tests/test_native_picker.py`
- Modify: `src/goodteam_os/web.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `choose_native_path(mode, initial_path, *, host, platform=None, environ=None, runner=None) -> dict[str, str]`.
- Produces: `POST /api/fs/choose` returning `selected`, `cancelled`, `unsupported`, or `error` payloads.

- [ ] **Step 1: Write failing adapter and API tests**

Cover directory/file success, cancellation, unsupported platform, headless and non-loopback rejection, invalid mode, normalized initial path, Unicode/space paths, timeout/failure, argv-only injection resistance, route same-origin rejection, and stable payloads.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_native_picker.py tests/test_api.py -q -k 'native_picker or fs_choose'
```

Expected: missing module/route failures.

- [ ] **Step 3: Implement fixed-script macOS selection and route wiring**

Use `/usr/bin/osascript` with a constant script and positional `mode`/`initial_path` arguments, `capture_output=True`, `text=True`, `check=False`, and a bounded timeout. Never accept an executable or script from the request. Validate selected path type and keep native selection disabled when the bind host is not loopback.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command and require all selected tests to pass.

### Task 3: Native-first React chooser with Web fallback

**Files:**
- Create: `frontend/src/utils/localPathPicker.js`
- Create: `frontend/src/utils/localPathPicker.test.js`
- Modify: `frontend/src/hooks/useDirectoryPicker.js`
- Modify: `frontend/src/components/NewProjectModal.jsx`
- Modify: `frontend/src/components/NewProjectModalContainer.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/package.json`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces: `chooseLocalPath({ mode, initialPath, onSelected, openWebPicker, onError, fetchImpl }) -> Promise<object>`.
- Extends `useDirectoryPicker` with `chooseLocalPath` while preserving `openDirectoryPicker` and `directoryPickerModal`.

- [ ] **Step 1: Write failing Node and Python frontend-contract tests**

Cover selected/cancelled/unsupported/error behavior, exact file/directory modes, only the target callback being updated, removal of `File.path`/`webkitdirectory`, URL Browse absence, and preservation of preview/generate controls.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
npm --prefix frontend test
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'directory_picker or context_sources_can_browse'
```

Expected: missing test script/helper and old `File.path` contract failures.

- [ ] **Step 3: Implement shared chooser and migrate both call sites**

The helper posts to `/api/fs/choose`. `selected` invokes the supplied callback, `cancelled` is a no-op, and `unsupported`, HTTP failure, or `error` opens the existing Web picker without clearing form state. Errors also use the existing lightweight toast path. Remove the hidden file input and `File.path` inference.

- [ ] **Step 4: Run frontend tests, lint, and build**

```bash
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: all tests pass, lint is clean, and Vite emits `frontend/dist/index.html` plus hashed assets.

### Task 4: Documentation, full verification, live smoke, and PR

**Files:**
- Modify: `README.md`
- Modify if required by the resolved architecture: `scripts/dev-up.sh`, `scripts/demo-up.sh`, `scripts/release-up.sh`

**Interfaces:**
- Documents: data workspace versus application asset boundary, `GOODTEAM_FRONTEND_DIST`, diagnostic recovery, and native/Web fallback behavior.

- [ ] **Step 1: Remove stale workspace-asset guidance and add actionable startup guidance**

Keep service management documentation and replace the legacy silent-fallback description with build/override instructions.

- [ ] **Step 2: Run Tier 4 verification**

```bash
git diff --check
.venv/bin/ruff check src/goodteam_os/ tests/
PYTHONPATH=src .venv/bin/python -m pytest tests/test_service_cli.py tests/test_frontend_assets.py tests/test_native_picker.py tests/test_web.py tests/test_api.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests -q
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

- [ ] **Step 3: Run isolated live service smoke from an unrelated cwd**

Use fresh temporary state and data directories, point the installed/source application at the built `frontend/dist`, run `restart`, `status`, request `/`, verify React index and absence of legacy title, then `stop` and confirm state cleanup. Do not use an active project workspace.

- [ ] **Step 4: Review, commit, push, and create the PR**

Create clear commits on `codex/fix-web-startup-native-picker`, push to origin, and open a non-draft PR to `main` titled `fix(web): restore native path picking and reliable global startup`. The body must record Tier 4 commands/results and honestly state whether a real Finder GUI smoke was executed.
