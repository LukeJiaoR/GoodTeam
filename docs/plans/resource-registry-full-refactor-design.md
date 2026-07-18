# Design — Resource Registry full refactor

Status: **draft 2026-06-17**. Triggered by the `task_ledger` dashboard bug: a project runtime JSON
file was registered in `state.artifacts`, rendered by the Artifacts tab, and sent through the
markdown artifact resolver, which rejected it because it was outside `artifacts/runs/<run_id>/`.

Implementation note (2026-07-18): the typed registry is active. The generic
`register_runtime_resource` and `register_external_reference` functions remain intentional public
extension seams for new runtime kinds and URL-only integrations; product-specific resources should
still prefer concrete helpers such as `register_task_ledger` when one exists. Contract tests cover
their scope, capability, and legacy-projection behavior.

## Problem
GoodTeam currently uses `AgileDevState.artifacts: dict[str, str]` as a catch-all registry for paths.
That dictionary now carries at least two different concepts:

- human-facing run artifacts, such as `prd`, `rfc`, `fix_plan`, `qa_report`, `staging_report`, and
  `release_notes`
- system/runtime files, such as `task_ledger`, which lives under the target project's
  `.goodteam/tasks/tasks.json` and is consumed by the Agile Board

The bug was not that the task ledger path was wrong. The bug was that a runtime data source and a
reviewable markdown artifact shared the same untyped container. Downstream code then guessed
capability from key names, file suffixes, and path location. That creates recurring coupling:
dashboard tabs, approval flows, Telegram views, action logs, task sync, and artifact editing all
have to remember which keys are special.

## Goal
Replace the untyped artifact map as the source of truth with a typed resource registry. Every
dashboard-visible or workflow-visible object must declare what it is, where it lives, and which
operations are allowed. UI and API code should render actions from capabilities, not from ad hoc
key checks.

The end state:

```python
state.resources: dict[str, ResourceDescriptor]
```

`state.artifacts` may remain temporarily as a compatibility projection for old tests and old state
files, but new production code must register and consume resources through typed helpers.

## Resource model
Create `src/goodteam_os/resources.py` with focused dataclasses/enums:

```python
class ResourceKind(StrEnum):
    MARKDOWN_ARTIFACT = "markdown_artifact"
    PROPOSED_MARKDOWN_ARTIFACT = "proposed_markdown_artifact"
    TASK_LEDGER = "task_ledger"
    WORKER_JOB_STORE = "worker_job_store"
    MEMORY = "memory"
    PROVENANCE = "provenance"
    SMOKE_REPORT = "smoke_report"
    EXTERNAL_REFERENCE = "external_reference"


class ResourceScope(StrEnum):
    RUN = "run"
    PROJECT_RUNTIME = "project_runtime"
    WORKSPACE_STATE = "workspace_state"
    EXTERNAL = "external"


class ResourceCapability(StrEnum):
    READ = "read"
    PREVIEW = "preview"
    EDIT = "edit"
    REVIEW = "review"
    APPROVE = "approve"
    DIFF = "diff"
    DOWNLOAD = "download"
    BOARD_SOURCE = "board_source"
    PROVENANCE_SOURCE = "provenance_source"
    WORKER_SOURCE = "worker_source"


@dataclass
class ResourceDescriptor:
    key: str
    kind: ResourceKind
    scope: ResourceScope
    path: str = ""
    url: str = ""
    mime: str = ""
    title: str = ""
    producer: str = ""
    consumer: str = ""
    capabilities: list[ResourceCapability] = field(default_factory=list)
    legacy_artifact: bool = False
```

Rules:

- A resource may have a local `path`, external `url`, or both.
- `MARKDOWN_ARTIFACT` with `scope=RUN` is the only default editable dashboard artifact.
- `TASK_LEDGER` is `scope=PROJECT_RUNTIME`, `mime=application/json`, and has
  `READ + BOARD_SOURCE`, not `EDIT`.
- Approval workbench consumes `REVIEW`/`APPROVE`, not all resources.
- Artifact editor consumes `EDIT`, not all markdown-looking paths.
- Agile Board consumes `BOARD_SOURCE`, not the literal key `task_ledger`.

## Registration API
Introduce helper functions in `resources.py` and stop writing `state.artifacts[...]` directly in
production code:

```python
def register_markdown_artifact(state, key, path, *, title="", producer="", reviewable=True) -> ResourceDescriptor
def register_proposed_markdown_artifact(state, key, path, *, base_key) -> ResourceDescriptor
def register_task_ledger(state, path) -> ResourceDescriptor
def register_runtime_resource(state, key, path, *, kind, mime, capabilities, title="", consumer="") -> ResourceDescriptor
def register_external_reference(state, key, url, *, title, capabilities) -> ResourceDescriptor
def resource_path(state, key, *, capability=None) -> Path
def resource_descriptors(state) -> dict[str, ResourceDescriptor]
def legacy_artifacts_view(state) -> dict[str, str]
```

`register_*` helpers should update `state.resources`. During migration they may also update
`state.artifacts` for compatibility, but direct production writes to `state.artifacts` become a
tested anti-pattern.

Initial mappings:

| Key | Kind | Scope | Capabilities |
| --- | --- | --- | --- |
| `takeover` | `markdown_artifact` | `run` | `read, preview, edit, review, approve, download` |
| `prd` | `markdown_artifact` | `run` | `read, preview, edit, review, approve, diff, download` |
| `rfc` | `markdown_artifact` | `run` | `read, preview, edit, review, approve, diff, download` |
| `fix_plan` | `markdown_artifact` | `run` | `read, preview, edit, review, approve, diff, download` |
| `implementation_handoff` | `markdown_artifact` | `run` | `read, preview, download` |
| `qa_report` | `markdown_artifact` | `run` | `read, preview, review, download` |
| `staging_report` | `markdown_artifact` | `run` | `read, preview, review, approve, download` |
| `release_notes` | `markdown_artifact` | `run` | `read, preview, review, approve, download` |
| `retro_memory` | `memory` | `run` or `workspace_state` | `read, preview, download` |
| `*_proposed` | `proposed_markdown_artifact` | `run` | `read, preview, diff` |
| `task_ledger` | `task_ledger` | `project_runtime` | `read, board_source` |

## State schema and migration
Update `AgileDevState`:

```python
resources: dict[str, ResourceDescriptor] = field(default_factory=dict)
artifacts: dict[str, str] = field(default_factory=dict)  # legacy projection
```

Read path:

1. `StateStore._read` loads existing JSON.
2. If `resources` is absent, derive resources from legacy `artifacts`.
3. If both exist, `resources` wins. `artifacts` is regenerated as a compatibility projection before
   returning the dataclass.
4. Persist both fields during the transition so older tooling can still inspect state files.

Legacy derivation rules:

- Keys in the known markdown artifact set become `MARKDOWN_ARTIFACT`.
- Keys ending in `_proposed` become `PROPOSED_MARKDOWN_ARTIFACT`.
- `task_ledger` becomes `TASK_LEDGER`, regardless of path.
- Unknown `.md` or `.markdown` paths inside `artifacts/runs/<run_id>/` become non-reviewable
  `MARKDOWN_ARTIFACT` with `read + preview + download`.
- Unknown paths outside the run root become `runtime_resource`-style descriptors with
  `read + download` only, never `edit` or `review`.

Write path:

- Phase 1 still writes `artifacts` projection.
- Phase 2 changes production call sites to `register_*`.
- Phase 3 adds a regression test that fails on production `state.artifacts[...] =` writes outside
  `resources.py` and migration tests.
- Phase 4 can remove most production reads from `state.artifacts`, leaving only compatibility
  exports and tests.

## Backend API boundaries
Keep existing endpoints during migration, but move semantics to resources.

New endpoints:

- `GET /api/resources?project=<key>` → `{ resources: [ResourceDescriptor], legacy_artifacts: {...} }`
- `GET /api/resources/<key>?project=<key>` → descriptor
- `GET /api/artifacts/<key>/content?project=<key>` → content only when resource has `READ` and is
  a local text artifact
- `POST /api/artifacts/<key>/content` → save only when resource has `EDIT`
- `GET /api/runtime-resources?project=<key>` → runtime/system resources for System tab
- `GET /api/agile-board?project=<key>` → unchanged payload, but resolves its source by
  `BOARD_SOURCE`

Compatibility endpoints:

- `GET /api/artifact` delegates to the new content resolver and rejects resources without `EDIT` or
  text read capability with a typed error.
- `POST /api/artifact` delegates to save and requires `EDIT`.
- `GET /api/state` includes `resources` alongside `state.artifacts` until frontend migration is done.

Typed resolver behavior:

- `resolve_resource(workspace, state, key, required_capability)` first checks the descriptor.
- Path containment checks depend on `scope`:
  - `RUN` resources must live under `workspace/artifacts/runs/<run_id>/`.
  - `PROJECT_RUNTIME` resources may live under the configured project root.
  - `WORKSPACE_STATE` resources must live under GoodTeam workspace `state/`.
  - `EXTERNAL` resources do not use local path checks.
- The error message should name the missing capability or invalid scope, not a generic run path
  failure.

## Frontend boundaries
The React app should stop using `Object.entries(state.artifacts)` as a source of actions.

Tab responsibilities:

- **Artifacts tab:** show resources with `PREVIEW`, `DOWNLOAD`, `EDIT`, or `REVIEW`, excluding
  runtime-only resources. Buttons come from capabilities.
- **Approvals tab:** uses approval gate → artifact key mapping, then requires resource capability
  `REVIEW`/`APPROVE`.
- **Agile Board tab:** consumes `/api/agile-board`; it may display the backing resource descriptor
  for diagnostics, but never edits it as markdown.
- **System tab:** shows runtime resources such as task ledger, worker stores, memory, provenance,
  and smoke reports. It can offer "reveal path" or "download" only when the descriptor allows it.
- **Overview tab:** can show important resource paths, but labels them as runtime state vs run
  artifacts.

Front-end state shape should add a derived selector layer:

```js
editableArtifacts = resources.filter(r => r.capabilities.includes('edit'))
reviewableArtifacts = resources.filter(r => r.capabilities.includes('review'))
runtimeResources = resources.filter(r => ['project_runtime', 'workspace_state'].includes(r.scope))
boardSources = resources.filter(r => r.capabilities.includes('board_source'))
```

No tab should infer editability from suffix or path.

## Migration tasks
Recommended implementation order:

1. Add `resources.py` dataclasses, enum serialization helpers, registration helpers, and legacy
   derivation tests.
2. Add `resources` to `AgileDevState` and `StateStore._read/_write` migration behavior.
3. Convert graph registration points from direct `state.artifacts[...] = str(path)` to
   `register_markdown_artifact`, `register_proposed_markdown_artifact`, and `register_task_ledger`.
4. Convert web artifact resolvers to `resolve_resource(..., required_capability=EDIT/READ/REVIEW)`.
5. Add `/api/resources` and include `resources` in `/api/state`.
6. Convert Agile Board lookup from `_task_ledger_from_state(state.artifacts)` to
   `BOARD_SOURCE` resource lookup.
7. Convert frontend Artifacts/System/Overview tabs to resource selectors.
8. Convert Approval editor hooks to new content endpoints, keeping `/api/artifact` as a fallback
   only until the UI is fully migrated.
9. Convert Telegram `/gt view` and approval notification artifact reads to `REVIEW` resource lookup.
10. Add anti-regression tests that scan production source for direct `state.artifacts[` writes
    outside `resources.py`, state migration, and narrowly allowed compatibility code.
11. Remove special-case `task_ledger` checks from web/UI once resource capabilities are authoritative.

## Testing strategy
Backend:

- Unit tests for descriptor serialization/deserialization.
- Unit tests for legacy artifact map → resources migration.
- Unit tests for path containment by scope.
- Regression test: `task_ledger` has `BOARD_SOURCE`, lacks `EDIT`, and cannot be saved through the
  markdown artifact API.
- Regression test: markdown artifacts inside run root keep edit/review behavior.
- Contract test: `/api/resources` returns typed descriptors and `/api/state` includes resources.
- Source scan test: no new direct production writes to `state.artifacts[...]`.

Frontend:

- Contract tests that Artifacts tab uses resources/capabilities, not `state.artifacts`.
- Contract tests that System/Overview can display task ledger as runtime resource.
- Build/lint.

Integration smoke:

- Start or load a project with a task ledger.
- Verify Agile Board still renders tasks.
- Verify Artifacts tab shows PRD/RFC/fix plan but not task ledger as editable markdown.
- Verify approval review/edit still works for PRD/RFC.
- Verify Telegram `/gt view` still reads the pending review artifact.

## Rollout and compatibility
This is a full refactor, but rollout should remain reversible:

- Keep `artifacts` in state JSON for one release as a compatibility projection.
- Keep `/api/artifact` as an adapter until the React app and Telegram command paths are migrated.
- Make new registry functions authoritative for new writes.
- Add metrics/logging around legacy derivation so old state files are visible during dogfooding.
- After dashboard and Telegram no longer consume `state.artifacts`, open a follow-up cleanup to
  remove the projection or demote it to export-only.

## Out of scope
- Rebuilding the task system itself.
- Changing Agile Board task schema.
- Changing artifact file storage layout under `artifacts/runs/<run_id>/`.
- Changing approval gate semantics.
- Building a generic file browser.
- Making runtime resources editable from the dashboard.

## Risks
- This touches core state shape and many tests. The plan must land in small commits with focused
  red/green tests.
- Existing project state files may have unknown artifact keys. Legacy derivation must be
  conservative: unknown resources should be readable, not editable.
- API churn could break the React app if migration is not staged. Keep compatibility endpoints until
  the frontend is fully on resources.
- Direct `state.artifacts` writes are easy to reintroduce. The source scan test is part of the
  design, not cleanup polish.
