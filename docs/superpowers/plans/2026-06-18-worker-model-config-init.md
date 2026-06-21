# Worker Model Config Init Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the New Project modal's free-text role/model init control with three bucket rows where users can choose backend + model from discovered local agents, while preserving clean default inheritance.

**Architecture:** Backend routing remains project config state (`worker_backend:` + `workers:`); model routing remains `role_model_overrides:`. The init modal computes bucket overrides, sends `workers` + `role_model_overrides` when creating config, and starts workflows through the resolved project config rather than adding transient `/api/start` backend-routing semantics.

**Tech Stack:** Python stdlib HTTP server, pytest, React/Vite, existing `/api/agents`, `CliBackendSpec`, Tailwind utility classes, existing frontend worker constants/hooks.

---

## File Map

- Modify `src/goodteam_os/workers.py`: register `opencode`, query `opencode models`, include it in `/api/agents`.
- Modify `src/goodteam_os/web.py`: accept `workers` in create-config payload, validate/persist them, return them, and ensure `/api/project-config` still routes create vs update correctly.
- Modify `frontend/src/constants/workers.js`: add init bucket helper exports and update stale Codex fallback models.
- Modify `frontend/src/utils/roleModelOverrides.js`: add pure bucket expansion helpers for create/start payloads.
- Modify `frontend/src/hooks/useNewProjectForm.js`: own init bucket selection state and reset it on modal open.
- Modify `frontend/src/App.jsx`: fetch agents for the modal, compute bucket payloads, create config before start when bucket backend overrides exist, pass props down.
- Modify `frontend/src/components/NewProjectModalContainer.jsx`: pass new init bucket props through.
- Modify `frontend/src/components/NewProjectModal.jsx`: render three backend/model bucket rows and keep the Advanced textarea as an escape hatch.
- Modify tests in `tests/test_workers.py`, `tests/test_web.py`, `tests/test_api.py`.

## Chosen `/api/start` Semantics

Do not add transient `workers` support to `/api/start` in this increment.

When the user presses Start and bucket backend overrides exist, frontend must create or update the project config first, then call `/api/start` with that `project_config`. This keeps backend routing in the one canonical place: project config.

---

### Task 1: Backend Opencode Discovery

**Files:**
- Modify: `src/goodteam_os/workers.py`
- Test: `tests/test_workers.py`

- [ ] **Step 1: Write failing opencode model parser and discovery tests**

Add these tests near existing discovery/model-query tests in `tests/test_workers.py`:

```python
def test_query_opencode_models_parses_plain_lines(monkeypatch):
    from goodteam_os.workers import _query_opencode_models

    def fake_run(command, check, capture_output, text, timeout):
        assert command == ["/fake/bin/opencode", "models"]
        assert check is False
        assert capture_output is True
        assert text is True
        assert timeout == 10
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="opencode-go/deepseek-v4-pro\nopencode-go/qwen3.7-max\nopencode-go/deepseek-v4-pro\n",
            stderr="",
        )

    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)

    assert _query_opencode_models("/fake/bin/opencode") == (
        "opencode-go/deepseek-v4-pro",
        "opencode-go/qwen3.7-max",
    )


def test_discover_installed_agents_reports_opencode_live_models(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setenv("CODEX_API_KEY", "codex-secret")
    monkeypatch.setattr("goodteam_os.workers._query_codex_models", lambda command_path: None)
    monkeypatch.setattr("goodteam_os.workers._query_agy_models", lambda command_path: None)
    monkeypatch.setattr(
        "goodteam_os.workers._query_opencode_models",
        lambda command_path: ("opencode-go/deepseek-v4-pro", "opencode-go/qwen3.7-max"),
    )

    by_backend = {item["backend"]: item for item in discover_installed_agents()}

    assert "opencode" in by_backend
    assert by_backend["opencode"]["available"] is True
    assert by_backend["opencode"]["ready"] is True
    assert by_backend["opencode"]["models_source"] == "live"
    assert by_backend["opencode"]["models"] == [
        "opencode-go/deepseek-v4-pro",
        "opencode-go/qwen3.7-max",
    ]


def test_command_with_prompt_supports_positional_arg_mode():
    from goodteam_os.workers import _command_with_prompt

    command = ["opencode", "run", "--dir", "/repo", "-m", "opencode-go/deepseek-v4-pro"]

    assert _command_with_prompt(command, "do the task", prompt_via="positional_arg") == [
        "opencode",
        "run",
        "--dir",
        "/repo",
        "-m",
        "opencode-go/deepseek-v4-pro",
        "do the task",
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py -q -k 'opencode or discover_installed_agents'
```

Expected: fails because `_query_opencode_models`, the `opencode` backend, and positional prompt mode are missing.

- [ ] **Step 3: Implement opencode backend**

In `src/goodteam_os/workers.py`, add constants near the existing backend constants:

```python
DEFAULT_OPENCODE_PATH = os.getenv("GOODTEAM_OPENCODE_PATH", "opencode")
DEFAULT_OPENCODE_MODEL = os.getenv("GOODTEAM_OPENCODE_MODEL", "opencode-go/deepseek-v4-pro")
OPENCODE_AUTH_ENV_NAMES = ("OPENCODE_API_KEY",)
```

Add this query function after `_query_agy_models`:

```python
def _query_opencode_models(command_path: str) -> tuple[str, ...] | None:
    try:
        completed = subprocess.run(
            [command_path, "models"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None

    models = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return tuple(dict.fromkeys(models)) or None
```

Add this command builder after `_agy_build_command`:

```python
def _opencode_build_command(binary: str, model: str, project_root: Path, result_path: Path) -> list[str]:
    command = [
        binary,
        "run",
        "--dir",
        str(project_root),
        "--dangerously-skip-permissions",
    ]
    model = model.strip()
    if model and model.lower() != "auto":
        command.extend(["-m", model])
    return command
```

Extend `_command_with_prompt` so positional-message CLIs can append the prompt:

```python
def _command_with_prompt(command: list[str], prompt: str, *, prompt_via: str) -> list[str]:
    if prompt_via == "stdin":
        return list(command)
    if prompt_via == "positional_arg":
        return [*command, prompt]
    if prompt_via != "arg":
        raise ValueError(f"Unsupported prompt_via value: {prompt_via}")
    updated = list(command)
    try:
        prompt_index = updated.index("-p")
    except ValueError:
        try:
            prompt_index = updated.index("--prompt")
        except ValueError as exc:
            raise ValueError("prompt_via='arg' requires -p or --prompt in command.") from exc
    updated.insert(prompt_index + 1, prompt)
    return updated
```

Add this backend near `AGY_BACKEND`:

```python
OPENCODE_BACKEND = CliBackendSpec(
    name="opencode",
    which_name="opencode",
    default_path=lambda: DEFAULT_OPENCODE_PATH,
    model_default=DEFAULT_OPENCODE_MODEL,
    models=(DEFAULT_OPENCODE_MODEL,),
    auth_env=OPENCODE_AUTH_ENV_NAMES,
    capture="stdout",
    label="opencode CLI",
    build_command=_opencode_build_command,
    summarize=_gemini_summary,
    not_available_message="opencode CLI is not available. Install opencode or add it to PATH before running opencode workers.",
    prompt_via="positional_arg",
)
```

Register it:

```python
CLI_BACKENDS: dict[str, CliBackendSpec] = {
    "codex": CODEX_BACKEND,
    "anthropic": CLAUDE_BACKEND,
    "claude": CLAUDE_BACKEND,
    "gemini": GEMINI_BACKEND,
    "agy": AGY_BACKEND,
    "antigravity": AGY_BACKEND,
    "opencode": OPENCODE_BACKEND,
}
```

Extend `discover_installed_agents`:

```python
elif spec.which_name == "opencode" and report.get("available"):
    live_models = _query_opencode_models(str(report.get("command_path", "")))
    if live_models:
        models = live_models
        models_source = "live"
```

- [ ] **Step 4: Run focused backend tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py -q -k 'opencode or discover_installed_agents'
```

Expected: focused tests pass.

- [ ] **Step 5: Commit backend discovery**

```bash
git add src/goodteam_os/workers.py tests/test_workers.py
git commit -m "feat(workers): register opencode backend"
```

---

### Task 2: Create-Config Workers Contract

**Files:**
- Modify: `src/goodteam_os/web.py`
- Test: `tests/test_web.py`, `tests/test_api.py`

- [ ] **Step 1: Write failing create-config workers persistence test**

Add to `tests/test_web.py` near create config tests:

```python
def test_create_local_project_config_writes_workers_and_role_model_overrides(tmp_path):
    from goodteam_os.projects import load_project_target
    from goodteam_os.web import create_local_project_config

    root = tmp_path / "app"
    root.mkdir()

    result = create_local_project_config(
        workspace=tmp_path,
        project_name="Worker Init",
        project_key="WMI",
        project_root=str(root),
        worker_backend_mode="codex",
        workers={
            "product_requirement": "claude",
            "architect": "claude",
            "ui": "opencode",
            "implementation": "opencode",
        },
        role_model_overrides={
            "product_requirement": "claude:opus",
            "architect": "claude:opus",
            "ui": "opencode:opencode-go/deepseek-v4-pro",
            "implementation": "opencode:opencode-go/deepseek-v4-pro",
        },
    )

    target = load_project_target(Path(result["project_config"]))

    assert target["worker_backend"]["mode"] == "codex"
    assert target["workers"]["product_requirement"] == "claude"
    assert target["workers"]["architect"] == "claude"
    assert target["workers"]["ui"] == "opencode"
    assert target["workers"]["implementation"] == "opencode"
    assert target["role_model_overrides"]["architect"] == "claude:opus"
    assert target["role_model_overrides"]["implementation"] == "opencode:opencode-go/deepseek-v4-pro"
    assert result["workers"]["architect"] == "claude"
```

`tests/test_web.py` already imports `Path` at module scope; do not add a duplicate import.

- [ ] **Step 2: Write failing API create-config payload test**

Add to `tests/test_api.py` near project-config create tests:

```python
def test_api_project_config_create_accepts_workers_map(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()

    handler = capture_post_handler(tmp_path, monkeypatch)(
        "/api/project-config",
        {
            "project_name": "Worker Init",
            "project_key": "WMI",
            "project_root": str(root),
            "worker_backend_mode": "codex",
            "workers": {"architect": "claude", "implementation": "opencode"},
            "role_model_overrides": {
                "architect": "claude:opus",
                "implementation": "opencode:opencode-go/deepseek-v4-pro",
            },
        },
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    config_path = Path(payload["project_config"])
    content = config_path.read_text(encoding="utf-8")

    assert "workers:" in content
    assert "  architect: claude" in content
    assert "  implementation: opencode" in content
    assert "role_model_overrides:" in content
    assert "  architect: claude:opus" in content
    assert "  implementation: opencode:opencode-go/deepseek-v4-pro" in content


def test_api_project_config_update_still_accepts_project_key_with_workers(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    create = capture_post_handler(tmp_path, monkeypatch)(
        "/api/project-config",
        {
            "project_name": "Worker Init",
            "project_key": "WMI",
            "project_root": str(root),
            "worker_backend_mode": "codex",
        },
    )
    assert 200 in create.responses

    update = capture_post_handler(tmp_path, monkeypatch)(
        "/api/project-config",
        {
            "action": "update_worker_backends",
            "project_key": "WMI",
            "worker_backend_mode": "codex",
            "workers": {"implementation": "opencode"},
            "role_models": {"implementation": "opencode:opencode-go/deepseek-v4-pro"},
        },
    )

    assert 200 in update.responses
    payload = json.loads(update.wfile.getvalue().decode("utf-8"))
    assert payload["role_worker_backends"]["implementation"] == "opencode"
    assert payload["role_model_overrides"]["implementation"] == "opencode:opencode-go/deepseek-v4-pro"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py tests/test_api.py -q -k 'create_local_project_config_writes_workers or create_accepts_workers_map'
```

Expected: fails because `create_local_project_config` does not accept/persist `workers`.

- [ ] **Step 4: Implement create-config workers support**

In `src/goodteam_os/web.py`, extend `create_local_project_config` signature:

```python
workers: dict[str, str] | None = None,
```

Place it after `worker_backend_mode`.

After `worker_mode = ...`, validate and normalize:

```python
worker_overrides = {
    _config_mapping_key("Worker role", str(role)): _single_line_config_value("Worker backend", str(backend))
    for role, backend in (workers or {}).items()
    if str(role).strip() and str(backend).strip()
}
_validate_worker_backend_config(mode=worker_mode, workers=worker_overrides, role_models=overrides)
```

Then write the `workers:` block immediately after `worker_backend:`:

```python
lines.extend(["worker_backend:", f"  mode: {_config_scalar('Worker backend mode', worker_mode)}"])
if worker_overrides:
    lines.append("workers:")
    lines.extend(
        f"  {role}: {_config_scalar('Worker backend', backend)}"
        for role, backend in _ordered_worker_overrides(worker_overrides).items()
    )
```

Update the returned payload:

```python
"workers": _ordered_worker_overrides(worker_overrides),
```

In the `/api/project-config` create branch, pass:

```python
workers=dict(request.get("workers", {})),
```

Change `/api/project-config` routing to prefer an explicit action for worker updates, while still accepting the existing worker-panel shape. Replace the current update branch condition with:

```python
elif normalized_path == "/project-config" and (
    request.get("action") == "update_worker_backends"
    or (
        ("project" in request or "project_key" in request)
        and "workers" in request
        and "project_name" not in request
    )
):
```

This prevents create-config requests that include `workers` from being misrouted, while preserving update callers that send `project_key` rather than `project`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py tests/test_api.py -q -k 'create_local_project_config_writes_workers or create_accepts_workers_map or project_config_updates_worker_backends'
```

Expected: focused tests pass, including existing worker-backend update tests.

- [ ] **Step 6: Commit create-config contract**

```bash
git add src/goodteam_os/web.py tests/test_web.py tests/test_api.py
git commit -m "feat(web): persist init worker routing"
```

---

### Task 3: Frontend Bucket Expansion Helpers

**Files:**
- Modify: `frontend/src/constants/workers.js`
- Modify: `frontend/src/utils/roleModelOverrides.js`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing frontend contract test for helpers**

Add to `tests/test_web.py` near role override helper contracts:

```python
def test_frontend_has_init_bucket_worker_model_helpers():
    root = Path(__file__).resolve().parents[1]
    constants = (root / "frontend" / "src" / "constants" / "workers.js").read_text(encoding="utf-8")
    helpers = (root / "frontend" / "src" / "utils" / "roleModelOverrides.js").read_text(encoding="utf-8")

    assert "INIT_WORKER_ROUTING_BUCKET_KEYS" in constants
    assert '"gpt-5.4"' in constants
    assert '"gpt-5.3-codex"' not in constants
    assert "export function expandInitBucketWorkerOverrides" in helpers
    assert "export function expandInitBucketModelOverrides" in helpers
    assert "selection.model === '__custom__'" in helpers
    assert "backend:model" not in helpers
    assert "role_model_overrides" not in helpers
```

Also add a Node smoke command to the plan after helper implementation, so the helper behavior is checked instead of only source strings.

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'init_bucket_worker_model_helpers'
```

Expected: fails because helper exports do not exist and fallback constants are stale.

- [ ] **Step 3: Update constants**

In `frontend/src/constants/workers.js`, update Codex fallback:

```js
codex: {
  models: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.5"],
  model_default: "gpt-5.4"
},
```

Add opencode fallback:

```js
opencode: {
  models: ["opencode-go/deepseek-v4-pro"],
  model_default: "opencode-go/deepseek-v4-pro"
}
```

Add init bucket keys:

```js
export const INIT_WORKER_ROUTING_BUCKET_KEYS = ['product', 'implementation', 'quality'];
```

- [ ] **Step 4: Add pure expansion helpers**

Append to `frontend/src/utils/roleModelOverrides.js`:

```js
export function expandInitBucketWorkerOverrides(bucketSelections, workerRoutingBuckets) {
  const workers = {};
  workerRoutingBuckets.forEach((bucket) => {
    const selection = bucketSelections[bucket.key] || {};
    const backend = String(selection.backend || '').trim();
    if (!backend) return;
    bucket.roles.forEach((role) => {
      workers[role] = backend;
    });
  });
  return workers;
}

export function expandInitBucketModelOverrides(bucketSelections, workerRoutingBuckets, fallbackBackendByBucket = {}) {
  const roleModels = {};
  workerRoutingBuckets.forEach((bucket) => {
    const selection = bucketSelections[bucket.key] || {};
    const selectedModel = selection.model === '__custom__' ? selection.customModel : selection.model;
    const model = String(selectedModel || '').trim();
    if (!model) return;
    const backend = String(selection.backend || fallbackBackendByBucket[bucket.key] || '').trim();
    const value = backend && !model.includes(':') ? `${backend}:${model}` : model;
    bucket.roles.forEach((role) => {
      roleModels[role] = value;
    });
  });
  return roleModels;
}

export function formatRoleModelOverridesMap(roleModelOverrides) {
  return Object.entries(roleModelOverrides)
    .map(([role, model]) => `${role}=${model}`)
    .join('\n');
}
```

- [ ] **Step 5: Run helper contract test**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'init_bucket_worker_model_helpers or role_model_override_helpers'
```

Expected: focused tests pass.

Then run this helper behavior smoke:

```bash
node --input-type=module - <<'JS'
import {
  expandInitBucketModelOverrides,
  expandInitBucketWorkerOverrides,
} from './frontend/src/utils/roleModelOverrides.js';

const buckets = [{ key: 'implementation', roles: ['ui', 'implementation'] }];
const selection = {
  implementation: {
    backend: 'opencode',
    model: '__custom__',
    customModel: 'opencode-go/qwen3.7-max',
  },
};

const workers = expandInitBucketWorkerOverrides(selection, buckets);
const models = expandInitBucketModelOverrides(selection, buckets, {});

if (workers.ui !== 'opencode' || workers.implementation !== 'opencode') {
  throw new Error(`unexpected workers ${JSON.stringify(workers)}`);
}
if (models.ui !== 'opencode:opencode-go/qwen3.7-max') {
  throw new Error(`unexpected models ${JSON.stringify(models)}`);
}
JS
```

Expected: exits 0.

- [ ] **Step 6: Commit helper work**

```bash
git add frontend/src/constants/workers.js frontend/src/utils/roleModelOverrides.js tests/test_web.py
git commit -m "feat(ui): add init bucket routing helpers"
```

---

### Task 4: Frontend Init Bucket State and Modal UI

**Files:**
- Modify: `frontend/src/hooks/useNewProjectForm.js`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/NewProjectModalContainer.jsx`
- Modify: `frontend/src/components/NewProjectModal.jsx`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing modal contract test**

Add to `tests/test_web.py` near NewProjectModal contracts:

```python
def test_new_project_modal_renders_init_backend_model_buckets():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = (root / "frontend" / "src" / "hooks" / "useNewProjectForm.js").read_text(encoding="utf-8")
    modal = (root / "frontend" / "src" / "components" / "NewProjectModal.jsx").read_text(encoding="utf-8")
    container = (root / "frontend" / "src" / "components" / "NewProjectModalContainer.jsx").read_text(encoding="utf-8")

    assert "initWorkerBucketSelections" in hook
    assert "setInitWorkerBucketSelections({})" in hook
    assert "expandInitBucketWorkerOverrides" in app
    assert "expandInitBucketModelOverrides" in app
    assert "fetchDiscoveredAgents" in app
    assert "initWorkerRoutingBuckets" in container
    assert "initWorkerRoutingBuckets" in modal
    assert "newProject.workerBuckets.title" in modal
    assert "newProject.advanced.roleModelOverrides" in modal
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'init_backend_model_buckets'
```

Expected: fails because modal bucket state/UI is absent.

- [ ] **Step 3: Add form state**

In `frontend/src/hooks/useNewProjectForm.js`, add state:

```js
const [initWorkerBucketSelections, setInitWorkerBucketSelections] = useState({});
```

Reset in `openNewProjectModal`:

```js
setInitWorkerBucketSelections({});
```

Return both:

```js
initWorkerBucketSelections,
setInitWorkerBucketSelections,
```

- [ ] **Step 4: Wire App imports and computed bucket payloads**

In `frontend/src/App.jsx`, import:

```js
import { FALLBACK_AGENT_MODES, INIT_WORKER_ROUTING_BUCKET_KEYS, WORKER_ROUTING_BUCKETS } from './constants/workers';
```

Add exactly this import line once near the existing imports.

Import helpers:

```js
import {
  expandInitBucketModelOverrides,
  expandInitBucketWorkerOverrides,
  formatRoleModelOverridesMap,
  formatRoleModelOverridesText,
  parseRoleModelOverrideLines,
  parseRoleModelOverridesMap,
} from './utils/roleModelOverrides';
```

Destructure form state:

```js
initWorkerBucketSelections,
setInitWorkerBucketSelections,
```

Destructure `fetchDiscoveredAgents` from `workerBackendSettings`.

Add these computed values before handlers:

```js
const initWorkerRoutingBuckets = WORKER_ROUTING_BUCKETS.filter((bucket) => (
  INIT_WORKER_ROUTING_BUCKET_KEYS.includes(bucket.key)
));

const initBucketWorkers = expandInitBucketWorkerOverrides(
  initWorkerBucketSelections,
  initWorkerRoutingBuckets
);

const initBucketFallbackBackends = Object.fromEntries(
  initWorkerRoutingBuckets.map((bucket) => [bucket.key, workerBackendMode || panelGlobalMode || 'codex'])
);

const initBucketRoleModels = expandInitBucketModelOverrides(
  initWorkerBucketSelections,
  initWorkerRoutingBuckets,
  initBucketFallbackBackends
);
```

- [ ] **Step 5: Send bucket payloads in create config**

In `handleCreateConfig`, merge text and bucket role models:

```js
const parsedOverrides = {
  ...parseRoleModelOverridesMap(roleModelOverrides),
  ...initBucketRoleModels,
};
```

Add `workers` to the body:

```js
workers: initBucketWorkers,
```

- [ ] **Step 6: Ensure Start syncs config before workflow when needed**

In `handleStartWorkflow`, before the `/api/start` call, compute:

```js
const hasInitBucketOverrides = Object.keys(initBucketWorkers).length > 0 || Object.keys(initBucketRoleModels).length > 0;
const mergedRoleModelOverrides = {
  ...parseRoleModelOverridesMap(roleModelOverrides),
  ...initBucketRoleModels,
};
const bucketOverrideLines = parseRoleModelOverrideLines(formatRoleModelOverridesMap(initBucketRoleModels));
const overrides = [
  ...parseRoleModelOverrideLines(roleModelOverrides),
  ...bucketOverrideLines,
];
let workflowProjectConfig = projectConfig;
if (hasInitBucketOverrides && workflowProjectConfig) {
  const updateResponse = await fetch('/api/project-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action: 'update_worker_backends',
      project: startProjectKey || projectKey,
      worker_backend_mode: workerBackendMode,
      workers: initBucketWorkers,
      role_models: mergedRoleModelOverrides,
    }),
  });
  const updateResult = await updateResponse.json();
  if (!updateResponse.ok) throw new Error(updateResult.error || 'Failed to update worker config');
}
if (hasInitBucketOverrides && !workflowProjectConfig) {
  const customDeliveryPayload = buildCustomDeliveryPayload(deliveryMode, deliveryStandards);
  const configResponse = await fetch('/api/project-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_name: projectName,
      project_key: startProjectKey || projectKey,
      project_root: projectRoot,
      task_sync: taskSync ? [taskSync] : [],
      plane_api_host_url: planeApiHost,
      plane_workspace_slug: planeWorkspaceSlug,
      plane_project_id: planeProjectId,
      plane_api_key_env: planeApiKeyEnv,
      plane_mcp_server: planeMcpServer,
      worker_backend_mode: workerBackendMode,
      workers: initBucketWorkers,
      role_model_overrides: mergedRoleModelOverrides,
      model_profile: modelProfile,
      delivery_mode: deliveryMode,
      ...customDeliveryPayload,
      ...(isNewProjectMode ? {
        init_git: initGit,
        create_remote: createRemote,
        remote_private: remotePrivate,
      } : {}),
    }),
  });
  const configResult = await configResponse.json();
  if (!configResponse.ok) throw new Error(configResult.error || 'Failed to create config');
  workflowProjectConfig = configResult.project_config;
  setProjectConfig(configResult.project_config);
}
```

Then pass `project_config: workflowProjectConfig`.

Keep one `customDeliveryPayload` declaration in the handler; do not redeclare it twice in the same scope.

- [ ] **Step 7: Fetch agents when modal opens**

Add an effect in `App.jsx`:

```js
useEffect(() => {
  if (newProjectModalOpen && discoveredAgents === null && !loadingDiscoveredAgents) {
    fetchDiscoveredAgents();
  }
}, [discoveredAgents, fetchDiscoveredAgents, loadingDiscoveredAgents, newProjectModalOpen]);
```

- [ ] **Step 8: Pass props through container**

Add these props to `NewProjectModalContainer` signature and `<NewProjectModal />`:

```js
discoveredAgents
fallbackAgentModes
initWorkerBucketSelections
initWorkerRoutingBuckets
loadingDiscoveredAgents
safeDiscoveredAgents
setInitWorkerBucketSelections
```

- [ ] **Step 9: Render bucket UI**

In `frontend/src/components/NewProjectModal.jsx`, add props:

```js
discoveredAgents = null,
fallbackAgentModes = {},
initWorkerBucketSelections = {},
initWorkerRoutingBuckets = [],
loadingDiscoveredAgents = false,
safeDiscoveredAgents = [],
setInitWorkerBucketSelections,
```

Add helpers inside the component:

```js
const availableInitAgents = (safeDiscoveredAgents.length > 0 ? safeDiscoveredAgents : [])
  .filter((agent) => agent.available);

const initAgentOptions = availableInitAgents.length > 0
  ? availableInitAgents
  : Object.entries(fallbackAgentModes).map(([backend, config]) => ({
      backend,
      available: true,
      ready: true,
      models: config.models,
      model_default: config.model_default,
    }));

const setInitBucketSelection = (bucketKey, patch) => {
  setInitWorkerBucketSelections((prev) => ({
    ...prev,
    [bucketKey]: {
      ...(prev[bucketKey] || {}),
      ...patch,
    },
  }));
};

const agentByBackend = Object.fromEntries(initAgentOptions.map((agent) => [agent.backend, agent]));
```

Replace the textarea block at the bottom of Advanced config with a bucket section earlier in the modal body, before the advanced drawer:

```jsx
<div className="col-span-2 grid grid-cols-1 gap-3 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
  <div className="flex items-center justify-between gap-3">
    <div className="font-bold text-2xs text-text-muted uppercase tracking-wider">
      {t('newProject.workerBuckets.title')}
    </div>
    {loadingDiscoveredAgents && (
      <span className="text-2xs text-text-secondary font-semibold">
        {t('newProject.workerBuckets.loading')}
      </span>
    )}
  </div>
  {initWorkerRoutingBuckets.map((bucket) => {
    const selection = initWorkerBucketSelections[bucket.key] || {};
    const effectiveBackend = selection.backend || workerBackendMode || 'codex';
    const agent = agentByBackend[effectiveBackend] || fallbackAgentModes[effectiveBackend] || {};
    const models = agent.models || ['auto'];
    const defaultModel = agent.model_default || models[0] || 'auto';
    const modelValue = selection.model || '';
    return (
      <div key={bucket.key} className="grid grid-cols-1 md:grid-cols-[1fr_1fr] gap-2">
        <label className="block">
          <span className="block text-2xs text-text-secondary mb-1 uppercase">{bucket.label}</span>
          <select
            value={selection.backend || ''}
            onChange={(event) => setInitBucketSelection(bucket.key, { backend: event.target.value, model: '' })}
            className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
          >
            <option value="">Inherit ({workerBackendMode || 'codex'})</option>
            {initAgentOptions.map((agentOption) => (
              <option key={agentOption.backend} value={agentOption.backend}>
                {agentOption.backend}{agentOption.ready === false ? ' - not ready' : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="block text-2xs text-text-secondary mb-1 uppercase">Model</span>
          <select
            value={modelValue}
            onChange={(event) => setInitBucketSelection(bucket.key, { model: event.target.value })}
            className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
          >
            <option value="">Inherit ({defaultModel})</option>
            {models.map((model) => (
              <option key={model} value={model}>{model}</option>
            ))}
          </select>
        </label>
      </div>
    );
  })}
</div>
```

Add custom model entry in the same bucket UI. In the model select, add:

```jsx
<option value="__custom__">Custom...</option>
```

Then render a compact input below the model select when `modelValue === '__custom__'`:

```jsx
{modelValue === '__custom__' && (
  <input
    type="text"
    value={selection.customModel || ''}
    onChange={(event) => setInitBucketSelection(bucket.key, { customModel: event.target.value })}
    placeholder="provider/model"
    className="mt-2 w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs font-mono focus:outline-none focus:border-accent"
  />
)}
```

Do not normalize custom model text in `App.jsx`; `expandInitBucketModelOverrides`
owns the `__custom__` handling so create and start payloads stay consistent.

Keep the existing Advanced textarea block using `newProject.advanced.roleModelOverrides`. It remains an escape hatch for unusual roles/models and for backward compatibility; the new bucket UI is the default path above the Advanced drawer.

- [ ] **Step 10: Add locale keys**

In `frontend/src/i18n/locales/en.js`:

```js
'newProject.workerBuckets.title': 'Worker defaults',
'newProject.workerBuckets.loading': 'Detecting agents...',
```

In `frontend/src/i18n/locales/zh-CN.js`:

```js
'newProject.workerBuckets.title': 'Worker 默认配置',
'newProject.workerBuckets.loading': '正在检测 agents...',
```

- [ ] **Step 11: Run focused frontend contract tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'init_backend_model_buckets or i18n or role_model_override_helpers'
```

Expected: focused tests pass.

Run the helper behavior smoke again:

```bash
node --input-type=module - <<'JS'
import { expandInitBucketModelOverrides } from './frontend/src/utils/roleModelOverrides.js';
const buckets = [{ key: 'implementation', roles: ['implementation'] }];
const models = expandInitBucketModelOverrides(
  { implementation: { backend: 'opencode', model: '__custom__', customModel: 'opencode-go/qwen3.7-max' } },
  buckets,
  {}
);
if (models.implementation !== 'opencode:opencode-go/qwen3.7-max') {
  throw new Error(JSON.stringify(models));
}
JS
```

Expected: exits 0.

- [ ] **Step 12: Commit modal UI**

```bash
git add frontend/src/App.jsx frontend/src/hooks/useNewProjectForm.js frontend/src/components/NewProjectModalContainer.jsx frontend/src/components/NewProjectModal.jsx frontend/src/i18n/locales/en.js frontend/src/i18n/locales/zh-CN.js tests/test_web.py
git commit -m "feat(ui): configure worker buckets at init"
```

---

### Task 5: End-to-End Verification and Hardening

**Files:**
- Inspect: all files changed by Tasks 1-4.
- Modify: the exact file reported by a failing verification command.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py tests/test_web.py tests/test_api.py -q -k 'opencode or discover_installed_agents or project_config or init_bucket or NewProjectModal or role_model'
```

Expected: all focused tests pass.

- [ ] **Step 2: Run ruff**

Run:

```bash
.venv/bin/ruff check src/goodteam_os/ tests/
```

Expected: no new ruff errors.

- [ ] **Step 3: Run frontend lint and build**

Run:

```bash
cd frontend && npm run lint && npm run build
```

Expected: lint and build pass.

- [ ] **Step 4: Run backend API smoke for create config**

Start the dev server with the repo helper, then create a temp root and POST a create-config payload with `workers` and `role_model_overrides`.

Run:

```bash
scripts/dev-up.sh
```

In another terminal, run:

```bash
SMOKE_ROOT="$(mktemp -d)"
curl -sS http://127.0.0.1:8788/api/project-config \
  -H 'Content-Type: application/json' \
  -d "{\"project_name\":\"Worker Init Smoke\",\"project_key\":\"WMI\",\"project_root\":\"$SMOKE_ROOT\",\"worker_backend_mode\":\"codex\",\"workers\":{\"architect\":\"claude\",\"implementation\":\"opencode\"},\"role_model_overrides\":{\"architect\":\"claude:opus\",\"implementation\":\"opencode:opencode-go/deepseek-v4-pro\"}}" \
  | python -m json.tool
```

Expected config contains:

```yaml
worker_backend:
  mode: codex
workers:
  architect: claude
  implementation: opencode
role_model_overrides:
  architect: claude:opus
  implementation: opencode:opencode-go/deepseek-v4-pro
```

- [ ] **Step 5: Run live UI smoke**

Run the dashboard server:

```bash
scripts/dev-up.sh
```

Open `http://localhost:8788/?project=DEMO` in a browser and complete this checklist:

1. Open the dashboard.
2. Open New Project modal.
3. Confirm Worker defaults section appears.
4. Confirm three bucket rows appear.
5. Select a backend and model for Implementation.
6. Create config.
7. Inspect generated config.

Expected: selected bucket expands to role entries under `workers:` and `role_model_overrides:`.

- [ ] **Step 6: Run final full test suite**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```

Expected: full suite passes.

- [ ] **Step 7: Commit verification fixes**

Review the working tree and stage the exact modified files from the allowed list:

```bash
git status --short
git add src/goodteam_os/workers.py src/goodteam_os/web.py frontend/src/constants/workers.js frontend/src/utils/roleModelOverrides.js frontend/src/hooks/useNewProjectForm.js frontend/src/App.jsx frontend/src/components/NewProjectModalContainer.jsx frontend/src/components/NewProjectModal.jsx frontend/src/i18n/locales/en.js frontend/src/i18n/locales/zh-CN.js tests/test_workers.py tests/test_web.py tests/test_api.py
git commit -m "fix: harden init worker model configuration"
```

If no changes were required, do not create an empty commit.

---

## Self-Review

- Spec coverage: opencode registration, live discovery, init backend+model controls, visible inheritance defaults, clean config semantics, create/start flow, and tests are all covered.
- Main risk: the `Start Workflow` handler must not create duplicate configs unexpectedly. The plan updates an existing config when one is selected and creates a config only when bucket overrides exist and no config path is currently selected.
- No transient `/api/start` backend override path is introduced.
- The plan intentionally avoids broad UI refactors outside the modal/container/form state path.
