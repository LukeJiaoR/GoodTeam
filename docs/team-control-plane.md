# Team Control Plane: Roles, Workers, and the Decision Ledger

Issue #402 separates **Role capabilities** from **Worker instances** with an
auditable lifecycle. The product principle:

> Role defines what can be done; Worker defines who actually does it.
> Chat for decisions. UI for current truth. Ledger for history.

## Domain model

```text
Role Catalog (code-defined capability contract)
    ↓
TeamRole (per-project membership state: enabled | disabled)
    ↓
Worker (first-class member: identity + lifecycle + runtime)
    ↓
Worker Runtime (provider, model, reasoning depth, additional prompt)
    ↓
Execution (dispatched jobs bind the executing worker)
```

Three different kinds of truth are stored separately:

1. **Current State** — `state/projects/<KEY>/team-state.json`.
2. **Decision/Event History** — `state/projects/<KEY>/team-events.jsonl`
   (append-only ledger).
3. **Execution Snapshot** — bound per durable run in the run state
   (`team_snapshot`) and in every worker job record (`worker_id`,
   `worker_name`, adapter, model, reasoning depth).

### Role Definition vs TeamRole vs Worker

- A **Role Definition** is a GoodTeam-defined capability contract
  (`src/goodteam_os/team_control_plane.py`, `ROLE_CATALOG`). It owns
  semantics, never runtime configuration or worker counts. The Task
  Assistant (`orchestrator`) is intentionally **not** a TeamRole: it is the
  single logical primary owner and is always available.
- A **TeamRole** is the project-level `enabled`/`disabled` state of a Role
  Definition. Role existence is a system fact; role membership is team state.
- A **Worker** is a real team member: `id`, `name`, `role`, `lifecycle`,
  `runtime`, and audit timestamps. Multiple workers may carry the same role
  with different runtimes.

### Worker lifecycle (independent of runtime state)

| Lifecycle | Current member | Team UI | Dispatchable | Record |
| --- | --- | --- | --- | --- |
| `active` | yes | yes | yes, subject to runtime eligibility | retained |
| `inactive` | yes | yes | no | retained |
| `released` | no | ledger/archive | no | retained |
| `deleted` | no | hidden (tombstone) | no | retained, never physically erased |

Runtime/execution availability is a separate dimension
(`worker_runtime_eligible`: known provider whose backend doctor reports
ready). `lifecycle = active` + `runtime unavailable` is a legal combination.

### Derived capability states

`build_team_capability_view` normalizes each TeamRole into exactly one
operational state:

- `disabled` — TeamRole disabled; the team does not have the capability.
- `unstaffed` — enabled with no rostered Worker (legal state).
- `degraded` — enabled and rostered, but no Worker is currently executable.
- `ready` — enabled with at least one active, runtime-eligible Worker.

The Manager receives this normalized view in every planning turn; it never
infers capability from raw fields.

## Control plane commands

Every Team mutation from every surface (Team UI, Manager conversation, API
adapter, migration) flows through `execute_team_commands`
(`src/goodteam_os/team_control_plane.py`):

```text
enable_role / disable_role
initialize_worker / activate_worker / inactivate_worker /
release_worker / delete_worker / restore_worker
change_worker_runtime / change_worker_role / rename_worker
change_manager_runtime
```

Authorization contract:

> Explicit user intent may mutate Team directly. Agent-initiated Team
> mutation requires approval.

An explicit user utterance (“加个 Researcher”, “把 Research 关掉”) authorizes
the corresponding commands without an extra confirmation. Manager-proposed
changes are stored as `pending_team_decision` in the Task Assistant session
and execute only after the user confirms. A preference scoped to the current
task (“这次不要做 Research”) is never persisted as a Team mutation; it is
traceable as a `team_preference_recorded` timeline event.

## Decision/Event ledger

Every execution appends immutable events to `team-events.jsonl`. Events
carry: event id, `decision_id`, project, timestamp, actor (`user`,
`manager`, `system`, `migration`), source surface
(`manager_conversation`, `team_ui`, `api`, `migration`), source message /
proposal reference, reason, before/after payload, and a `cause`
(`user_command`, `cascade`, `migration_init`). One user intent that produces
multiple changes (for example disable a role and inactivate its workers)
shares one `decision_id`.

Event vocabulary: `team_role.enabled`, `team_role.disabled`,
`worker.initialized`, `worker.activated`, `worker.inactivated`,
`worker.released`, `worker.deleted`, `worker.restored`,
`worker.runtime_changed`, `worker.role_changed`, `worker.renamed`,
`manager.initialized`, `manager.runtime_changed`.

Idempotency: callers may pass a `command_key`; replays return the recorded
decision without re-mutating. Conversation retries of the same message id
reuse `team:<message_id>` keys, so retried chat turns never duplicate
workers.

## Dispatch invariants (fail closed)

`resolve_worker_for_role` gates every project dispatch path
(implementation trio, artifact generation, reviewer dispatch):

- TeamRole disabled → `TeamDispatchBlocked(team_role_disabled)`, even when a
  worker id is passed directly or the planner still references the worker.
- No active, runtime-eligible Worker → `TeamDispatchBlocked(team_worker_unavailable)`.

Released/deleted worker identity is never physically removed, so historical
runs, artifacts, reviews, and provenance keep resolving.

## Execution snapshot

`start_workflow` binds `state.team_snapshot`
(`team_execution_snapshot.v1`): every role's enabled state, every worker's
identity/lifecycle/runtime, the manager runtime, and the team revision at
start. Later changes — runtime edits, lifecycle moves, role changes,
renames, deletes — never rewrite a historical run's snapshot. Worker job
records additionally carry `worker_id`/`worker_name` for the executing
worker.

## Migration from the legacy worker model

The legacy project config stored team composition in YAML blocks
(`worker_backend`, `workers`, `worker_type_settings` with
`desired_instances`, `role_model_overrides`). These blocks are **no longer
the canonical worker representation**; they survive only as a derived
compatibility view that the control plane rewrites after each mutation so
diagnostics and not-yet-migrated readers keep working.

Lazy migration (`ensure_team_state` → `build_team_state_from_legacy_config`):

- Runs once per project at an explicit team touchpoint (Team surface, chat
  turn, workflow start, control-plane command, settings bridge) — never on
  rejected/failing requests.
- Every catalog Role becomes an **enabled** TeamRole (all roles were
  dispatchable before the control plane existed), so no capability silently
  disappears.
- `desired_instances = N` materializes N deterministically named workers
  (`<role>-a`, `<role>-b`, …) with the role's runtime copied onto each.
- The orchestrator keeps a single logical owner record under `manager`.
- The persisted `schema_version` (`team_control_plane.v1`) is the migration
  marker, so reruns never duplicate workers or events.

New projects adopt the same model at first team touchpoint; default team
composition policy is unchanged by this refactor.

## API surface

- `GET /api/state` (project payload) and `GET /api/projects/<key>/team` —
  current capability view plus recent ledger events.
- `POST /api/projects/<key>/team/commands` — the canonical mutation entry
  point (`{commands: [...], reason, command_key?}`); returns the decision id,
  emitted events, and the refreshed view.
- `GET /api/projects/<key>/team/events` — the append-only decision ledger.
- `POST /api/project-config` — deprecated role-keyed settings bridge;
  translates the legacy payload onto control-plane commands.
- Task Assistant actions `confirm_team_decision` / `dismiss_team_decision`
  approve or dismiss a Manager-proposed team decision.
