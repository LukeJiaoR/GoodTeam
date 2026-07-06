# Agent Sandbox v0

Issue: #218

## Purpose

Agent Sandbox v0 establishes an auditable boundary for agent work without
forcing every existing execution path to migrate immediately. The v0 contract is
small on purpose:

- sandbox workspace metadata
- command policy
- secret scrubbing
- advisory path lease / conflict risk
- sandbox ledger events
- Review Workbench sandbox evidence

This is not a production isolation runtime. It is the first ledger-backed
contract that lets maintainers test and inspect sandbox boundaries before the
runner migration is mandatory.

## API Surface

The sandbox API is available through `ExecutionAPI`:

```python
api = ExecutionAPI(project_root)
api.create_sandbox(
    execution_id,
    claimed_paths=["src/goodteam_os/execution/api.py"],
    mode="workspace",
)
api.run_sandbox_command(execution_id, ["python", "-V"])
api.get_sandbox(execution_id)
```

`mode="workspace"` creates a deterministic isolated directory under:

```text
.goodteam/sandboxes/<execution_id>/workspace
```

`mode="worktree"` uses a pluggable git adapter:

```python
class SandboxGitAdapter:
    def create_worktree(...): ...
    def remove_worktree(...): ...
```

The default adapter shells out to `git worktree add/remove` with `shell=False`.
Tests use fake adapters so they do not depend on GitHub, remotes, or complex
local git state. If worktree creation fails, Sandbox v0 writes
`sandbox_create_failed` and does not write active sandbox metadata.

## Metadata

Active sandbox metadata is written to:

```text
.goodteam/executions/<execution_id>/sandbox.json
```

The metadata uses `schema_version = sandbox.v1` and records:

- `execution_id`
- `sandbox_id`
- `status`
- `mode`
- `workspace_path`
- `worktree_path`
- `branch_name`
- `claimed_paths`
- `conflict_risk`
- `conflict_risks`
- `created_at`

## Ledger Events

Sandbox v0 uses existing `status_changed` ledger events with
`payload.transition` and `payload.source = sandbox`. It does not expand the core
ledger enum.

Current transitions:

- `sandbox_created`
- `sandbox_create_failed`
- `sandbox_conflict_risk_detected`
- `sandbox_command_executed`
- `sandbox_command_rejected`

All sandbox events include:

```json
{
  "source": "sandbox",
  "schema_version": "sandbox.v1"
}
```

## Command Policy

Commands must be argv lists, never shell strings. Sandbox v0 rejects:

- shell strings, such as `"python -V"`
- shell execution through `sh -c`, `bash -c`, or `zsh -c`
- chained command tokens containing `&&`, `;`, or `|`
- unknown commands
- `cwd` outside the sandbox workspace/worktree

The v0 allowlist is intentionally narrow:

- `python`
- `python3`
- `pytest`

`gh` is not allowlisted. GitHub/CI writes should go through a future host-side
typed broker, not through ordinary sandbox command execution.

Rejected commands still write `sandbox_command_rejected` ledger events. Non-zero
exit codes are recorded as `sandbox_command_executed` with the exit code and do
not destroy or corrupt sandbox metadata.

## Secret Scrubbing

Sandbox v0 does not implement a real secret broker and does not hand external
credentials to agents. It scrubs these environment keys before command
execution and before writing command results to the ledger:

- `GH_TOKEN`
- `GITHUB_TOKEN`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `AWS_*`
- `GOOGLE_*`

Scrubbed values are replaced with:

```text
[REDACTED]
```

Stdout/stderr excerpts are bounded before they are returned or written to the
ledger. Command results and ledger events must not contain plaintext secret
values.

Future GitHub/CI actions should use a host-side `SandboxCredentialProvider` or
`SandboxToolBroker` style interface that exposes typed operations, not raw
tokens.

## Advisory Path Lease

`claimed_paths` is advisory in v0. It is not a hard lock and does not queue or
block agents.

Expected behavior:

- no overlap: sandbox is created normally
- overlap with an active sandbox: sandbox is still created
- overlap writes `sandbox_conflict_risk_detected`
- metadata and Workbench show `conflict_risk = true`

Sandbox v0 does not do automatic rebase, conflict resolution, scheduling, or
branch fan-in.

## Review Workbench Surface

`review_workbench.v1` projects sandbox ledger events into:

```json
{
  "available": true,
  "schema_version": "sandbox.v1",
  "commands": [],
  "conflict_risks": [],
  "events": []
}
```

When no sandbox events exist, it returns:

```json
{
  "available": false,
  "reason": "sandbox_evidence_missing"
}
```

Workbench GET routes remain read-only. They do not create sandboxes, repair
metadata, remove worktrees, or write ledger events.

The browser Workbench displays sandbox status, workspace/worktree paths, command
history, rejected commands, conflict risks, and redaction markers from ledger
evidence.

## Explicit Non-Goals

This PR does not implement:

- Docker or VM isolation
- production secret management
- a real GitHub write broker
- `gh pr merge` or `gh pr review`
- auto rebase or auto conflict resolution
- mandatory migration of all current agent execution paths into sandbox
- a large React dashboard rewrite

## Remaining #218 Gaps

- Mandatory integration of all agent execution paths.
- Real GitHub/CI typed broker.
- Stronger filesystem isolation.
- Cleanup and recovery hardening.
- Optional Docker/VM future isolation.
