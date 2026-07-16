# GoodTeam Project Load and Readiness Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first releasable redesign slice: durable nested routes, the calm bilingual product shell, idempotent read-only project import, persisted shared readiness, a new Projects page, and a recoverable project Home.

**Architecture:** A new Python readiness domain owns project classification, lifecycle, step persistence, and capability projection. Minimal import writes only GoodTeam workspace state and a local project config; new React routes consume focused `/api/projects/**` view models while `/` continues to render the legacy application until the mode-specific readiness plans are complete.

**Tech Stack:** Python 3.11+, pytest, Ruff, React 19.2.6, Vite 8.0.12, Tailwind CSS 4.3.0, react-router-dom, Vitest, Testing Library, Playwright, Simplified Chinese and English catalogs.

## Global Constraints

- Minimal import must not write any file or directory beneath the selected target root.
- Minimal import must not initialize Git, create `docs/`, read full source bodies, or invoke a model.
- Reimporting the same canonical root returns the existing project and never creates a duplicate.
- New routes support `zh-CN`, `en`, and `auto`; commands, paths, model IDs, Git refs, and raw evidence remain untranslated.
- Use the approved calm execution visual language: warm neutral surfaces, one terracotta accent, 14–16 px default body text, sparse borders, and light/dark appearance modes.
- `/` and the current `?project=KEY` behavior remain available throughout this plan; the new routes are additive.
- New browser-visible strings must use semantic i18n keys; missing keys fail frontend tests.
- New API errors use stable `code`, `message`, `details`, `recovery`, and `request_id` fields.
- Server capability projection, not frontend duplication, decides whether a workflow may start.
- This is Tier 4 browser-visible project-root work and Tier 3 state mutation: run focused tests, the full backend suite, Ruff, frontend test/lint/build, and isolated Playwright smoke before completion.
- Run mutating smoke only against `/private/tmp/goodteam-project-load-e2e`; never target a real active project workspace.
- Do not delete untracked `output/`, `.codegraph/`, screenshots, or user files.

## Program Decomposition

This is implementation plan 1 of 4 for the approved product redesign.

1. **This plan:** route/design foundation, minimal import, shared readiness, Projects, and Home.
2. **Next plan:** new-project Init executors and existing-project repository-understanding executors.
3. **Third plan:** run-centered Work workspace.
4. **Fourth plan:** Review, Knowledge, settings migration, parity audit, and legacy retirement.

The new `/projects` routes stay additive after this plan. Changing `/` to the new portfolio is explicitly outside this plan and occurs only after plan 2 satisfies mode-specific readiness.

## Target File Structure

```text
src/goodteam_os/
  project_import.py             canonical-root import and duplicate resolution
  project_readiness.py          classification, readiness store, lifecycle, capabilities
  projects.py                   safe local config rendering used by import
  web.py                        focused project APIs and product-route SPA fallback

tests/
  test_project_import.py
  test_project_readiness.py
  test_web.py

frontend/src/
  app/
    AppRouter.jsx               legacy and new route tree
    ProductLayout.jsx           calm global product shell
    NotFoundPage.jsx
  components/ui/
    Button.jsx
    InlineNotice.jsx
    PageState.jsx
    StatusBadge.jsx
  features/projects/
    projectApi.js
    LoadProjectPanel.jsx
    ProjectsPage.jsx
    ProjectHomePage.jsx
    ReadinessCenter.jsx
    ProjectFacts.jsx
  lib/api/
    client.js                   JSON request and stable error normalization
  styles/
    product.css                 approved product tokens and responsive shell
  test/
    setup.js
  i18n/locales/en.js
  i18n/locales/zh-CN.js
  i18n/index.js
  main.jsx

frontend/e2e/
  global-setup.js
  project-load-home.spec.js
frontend/playwright.config.js
frontend/vite.config.js
frontend/package.json
frontend/package-lock.json
```

---

### Task 1: Add the persisted project-readiness domain

**Files:**
- Create: `src/goodteam_os/project_readiness.py`
- Create: `tests/test_project_readiness.py`

**Interfaces:**
- Produces: `classify_project_mode(root: Path) -> str` returning `new_project`, `existing_project`, or `unconfirmed`.
- Produces: `ProjectReadinessStore(workspace: Path)` with `initialize(project_key, root)`, `read(project_key)`, and `confirm_mode(project_key, mode)`.
- Produces: `build_capabilities(readiness: dict[str, object]) -> dict[str, object]`.
- Persists: `<workspace>/state/projects/<KEY>/readiness.json` with schema version 1.

- [ ] **Step 1: Write classifier tests**

```python
from pathlib import Path

from goodteam_os.project_readiness import classify_project_mode


def test_classify_empty_directory_as_new_project(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    assert classify_project_mode(root) == "new_project"


def test_classify_manifest_repository_as_existing_project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"name":"sample"}', encoding="utf-8")
    assert classify_project_mode(root) == "existing_project"


def test_classify_unstructured_nonempty_directory_as_unconfirmed(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")
    assert classify_project_mode(root) == "unconfirmed"
```

- [ ] **Step 2: Run classifier tests and verify red**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_readiness.py -q
```

Expected: collection fails because `goodteam_os.project_readiness` does not exist.

- [ ] **Step 3: Implement deterministic shallow classification**

```python
from __future__ import annotations

from pathlib import Path


MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
}
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}
IGNORED_NAMES = {".DS_Store", "Thumbs.db"}


def classify_project_mode(root: Path) -> str:
    entries = [entry for entry in root.iterdir() if entry.name not in IGNORED_NAMES]
    visible = [entry for entry in entries if entry.name != ".git"]
    if not visible:
        return "new_project"
    if (root / ".git").exists():
        return "existing_project"
    for entry in visible:
        if entry.name in MANIFEST_NAMES or entry.suffix.lower() in SOURCE_SUFFIXES:
            return "existing_project"
        if entry.is_dir() and entry.name in {"src", "app", "frontend", "backend", "packages"}:
            return "existing_project"
    return "unconfirmed"
```

- [ ] **Step 4: Write readiness persistence and capability tests**

```python
from goodteam_os.project_readiness import ProjectReadinessStore, build_capabilities


def test_readiness_initialization_is_recoverable_and_mode_confirmation_is_persisted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")

    store = ProjectReadinessStore(workspace)
    initial = store.initialize("DEMO", root)
    assert initial["lifecycle"] == "attention_required"
    assert initial["project_mode"] == "unconfirmed"
    assert [step["status"] for step in initial["steps"][:2]] == ["complete", "complete"]
    assert initial["steps"][2]["status"] == "needs_input"

    confirmed = store.confirm_mode("DEMO", "existing_project")
    assert confirmed["project_mode"] == "existing_project"
    assert store.read("DEMO") == confirmed
    assert build_capabilities(confirmed)["start_workflow"] is False
    assert build_capabilities(confirmed)["start_workflow_reason"] == "mode_specific_readiness_pending"
```

- [ ] **Step 5: Implement atomic readiness persistence**

Implement `ProjectReadinessStore` with this exact persisted shape:

```python
{
    "schema_version": 1,
    "project_key": "DEMO",
    "canonical_root": "/absolute/root",
    "project_mode": "unconfirmed",
    "lifecycle": "attention_required",
    "steps": [
        {"id": "project_record", "status": "complete", "required": True},
        {"id": "safe_preflight", "status": "complete", "required": True},
        {"id": "project_mode", "status": "needs_input", "required": True},
    ],
    "updated_at": "2026-07-16T00:00:00Z",
}
```

Use a temporary sibling file and `Path.replace()` so readers never observe a
partially written JSON document. `confirm_mode()` must accept only
`new_project` and `existing_project`, set the `project_mode` step to `complete`,
and append the mode-specific pending steps below without duplication:

```python
MODE_STEPS = {
    "new_project": ["new_project_init", "project_brief", "worker_preflight"],
    "existing_project": [
        "repository_understanding",
        "command_verification",
        "constraints_confirmation",
        "worker_preflight",
    ],
}
```

`initialize()` calls `classify_project_mode(root)`. For `new_project` and
`existing_project`, it writes the `project_mode` step as `complete` and appends
the matching mode steps immediately with lifecycle `preparing`. For
`unconfirmed`, it sets lifecycle `attention_required`, writes that step as
`needs_input`, and appends no mode steps until `confirm_mode()`. Confirming a
mode returns lifecycle to `preparing` while mode-specific steps remain pending.

`build_capabilities()` returns:

```python
{
    "inspect_project_facts": True,
    "edit_project_settings": True,
    "run_readiness_steps": True,
    "start_workflow": False,
    "start_workflow_reason": "project_mode_unconfirmed"
    if readiness["project_mode"] == "unconfirmed"
    else "mode_specific_readiness_pending",
}
```

- [ ] **Step 6: Run focused tests and Ruff**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_readiness.py -q
.venv/bin/ruff check src/goodteam_os/project_readiness.py tests/test_project_readiness.py
```

Expected: all readiness tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 7: Commit the readiness domain**

```bash
git add src/goodteam_os/project_readiness.py tests/test_project_readiness.py
git commit -m "feat(projects): add persisted readiness foundation"
```

---

### Task 2: Add safe idempotent project import

**Files:**
- Create: `src/goodteam_os/project_import.py`
- Modify: `src/goodteam_os/projects.py`
- Create: `tests/test_project_import.py`

**Interfaces:**
- Consumes: `ProjectReadinessStore.initialize(project_key: str, root: Path) -> dict`.
- Produces: `build_registered_project_config(key, name, root) -> str` in `projects.py`.
- Produces: `import_project(workspace: Path, root_value: str, display_name: str = "",
  requested_key: str = "", existing_configs: list[Path] | None = None) -> dict`.
- Returns: `{created, project, readiness, capabilities, home_url}`.

- [ ] **Step 1: Write no-target-write and duplicate-import tests**

```python
from pathlib import Path

from goodteam_os.project_import import import_project


def snapshot_target(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_import_writes_only_goodteam_workspace_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "existing"
    root.mkdir()
    (root / "README.md").write_text("# Existing\n", encoding="utf-8")
    before = snapshot_target(root)

    result = import_project(workspace, str(root), display_name="Existing")

    after = snapshot_target(root)
    assert before == after
    assert result["created"] is True
    assert result["project"]["project_key"] == "EXISTING"
    assert result["home_url"] == "/projects/EXISTING/home"
    assert (workspace / "projects" / "existing.local.yaml").is_file()
    assert (workspace / "state" / "projects" / "EXISTING" / "readiness.json").is_file()
    assert not (root / "docs").exists()
    assert not (root / ".git").exists()


def test_reimport_returns_existing_project_without_duplicate_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "sample"
    root.mkdir()

    first = import_project(workspace, str(root))
    second = import_project(workspace, str(root))

    assert first["project"]["project_key"] == second["project"]["project_key"]
    assert second["created"] is False
    assert len(list((workspace / "projects").glob("*.local.yaml"))) == 1
```

- [ ] **Step 2: Run import tests and verify red**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_import.py -q
```

Expected: collection fails because `goodteam_os.project_import` does not exist.

- [ ] **Step 3: Add a config renderer with no side effects**

Add to `src/goodteam_os/projects.py`:

```python
import json


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def build_registered_project_config(key: str, name: str, root: Path) -> str:
    return (
        f"name: {_yaml_scalar(name)}\n"
        f"key: {_yaml_scalar(key)}\n"
        "source_type: unconfirmed\n"
        "model_profile: balanced\n"
        "delivery_mode: init_project\n"
        "repos:\n"
        f"  root: {_yaml_scalar(root.resolve())}\n"
        "worker_backend:\n"
        "  mode: codex\n"
        "task_backend:\n"
        "  mode: local\n"
        "  sync: \n"
    )
```

Do not call `create_subdirectory()`, `_initialize_project_git()`, or
`create_local_project_config()` from the import service.

- [ ] **Step 4: Implement canonical-root duplicate detection and import**

`import_project()` must:

1. expand and resolve `root_value`;
2. reject a missing path with code `project_root_not_found`;
3. reject a non-directory with code `project_root_not_directory`;
4. call `root.iterdir()` once to prove readability and map `PermissionError` to
   code `project_root_not_readable`;
5. load every config in `existing_configs` or `workspace/projects/*.local.yaml`;
6. compare resolved repo roots, initialize missing readiness for a legacy match,
   and return the existing record on a match;
7. derive the display name from `root.name` when blank;
8. derive the key with `normalize_project_key(requested_key or display_name)`;
9. add `-2`, `-3`, and so on when the derived key belongs to another root;
10. write the config with exclusive creation; if another importer wins the
    filename race, reload configs and either return that canonical-root match or
    continue with the next suffix;
11. initialize readiness and return the exact result contract.

Define a typed service error:

```python
class ProjectImportError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}
```

- [ ] **Step 5: Add key-collision and unreadable-root tests**

```python
def test_import_suffixes_derived_key_for_a_different_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first = tmp_path / "one" / "sample"
    second = tmp_path / "two" / "sample"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert import_project(workspace, str(first))["project"]["project_key"] == "SAMPLE"
    assert import_project(workspace, str(second))["project"]["project_key"] == "SAMPLE-2"


def test_import_rejects_missing_root_with_stable_code(tmp_path: Path) -> None:
    from goodteam_os.project_import import ProjectImportError

    try:
        import_project(tmp_path / "workspace", str(tmp_path / "missing"))
    except ProjectImportError as exc:
        assert exc.code == "project_root_not_found"
    else:
        raise AssertionError("expected missing root to fail")


def test_import_rejects_unreadable_root_with_stable_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "blocked"
    root.mkdir()
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path.resolve() == root.resolve():
            raise PermissionError("blocked")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    try:
        import_project(tmp_path / "workspace", str(root))
    except ProjectImportError as exc:
        assert exc.code == "project_root_not_readable"
    else:
        raise AssertionError("expected unreadable root to fail")
```

- [ ] **Step 6: Run focused tests and Ruff**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_import.py tests/test_project_readiness.py -q
.venv/bin/ruff check src/goodteam_os/project_import.py src/goodteam_os/project_readiness.py src/goodteam_os/projects.py tests/test_project_import.py tests/test_project_readiness.py
```

Expected: all tests pass and Ruff is clean.

- [ ] **Step 7: Commit safe import**

```bash
git add src/goodteam_os/project_import.py src/goodteam_os/projects.py tests/test_project_import.py
git commit -m "feat(projects): add safe idempotent import"
```

---

### Task 3: Expose focused project APIs and product-route fallback

**Files:**
- Modify: `src/goodteam_os/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `import_project()` and `ProjectReadinessStore`.
- Produces: `GET /api/projects`.
- Produces: `POST /api/projects/import`.
- Produces: `GET /api/projects/{key}/home`.
- Produces: `POST /api/projects/{key}/mode`.
- Produces: shared `handle_project_api_get(workspace, path)` and
  `handle_project_api_post(workspace, path, request)` used by both dashboard
  handler implementations in `web.py`.
- Produces: `is_product_spa_path(path: str) -> bool`.

- [ ] **Step 1: Write API contract tests**

Add tests using the existing `_post_web_json()` helper and HTTP test server:

```python
def _get_web_json(workspace: Path, route: str):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}{route}",
            timeout=5,
        ) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_project_import_returns_created_home_contract(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root), "display_name": "Target"},
    )
    assert status == 201
    assert payload["created"] is True
    assert payload["project"]["project_key"] == "TARGET"
    assert payload["readiness"]["lifecycle"] == "preparing"
    assert payload["capabilities"]["start_workflow"] is False
    assert payload["home_url"] == "/projects/TARGET/home"


def test_api_project_import_returns_structured_missing_root_error(tmp_path: Path) -> None:
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(tmp_path / "missing")},
    )
    assert status == 404
    assert payload["code"] == "project_root_not_found"
    assert payload["recovery"] == ["choose_another_directory"]
    assert payload["request_id"]


def test_api_project_mode_confirmation_updates_home_projection(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"x")
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/AMBIGUOUS/mode",
        {"mode": "existing_project"},
    )
    assert status == 200
    assert payload["readiness"]["project_mode"] == "existing_project"
    assert payload["next_action"]["id"] == "repository_understanding"


def test_project_home_restores_from_disk_after_handler_restart(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    (root / "package.json").write_text('{"name":"existing"}', encoding="utf-8")

    first_status, imported = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root)},
    )
    assert first_status == 201

    # _post_web_json has shut down its server. _get_web_json constructs a new
    # handler and server against the same workspace, forcing a disk reload.
    second_status, restored = _get_web_json(
        tmp_path,
        f"/api/projects/{imported['project']['project_key']}/home",
    )
    assert second_status == 200
    assert restored["readiness"] == imported["readiness"]
    assert restored["project"]["root"] == str(root.resolve())


def test_project_list_projects_attention_and_active_run_fields(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})

    status, payload = _get_web_json(tmp_path, "/api/projects")

    assert status == 200
    assert len(payload["projects"]) == 1
    assert payload["projects"][0]["readiness"]["lifecycle"] == "attention_required"
    assert payload["projects"][0]["attention_count"] == 1
    assert payload["projects"][0]["active_run"] is None
```

- [ ] **Step 2: Run focused API tests and verify red**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k "project_import or project_mode_confirmation"
```

Expected: requests return 404 because the routes do not exist.

- [ ] **Step 3: Add project view-model builders and structured errors**

Add these helpers in `web.py` near existing project overview builders:

```python
def build_project_home(workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    try:
        config = _project_config_for_key(workspace, key)
    except ValueError as exc:
        raise ProjectApiError(
            "project_not_found",
            str(exc),
            404,
            details={"project_key": key},
            recovery=["return_to_projects"],
        ) from exc
    target = load_project_target(config)
    key = normalize_project_key(str(target.get("key", key)))
    root = resolve_project_runtime_root(dict(target.get("repos", {})))
    readiness = ProjectReadinessStore(workspace).read(key)
    if readiness is None:
        raise ProjectApiError(
            "project_readiness_missing",
            f"Project readiness is missing: {key}",
            409,
            details={"project_key": key},
            recovery=["return_to_projects", "reload_project"],
        )
    if root is None:
        raise ProjectApiError(
            "project_root_unavailable",
            f"Project root is unavailable: {key}",
            409,
            details={"project_key": key},
            recovery=["edit_project_settings", "return_to_projects"],
        )
    state = StateStore(workspace).read_project_current(key)
    active_run = (
        {"run_id": state.run_id, "next_node": state.next_node}
        if is_active_run(state)
        else None
    )
    pending = next((step for step in readiness["steps"] if step["status"] != "complete"), None)
    return {
        "project": {
            "project_key": key,
            "display_name": str(target.get("name", key)),
            "root": str(root),
        },
        "facts": {
            "root": str(root),
            "detected_mode": readiness["project_mode"],
            "active_run": active_run,
        },
        "readiness": readiness,
        "capabilities": build_capabilities(readiness),
        "next_action": pending or {"id": "shared_readiness_complete", "status": "complete"},
    }
```

Define `ProjectApiError(code, message, status, details=None, recovery=None)` for
unknown projects, invalid modes, and missing readiness. Create `_send_api_error()`
on `DashboardHandler` that always serializes the same five fields and a generated
`request_id`; it handles `ProjectApiError` directly and maps
`ProjectImportError.code` to status/recovery:

```python
IMPORT_ERROR_STATUS = {
    "project_root_not_found": 404,
    "project_root_not_directory": 400,
    "project_root_not_readable": 403,
}
IMPORT_ERROR_RECOVERY = {
    "project_root_not_found": ["choose_another_directory"],
    "project_root_not_directory": ["choose_another_directory"],
    "project_root_not_readable": ["fix_permissions", "choose_another_directory"],
}
```

Unexpected exceptions on these new endpoints must also pass through this helper
as status 500, code `internal_error`, message `GoodTeam could not complete this
request.`, empty details, recovery `['retry', 'report_request_id']`, and a request
ID. Log the exception with that ID server-side; never expose its raw text in the
response.

Add these contract tests:

```python
def test_unknown_project_home_uses_stable_error_shape(tmp_path: Path) -> None:
    status, payload = _get_web_json(tmp_path, "/api/projects/MISSING/home")

    assert status == 404
    assert payload["code"] == "project_not_found"
    assert payload["details"] == {"project_key": "MISSING"}
    assert payload["recovery"] == ["return_to_projects"]
    assert payload["request_id"]


def test_invalid_project_mode_uses_stable_error_shape(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})

    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/AMBIGUOUS/mode",
        {"mode": "guess"},
    )
    assert status == 400
    assert payload["code"] == "invalid_project_mode"
    assert payload["recovery"] == ["choose_project_mode"]
    assert payload["request_id"]


def test_project_import_hides_unexpected_exception_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_import(*args, **kwargs):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(web, "import_project", fail_import)
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(tmp_path)},
    )
    assert status == 500
    assert payload["code"] == "internal_error"
    assert payload["message"] == "GoodTeam could not complete this request."
    assert "sensitive" not in json.dumps(payload)
    assert payload["request_id"]
```

- [ ] **Step 4: Route project GET and POST endpoints**

Add `do_GET()` to the focused handler returned by `build_dashboard_handler()`;
it delegates only recognized project API paths to `handle_project_api_get()` and
returns 404 otherwise. In the full handler's `do_GET()`, route `/api/projects`
before legacy `/api/state`. Return every
discovered project with `project`, `readiness`, `capabilities`, `attention_count`,
and `active_run`. Derive `attention_count` from `needs_input` and `failed`
readiness steps. Project the current run from `StateStore.read_project_current()`
as `{run_id, next_node}` only when it is active, otherwise `null`. Legacy projects
without readiness use lifecycle `legacy` and are not mutated by the read.

Match home with:

```python
home_match = re.fullmatch(r"/api/projects/([^/]+)/home", parsed.path)
```

In `do_POST()`, add project import and mode routes to the same-origin guard
as other mutations. Import returns 201 for `created=True` and 200 for a duplicate.
Mode confirmation accepts only `new_project` or `existing_project`, calls
`ProjectReadinessStore.confirm_mode()`, and returns a freshly built project Home
payload with `readiness` and `next_action`. Put the
project endpoint logic in `handle_project_api_get()` and
`handle_project_api_post()`, then call those helpers from both
`build_dashboard_handler()` and the full `serve()` handler so focused tests and
the live product use one contract implementation.

- [ ] **Step 5: Write and implement SPA product-path fallback tests**

```python
def test_product_spa_path_contract() -> None:
    assert is_product_spa_path("/projects") is True
    assert is_product_spa_path("/projects/DEMO/home") is True
    assert is_product_spa_path("/inbox") is False
    assert is_product_spa_path("/api/projects") is False
    assert is_product_spa_path("/review-workbench/run-1") is False
    assert is_product_spa_path("/assets/index.js") is False


```

Implement:

```python
def is_product_spa_path(path: str) -> bool:
    return bool(
        path == "/projects"
        or re.fullmatch(r"/projects/[^/]+/home", path)
    )
```

When a non-API path has no matching static asset and `is_product_spa_path()` is
true, serve `dist/index.html`; preserve explicit review-workbench and execution
handlers before this fallback. Successor plans extend the allowlist only when
their corresponding screens exist, so a future-looking route never masquerades
as an implemented product surface.

- [ ] **Step 6: Run focused web tests and Ruff**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py tests/test_project_import.py tests/test_project_readiness.py -q -k "project_ or product_spa_path"
.venv/bin/ruff check src/goodteam_os/web.py src/goodteam_os/project_import.py src/goodteam_os/project_readiness.py tests/test_web.py tests/test_project_import.py tests/test_project_readiness.py
```

Expected: focused tests pass and Ruff is clean.

- [ ] **Step 7: Commit the API contracts**

```bash
git add src/goodteam_os/web.py tests/test_web.py
git commit -m "feat(web): expose project import and home APIs"
```

---

### Task 4: Establish the frontend test, design-system, and i18n foundation

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.js`
- Create: `frontend/src/test/setup.js`
- Create: `frontend/src/components/ui/Button.jsx`
- Create: `frontend/src/components/ui/InlineNotice.jsx`
- Create: `frontend/src/components/ui/PageState.jsx`
- Create: `frontend/src/components/ui/StatusBadge.jsx`
- Create: `frontend/src/styles/product.css`
- Modify: `frontend/src/i18n/index.js`
- Modify: `frontend/src/i18n/locales/en.js`
- Modify: `frontend/src/i18n/locales/zh-CN.js`
- Create: `frontend/src/components/ui/ui.test.jsx`
- Create: `frontend/src/i18n/catalogs.vitest.js`

**Interfaces:**
- Produces: `npm run test:node --prefix frontend` for existing Node tests,
  `npm run test:unit --prefix frontend` for Vitest, and a combined
  `npm test --prefix frontend` runner.
- Produces: `missingTranslationKeys(language: "en" | "zh-CN") -> string[]`.
- Produces: accessible `Button`, `InlineNotice`, `PageState`, and `StatusBadge` primitives.
- Produces: `.product-shell` token scope for redesigned routes.

- [ ] **Step 1: Install the route and frontend test dependencies**

Run from `frontend/`:

```bash
npm install react-router-dom @fontsource-variable/geist@5.2.9 @fontsource-variable/geist-mono@5.2.8
npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @playwright/test
```

Update scripts to:

```json
{
  "test": "npm run test:node && npm run test:unit",
  "test:node": "node --test",
  "test:unit": "vitest run",
  "test:watch": "vitest",
  "test:e2e": "playwright test"
}
```

- [ ] **Step 2: Configure Vitest and write failing primitive tests**

Add to `vite.config.js`:

```javascript
test: {
  environment: 'jsdom',
  setupFiles: './src/test/setup.js',
  css: true,
  include: ['src/**/*.test.jsx', 'src/**/*.vitest.js'],
}
```

`frontend/src/test/setup.js`:

```javascript
import '@testing-library/jest-dom/vitest';
```

`frontend/src/components/ui/ui.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import Button from './Button';
import PageState from './PageState';

test('button exposes disabled and busy state accessibly', () => {
  render(<Button busy>Load project</Button>);
  expect(screen.getByRole('button', { name: 'Load project' })).toBeDisabled();
  expect(screen.getByRole('button')).toHaveAttribute('aria-busy', 'true');
});

test('page error state keeps the recovery action inline', () => {
  render(<PageState kind="error" title="Cannot read project" action={<button>Choose another directory</button>} />);
  expect(screen.getByRole('heading', { name: 'Cannot read project' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose another directory' })).toBeVisible();
});
```

Run `npm run test:unit --prefix frontend -- ui.test.jsx`; expect failure because the
primitives do not exist.

- [ ] **Step 3: Implement the UI primitives and product token scope**

`Button` accepts `variant="primary" | "secondary" | "danger"`, `busy`, and all
native button props. When busy, it disables itself, preserves its label, and sets
`aria-busy=true`.

`PageState` renders a semantic section with an `h2`, optional description, and
optional action. It supports `loading`, `empty`, `error`, `needs_input`, and
`complete` without embedding product copy.

Define these product tokens in `product.css`:

Import the self-hosted variable fonts once from `main.jsx`:

```javascript
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
```

```css
.product-shell {
  --product-bg: #f3f0e9;
  --product-surface: #fffdf8;
  --product-surface-muted: #ece8df;
  --product-border: #d8d2c8;
  --product-text: #292723;
  --product-text-muted: #777064;
  --product-accent: #9d5d42;
  --product-accent-strong: #7f4933;
  --product-success: #41735a;
  --product-warning: #8a6b25;
  --product-danger: #984f49;
  min-height: 100dvh;
  color: var(--product-text);
  background: var(--product-bg);
  font-family: "Geist Variable", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 15px;
}

.product-shell code,
.product-shell pre,
.product-shell [data-monospace] {
  font-family: "Geist Mono Variable", ui-monospace, SFMono-Regular, monospace;
}

.dark .product-shell {
  --product-bg: #191816;
  --product-surface: #23211e;
  --product-surface-muted: #151412;
  --product-border: #3b3832;
  --product-text: #eee9df;
  --product-text-muted: #aaa398;
  --product-accent: #c07859;
  --product-accent-strong: #d18b6c;
}

@media (prefers-reduced-motion: reduce) {
  .product-shell *, .product-shell *::before, .product-shell *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
```

- [ ] **Step 4: Add strict catalog completeness tests**

Export a `PRODUCT_I18N_PREFIXES` array with `product.`, `projects.`, and
`readiness.`. Implement `missingTranslationKeys(language)` by comparing every
prefixed English key with the requested locale.

Test:

```javascript
import { missingTranslationKeys } from './index';
import { expect, test } from 'vitest';

test.each(['en', 'zh-CN'])('%s has every redesigned product key', (language) => {
  expect(missingTranslationKeys(language)).toEqual([]);
});
```

Add complete paired keys for global shell, project import, lifecycle, all shared
and mode-specific readiness step IDs, errors, recovery actions, Home, and project
facts. Do not use fallback keys as shipped labels.

- [ ] **Step 5: Run frontend tests, lint, and build**

```bash
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

Expected: all tests pass, ESLint has no errors, and Vite completes a production
build.

- [ ] **Step 6: Commit the frontend foundation**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.js frontend/src/test frontend/src/components/ui frontend/src/styles/product.css frontend/src/i18n
git commit -m "feat(frontend): add product design and test foundation"
```

---

### Task 5: Build the new Projects page and one-time load flow

**Files:**
- Create: `frontend/src/lib/api/client.js`
- Create: `frontend/src/features/projects/projectApi.js`
- Create: `frontend/src/features/projects/LoadProjectPanel.jsx`
- Create: `frontend/src/features/projects/ProjectsPage.jsx`
- Create: `frontend/src/features/projects/ProjectsPage.test.jsx`

**Interfaces:**
- Produces: `requestJson(path, options) -> Promise<object>` and `ApiError`.
- Produces: `listProjects()` and `importProject({root})` for this first-load UI.
- Consumes: `useDirectoryPicker`, `Button`, `InlineNotice`, and product i18n keys.
- Emits: `onImported(payload)` with the server import result.

- [ ] **Step 1: Write the API client and Projects interaction tests**

```jsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import ProjectsPage from './ProjectsPage';

test('loads a selected project once and navigates to its server home URL', async () => {
  const user = userEvent.setup();
  const navigate = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockResolvedValue({
      created: true,
      project: { project_key: 'DEMO', display_name: 'Demo' },
      home_url: '/projects/DEMO/home',
    }),
  };
  render(
    <MemoryRouter>
      <ProjectsPage api={api} navigate={navigate} language="en" />
    </MemoryRouter>,
  );
  await user.type(await screen.findByLabelText('Project directory'), '/tmp/demo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));
  expect(api.importProject).toHaveBeenCalledWith({ root: '/tmp/demo' });
  expect(navigate).toHaveBeenCalledWith('/projects/DEMO/home');
});

test('shows a localized inline recovery for an unreadable root', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockRejectedValue(Object.assign(new Error('denied'), {
      code: 'project_root_not_readable',
      recovery: ['fix_permissions', 'choose_another_directory'],
    })),
  };
  render(<MemoryRouter><ProjectsPage api={api} navigate={vi.fn()} language="en" /></MemoryRouter>);
  await user.type(await screen.findByLabelText('Project directory'), '/private/repo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));
  expect(await screen.findByText('GoodTeam cannot read this directory.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose another directory' })).toBeVisible();
});
```

- [ ] **Step 2: Run Projects tests and verify red**

```bash
npm run test:unit --prefix frontend -- ProjectsPage.test.jsx
```

Expected: imports fail because the new project feature files do not exist.

- [ ] **Step 3: Implement stable JSON error normalization**

`client.js`:

```javascript
export class ApiError extends Error {
  constructor(payload, status) {
    super(payload.message || payload.error || `Request failed (${status})`);
    this.name = 'ApiError';
    this.status = status;
    this.code = payload.code || 'unknown_error';
    this.details = payload.details || {};
    this.recovery = Array.isArray(payload.recovery) ? payload.recovery : [];
    this.requestId = payload.request_id || '';
  }
}

export async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new ApiError(payload, response.status);
  return payload;
}
```

`projectApi.js`:

```javascript
import { requestJson } from '../../lib/api/client';

export const listProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });

export const importProject = ({ root }) => requestJson('/api/projects/import', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ root }),
});
```

Export those functions both as named exports and as a `projectApi` object. Define
`ProjectsPage({api = projectApi, navigate: navigateOverride, language:
languageOverride})`; it always calls `useNavigate()` and `useOutletContext()`,
then prefers injected test props and otherwise uses ProductLayout's outlet
context. This keeps the production data flow explicit while allowing deterministic
interaction tests. Its mount effect creates an `AbortController`, passes
`signal` to `listProjects()`, and aborts on unmount; an aborted request never
replaces visible state with an error.

- [ ] **Step 4: Implement the Projects page**

The page must:

- fetch `/api/projects` on mount with a shape-matching skeleton;
- group attention-required, active-run, preparing, ready, and legacy projects in
  that order using the server's `attention_count`, `active_run`, and lifecycle;
- render a manual absolute-path input plus a Browse button using
  `useDirectoryPicker.chooseLocalPath()`;
- submit only `{root}` for the first import operation;
- keep server errors inline and preserve the entered path;
- navigate to the returned `home_url` for created and duplicate imports;
- render no dead Inbox, Activity, or Settings links in this first slice.

Use semantic `main`, `section`, `form`, and `article` elements. The path label,
button, empty state, lifecycle labels, and error recovery text come from the
selected locale.

- [ ] **Step 5: Run focused frontend validation**

```bash
npm run test:unit --prefix frontend -- ProjectsPage.test.jsx
npm run lint --prefix frontend
npm run build --prefix frontend
```

Expected: focused tests, lint, and build pass.

- [ ] **Step 6: Commit Projects**

```bash
git add frontend/src/lib/api frontend/src/features/projects
git commit -m "feat(frontend): add safe project load portfolio"
```

---

### Task 6: Build recoverable project Home and mode confirmation

**Files:**
- Create: `frontend/src/features/projects/ProjectHomePage.jsx`
- Create: `frontend/src/features/projects/ReadinessCenter.jsx`
- Create: `frontend/src/features/projects/ProjectFacts.jsx`
- Create: `frontend/src/features/projects/ProjectHomePage.test.jsx`
- Modify: `frontend/src/features/projects/projectApi.js`

**Interfaces:**
- Consumes: `GET /api/projects/{key}/home` and `POST /api/projects/{key}/mode`.
- Produces: `getProjectHome(projectKey)` and `confirmProjectMode(projectKey, mode)`.
- Renders: one next action, persisted readiness steps, project facts, and server capability reason.

- [ ] **Step 1: Write Home recovery and mode tests**

```jsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import ProjectHomePage from './ProjectHomePage';

const ambiguousHome = {
  project: { project_key: 'ASSETS', display_name: 'Assets', root: '/tmp/assets' },
  readiness: {
    lifecycle: 'attention_required',
    project_mode: 'unconfirmed',
    steps: [
      { id: 'project_record', status: 'complete', required: true },
      { id: 'safe_preflight', status: 'complete', required: true },
      { id: 'project_mode', status: 'needs_input', required: true },
    ],
  },
  capabilities: { start_workflow: false, start_workflow_reason: 'project_mode_unconfirmed' },
  next_action: { id: 'project_mode', status: 'needs_input' },
};

test('restores readiness and confirms an ambiguous project as existing', async () => {
  const user = userEvent.setup();
  const api = {
    getProjectHome: vi.fn().mockResolvedValue(ambiguousHome),
    confirmProjectMode: vi.fn().mockResolvedValue({
      ...ambiguousHome,
      readiness: { ...ambiguousHome.readiness, project_mode: 'existing_project' },
      next_action: { id: 'repository_understanding', status: 'pending' },
    }),
  };
  render(<ProjectHomePage projectKey="ASSETS" api={api} language="en" />);
  expect(await screen.findByText('Choose how GoodTeam should prepare this project')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Understand an existing codebase' }));
  expect(api.confirmProjectMode).toHaveBeenCalledWith('ASSETS', 'existing_project');
  expect(await screen.findByText('Repository understanding')).toBeVisible();
});

test('shows why workflow start is disabled', async () => {
  const api = { getProjectHome: vi.fn().mockResolvedValue(ambiguousHome) };
  render(<ProjectHomePage projectKey="ASSETS" api={api} language="en" />);
  expect(await screen.findByRole('button', { name: 'Start workflow' })).toBeDisabled();
  expect(screen.getByText('Confirm the project type before starting work.')).toBeVisible();
});
```

- [ ] **Step 2: Run Home tests and verify red**

```bash
npm run test:unit --prefix frontend -- ProjectHomePage.test.jsx
```

Expected: imports fail because Home components do not exist.

- [ ] **Step 3: Add Home API functions**

```javascript
export const getProjectHome = (projectKey, { signal } = {}) => (
  requestJson(`/api/projects/${encodeURIComponent(projectKey)}/home`, { signal })
);

export const confirmProjectMode = (projectKey, mode) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/mode`,
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  },
);
```

Define `ProjectHomePage({projectKey, api = projectApi, language:
languageOverride})` with the same injected-prop precedence as `ProjectsPage`.
Production receives `language` from `useOutletContext()`; component tests inject
it directly. Its load effect also passes an `AbortController.signal` and aborts
on project-key change or unmount without rendering an error for `AbortError`.

- [ ] **Step 4: Implement Home, ReadinessCenter, and ProjectFacts**

Home uses this hierarchy:

1. project name, key, and lifecycle;
2. one next-action module;
3. readiness steps with status and required marker;
4. verified project facts: root, detected mode, active run, and worker status when present;
5. currently unlocked capabilities.

When `project_mode` is `unconfirmed`, the next-action module renders three
actions: new project, existing codebase, and decide later. `decide later` leaves
state unchanged and never blocks navigation away.

After mode confirmation, the first mode-specific pending step becomes the next
action. This plan intentionally does not expose a run button for those pending
steps; it shows their real pending state and the server capability reason. The
executors are the complete scope of implementation plan 2.

- [ ] **Step 5: Add Chinese Home behavior test**

```jsx
test('renders the same ambiguous decision contract in Simplified Chinese', async () => {
  const api = { getProjectHome: vi.fn().mockResolvedValue(ambiguousHome) };
  render(<ProjectHomePage projectKey="ASSETS" api={api} language="zh-CN" />);
  expect(await screen.findByText('选择 GoodTeam 应如何准备这个项目')).toBeVisible();
  expect(screen.getByRole('button', { name: '理解现有代码库' })).toBeVisible();
});
```

- [ ] **Step 6: Run focused frontend validation**

```bash
npm run test:unit --prefix frontend -- ProjectHomePage.test.jsx ProjectsPage.test.jsx
npm run lint --prefix frontend
npm run build --prefix frontend
```

Expected: Home and Projects tests pass; lint and build pass.

- [ ] **Step 7: Commit Home**

```bash
git add frontend/src/features/projects frontend/src/i18n
git commit -m "feat(frontend): add recoverable project readiness home"
```

---

### Task 7: Wire nested routes while preserving the legacy application

**Files:**
- Create: `frontend/src/app/AppRouter.jsx`
- Create: `frontend/src/app/ProductLayout.jsx`
- Create: `frontend/src/app/NotFoundPage.jsx`
- Create: `frontend/src/app/AppRouter.test.jsx`
- Modify: `frontend/src/main.jsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `ProjectsPage`, `ProjectHomePage`, `useAppearancePreferences`, and react-router-dom.
- Produces: `/projects` and `/projects/:projectKey/home`.
- Preserves: `/` rendering the existing `App` and existing query-string project routing.

- [ ] **Step 1: Write route compatibility tests**

```jsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, Link, RouterProvider } from 'react-router-dom';
import { expect, test } from 'vitest';
import { createProductRoutes } from './AppRouter';

function ProjectsStub() {
  return (
    <main>
      <h1>Projects</h1>
      <Link to="/projects/DEMO/home">Demo</Link>
    </main>
  );
}

function HomeStub() {
  return <h1>Project home</h1>;
}

test('new project route renders the redesigned portfolio', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);
  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
});

test('legacy root still renders the current application', async () => {
  const router = createMemoryRouter(createProductRoutes(), { initialEntries: ['/?project=DEMO'] });
  render(<RouterProvider router={router} />);
  expect(await screen.findByTestId('legacy-app-root')).toBeInTheDocument();
});

test('browser back returns from project Home to Projects', async () => {
  const user = userEvent.setup();
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);

  await user.click(await screen.findByRole('link', { name: 'Demo' }));
  expect(await screen.findByRole('heading', { name: 'Project home' })).toBeVisible();
  await router.navigate(-1);
  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
});
```

Add `data-testid="legacy-app-root"` to the existing App root container only; do
not restructure `App.jsx` in this plan.

- [ ] **Step 2: Run router tests and verify red**

```bash
npm run test:unit --prefix frontend -- AppRouter.test.jsx
```

Expected: route module is missing.

- [ ] **Step 3: Implement the additive route tree**

```jsx
export function createProductRoutes({
  ProjectsComponent = ProjectsPage,
  ProjectHomeComponent = ProjectHomePage,
} = {}) {
  return [
    { path: '/', element: <LegacyApp /> },
    {
      element: <ProductLayout />,
      children: [
        { path: '/projects', element: <ProjectsComponent /> },
        {
          path: '/projects/:projectKey/home',
          element: <ProjectHomeRoute Component={ProjectHomeComponent} />,
        },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}
```

`ProjectHomeRoute` reads `projectKey` with `useParams()` and passes it to its
injected `Component`. Production uses `ProjectHomePage`; route tests inject inert
components and therefore never make network requests. `ProductLayout` applies
`.product-shell`, renders only the
implemented Projects global navigation item, provides locale and appearance
controls, and renders `<Outlet context={{ language, t }} />`. Its locale control is a native `select` with
localized accessible label `Language` / `语言` and values `auto`, `en`, and
`zh-CN`; this is the contract used by the Playwright test.

Replace `main.jsx` with `createBrowserRouter(createProductRoutes())` and
`RouterProvider`. Import `product.css` after `index.css`.

- [ ] **Step 4: Verify browser history and direct-route behavior**

The Step 1 history test covers Projects → Home → browser back. Build the frontend,
then run these commands from the repository root.

Terminal A:

```bash
mkdir -p /private/tmp/goodteam-project-route-smoke/workspace
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli serve --workspace /private/tmp/goodteam-project-route-smoke/workspace --host 127.0.0.1 --port 8898
```

Terminal B:

```bash
curl -fsS -o /private/tmp/goodteam-project-route-smoke/root.html http://127.0.0.1:8898/
curl -fsS -o /private/tmp/goodteam-project-route-smoke/home.html http://127.0.0.1:8898/projects/DEMO/home
cmp /private/tmp/goodteam-project-route-smoke/root.html /private/tmp/goodteam-project-route-smoke/home.html
curl -fsS -H 'Accept: application/json' http://127.0.0.1:8898/api/projects
```

Expected: `cmp` exits 0 and the final command prints a JSON object containing a
`projects` array. Stop Terminal A with Ctrl-C. Retain the isolated smoke directory
until verification evidence is recorded.

- [ ] **Step 5: Run route and regression validation**

```bash
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k "product_spa_path or frontend"
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Commit additive routing**

```bash
git add frontend/src/app frontend/src/main.jsx frontend/src/index.css frontend/src/App.jsx
git commit -m "feat(frontend): add redesigned project routes"
```

---

### Task 8: Add isolated browser acceptance and complete verification

**Files:**
- Create: `frontend/e2e/global-setup.js`
- Create: `frontend/e2e/project-load-home.spec.js`
- Create: `frontend/e2e/project-load-home.spec.js-snapshots/*.png`
- Create: `frontend/playwright.config.js`
- Modify: `README.md`
- Modify: `docs/operator-guide.md`

**Interfaces:**
- Validates: direct routes, manual path import, duplicate import, restart recovery, mode confirmation, bilingual UI, and no target-root mutation.
- Documents: additive `/projects` preview routes and isolated verification commands.

- [ ] **Step 1: Create deterministic isolated E2E fixtures**

`global-setup.js` must remove only `/private/tmp/goodteam-project-load-e2e`, then
create:

```text
/private/tmp/goodteam-project-load-e2e/workspace
/private/tmp/goodteam-project-load-e2e/targets/empty
/private/tmp/goodteam-project-load-e2e/targets/existing/package.json
/private/tmp/goodteam-project-load-e2e/targets/ambiguous/blob.bin
/private/tmp/goodteam-project-load-e2e/targets/blocked
```

Use Node `fs.rmSync(root, {recursive: true, force: true})`, where `root` is the
literal `/private/tmp/goodteam-project-load-e2e`; do not accept an environment
override for this destructive cleanup.

Use this complete setup:

```javascript
import fs from 'node:fs';
import path from 'node:path';

export default async function globalSetup() {
  const root = '/private/tmp/goodteam-project-load-e2e';
  fs.rmSync(root, { recursive: true, force: true });
  fs.mkdirSync(path.join(root, 'workspace'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'empty'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'existing'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'ambiguous'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'blocked'), { recursive: true });
  fs.writeFileSync(
    path.join(root, 'targets', 'existing', 'package.json'),
    JSON.stringify({ name: 'existing' }, null, 2),
  );
  fs.writeFileSync(path.join(root, 'targets', 'ambiguous', 'blob.bin'), 'asset');
  fs.chmodSync(path.join(root, 'targets', 'blocked'), 0o000);
}
```

- [ ] **Step 2: Configure Playwright against the built app**

`frontend/playwright.config.js`:

```javascript
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.js',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8899',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: 'PYTHONPATH=../src ../.venv/bin/python -m goodteam_os.cli serve --workspace /private/tmp/goodteam-project-load-e2e/workspace --host 127.0.0.1 --port 8899',
    url: 'http://127.0.0.1:8899/projects',
    reuseExistingServer: false,
    timeout: 120000,
  },
});
```

Run `npm run build --prefix frontend` before `npm run test:e2e --prefix frontend`.

- [ ] **Step 3: Write the browser acceptance test**

The Playwright test must:

1. open `/projects` directly;
2. capture the empty Projects light/English desktop baseline;
3. import the existing fixture through the manual path field;
4. assert navigation to `/projects/EXISTING/home`;
5. assert Home reports `existing_project` or offers the mode confirmation when
   the classifier returns unconfirmed;
6. reload and assert the same readiness state (the handler-restart disk restore
   contract is exercised separately in Task 3);
7. return to Projects and import the same root again;
8. assert navigation to the same project and one project record in the list;
9. import the empty fixture and assert the new-project readiness path;
10. submit the unreadable fixture and assert inline recovery, then restore its
    permission for local cleanup;
11. import the ambiguous fixture and choose existing codebase;
12. switch to Simplified Chinese and assert Home labels change while the absolute
   path remains unchanged;
13. capture Chinese mobile Home and English dark desktop Home baselines;
14. inspect the target fixtures with Node `fs` and assert neither `docs` nor
    `.git` was created;
15. assert there are no browser console errors.

Use this test. Keep the accessible labels identical to the paired i18n catalog
values created in Tasks 4–7:

```javascript
import fs from 'node:fs';
import { expect, test } from '@playwright/test';

const root = '/private/tmp/goodteam-project-load-e2e';
const emptyRoot = `${root}/targets/empty`;
const existingRoot = `${root}/targets/existing`;
const ambiguousRoot = `${root}/targets/ambiguous`;
const blockedRoot = `${root}/targets/blocked`;

test('loads once, restores Home, confirms mode, and switches language', async ({ page }) => {
  const consoleErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));

  await page.goto('/projects');
  await expect(page.getByText('No projects yet')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('projects-en-light-desktop.png', { fullPage: true });
  await page.getByLabel('Project directory').fill(existingRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/home$/);
  await expect(page.getByText('Repository understanding')).toBeVisible();
  await page.reload();
  await expect(page.getByText(existingRoot)).toBeVisible();

  await page.goto('/projects');
  await page.getByLabel('Project directory').fill(existingRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/home$/);
  await page.goto('/projects');
  await expect(page.getByText('existing')).toHaveCount(1);

  await page.getByLabel('Project directory').fill(emptyRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EMPTY\/home$/);
  await expect(page.getByText('New project initialization')).toBeVisible();

  await page.goto('/projects');
  try {
    await page.getByLabel('Project directory').fill(blockedRoot);
    await page.getByRole('button', { name: 'Load project' }).click();
    await expect(page.getByText('GoodTeam cannot read this directory.')).toBeVisible();
  } finally {
    fs.chmodSync(blockedRoot, 0o700);
  }

  await page.getByLabel('Project directory').fill(ambiguousRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/AMBIGUOUS\/home$/);
  await page.getByRole('button', { name: 'Understand an existing codebase' }).click();
  await expect(page.getByText('Repository understanding')).toBeVisible();

  await page.getByLabel('Language').selectOption('zh-CN');
  await expect(page.getByText('代码库理解')).toBeVisible();
  await expect(page.getByText(ambiguousRoot)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('home-zh-light-mobile.png', { fullPage: true });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => {
    localStorage.setItem('gt-language', 'en');
    localStorage.setItem('color-scheme', 'dark');
  });
  await page.reload();
  await expect(page.getByText('Repository understanding')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('home-en-dark-desktop.png', { fullPage: true });

  expect(fs.existsSync(`${existingRoot}/docs`)).toBe(false);
  expect(fs.existsSync(`${existingRoot}/.git`)).toBe(false);
  expect(fs.readdirSync(emptyRoot)).toEqual([]);
  expect(fs.readFileSync(`${ambiguousRoot}/blob.bin`, 'utf8')).toBe('asset');
  expect(consoleErrors).toEqual([]);
});
```

- [ ] **Step 4: Run focused and full backend validation**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_readiness.py tests/test_project_import.py tests/test_web.py -q -k "project or product_spa_path"
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/
```

Expected: focused tests and the full suite pass; Ruff reports no errors.

- [ ] **Step 5: Run full frontend and isolated browser validation**

```bash
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend -- --update-snapshots
npm run test:e2e --prefix frontend
git diff --check
```

Expected: unit tests, lint, build, and both Playwright runs pass. Inspect every
new PNG before accepting it, commit the baselines under
`frontend/e2e/project-load-home.spec.js-snapshots/`, and capture the Playwright
result, viewport, language paths, target root, and absence of console errors as
the Tier 4 smoke evidence.

- [ ] **Step 6: Update operator documentation**

Document:

- `/projects` is the additive redesigned preview and `/` remains legacy;
- import is read-only with respect to the selected root;
- shared readiness and mode confirmation survive restart;
- mode-specific Init and repository-understanding execution require plan 2;
- exact verification tier, changed contract area, commands, pass counts, and the
  isolated workspace used.

- [ ] **Step 7: Commit verification and documentation**

```bash
git add frontend/e2e frontend/playwright.config.js README.md docs/operator-guide.md
git commit -m "test(frontend): cover isolated project load and recovery"
```

---

## Spec Coverage Review

| Approved spec area | Coverage in this plan |
| --- | --- |
| Loaded versus ready lifecycle | Tasks 1–3 |
| Read-only, idempotent minimal import | Tasks 2–3 and Task 8 E2E |
| Shared readiness persistence and capability projection | Tasks 1, 3, and 6 |
| Additive durable routes and SPA fallback | Tasks 3 and 7 |
| Calm product visual foundation | Task 4 and ProductLayout in Task 7 |
| Simplified Chinese, English, and auto locale | Tasks 4, 6, 7, and 8 |
| Projects portfolio and recoverable Home | Tasks 5–8 |
| Loading, empty, error, needs-input, and complete primitives | Task 4; route-specific use in Tasks 5–6 |
| Accessibility and responsive foundation | Tasks 4, 5, 7, and browser acceptance in Task 8 |
| New-project Init executors | Deliberately assigned to implementation plan 2; this plan persists the real pending steps but exposes no nonfunctional executor action |
| Existing-project understanding executors | Deliberately assigned to implementation plan 2 under the same readiness execution contract |
| Work workspace | Deliberately assigned to implementation plan 3 |
| Review, Knowledge, settings, and legacy retirement | Deliberately assigned to implementation plan 4 |

There are no uncovered requirements inside this plan's declared slice. The new
routes remain additive precisely because the mode-specific readiness executors
belong to plan 2.

---

## Completion Gate

Before claiming this plan complete, verify all of the following:

- [ ] A readable directory imports exactly once and opens `/projects/:key/home`.
- [ ] The target directory is byte-for-byte unchanged by import.
- [ ] Shared readiness and mode confirmation survive page reload and service restart.
- [ ] The server capability projection blocks workflow start with a stable reason.
- [ ] New routes are complete in Simplified Chinese and English.
- [ ] `/` and `/?project=KEY` still render the legacy application.
- [ ] Direct product routes return the React entry; API and explicit backend routes do not fall through.
- [ ] Focused backend tests pass.
- [ ] Full backend pytest and Ruff pass.
- [ ] Frontend unit tests, lint, and production build pass.
- [ ] Isolated Playwright acceptance passes with no console errors.
- [ ] The PR body names Tier 4, project-root/import/readiness/routing contracts, every command and pass count, and skipped validation with safety rationale.
