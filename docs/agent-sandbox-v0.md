# Agent Sandbox v0

Issue: #218

## Purpose

Agent Sandbox v0 establishes an auditable execution workspace boundary for agent
work. #309 added the boundary primitives; #310 wires those primitives into the
real `ExecutionOrchestrator.run_execution()` lifecycle. The v0 contract is small
on purpose:

- sandbox workspace metadata
- command policy
- secret scrubbing
- advisory path lease / conflict risk
- sandbox ledger events
- Review Workbench sandbox evidence

This is not a replacement for Codex CLI or Claude Code CLI runtime sandboxing.
It is the first ledger-backed contract that lets maintainers test and inspect
execution workspace lifecycle, runtime cwd selection, and Review Workbench
evidence while each CLI keeps owning its runtime permission model.

## Runtime-Native Sandbox Strategy

GoodTeam does not replace Codex CLI / Claude Code CLI runtime sandboxing, and it
does not claim that a particular worker launch preserved those native controls.
If the configured CLI launch bypasses sandboxing or approvals, GoodTeam records
that fact as runtime evidence and surfaces a warning.

GoodTeam owns:

- execution workspace/worktree lifecycle
- runtime cwd selection
- worker context
- ledger evidence
- Review Workbench rendering

Codex/Claude own their native runtime controls when those controls are enabled
by the configured launch:

- command permissions
- filesystem/network sandbox
- approval policy
- model/tool execution safety

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
api.destroy_sandbox(execution_id)
```

`mode="workspace"` creates a deterministic isolated directory under:

```text
.goodteam/sandboxes/<execution_id>/workspace
```

Workspace mode is a fallback/evidence-only coding boundary unless the directory
has been materialized as a repo workspace. Empty workspace directories are not
used as CLI cwd for coding workers; the worker records an explicit
`workspace_cwd_fallback = true` status and falls back to the project root.

`mode="worktree"` uses a pluggable git adapter:

```python
class SandboxGitAdapter:
    def create_worktree(...): ...
    def remove_worktree(...): ...
```

The default adapter shells out to `git worktree add/remove` with `shell=False`.
Tests use fake adapters so they do not depend on GitHub, remotes, or complex
local git state. If worktree creation fails, Sandbox v0 writes
`sandbox_create_failed` and does not write active sandbox metadata. Destroying a
worktree sandbox calls the adapter's `remove_worktree`.

Destroy is idempotent. A first destroy writes `sandbox_cleanup_requested`, then
marks metadata `status = destroyed` and writes `sandbox_destroyed`; later destroy
calls return `already_destroyed` without appending another ledger event.

Successful worktree executions are retained for PR/diff handoff instead of
being removed immediately. The orchestrator records `sandbox_retained_for_pr`
and metadata `status = retained_for_pr`; a later explicit cleanup can call
`destroy_sandbox()` to remove the worktree.

If destroy fails, Sandbox v0 writes `sandbox_destroy_failed`, persists metadata
`status = cleanup_failed`, and returns a cleanup-failed result with
`recovery_state = sandbox_cleanup_recovery_required`. The execution outcome is
not rewritten: a worker success remains `execution_completed`, and a worker
failure remains `execution_failed`. A later `destroy_sandbox()` call can retry
from `cleanup_failed`.

## Execution Lifecycle

`ExecutionOrchestrator.run_execution()` creates a sandbox by default before it
runs the worker job:

```text
execution_started
  -> sandbox_created
  -> worker CLI launched with cwd=<execution workspace/worktree>
  -> execution_completed / execution_failed
  -> sandbox_retained_for_pr / sandbox_destroyed
```

The default execution mode is `auto`: git repositories use `worktree`; non-git
test or local fallback roots use `workspace`. Real coding runs should use git
worktree mode or another repo-backed workspace. Workspace fallback runs can be
destroyed immediately after completion because an empty metadata workspace is
not the source of PR changes. Worktree success is retained until PR/diff
handoff or explicit cleanup so uncommitted worker changes are not discarded
before they can be persisted.

The worker receives a sanitized `sandbox_context` with:

```json
{
  "available": true,
  "schema_version": "sandbox.v1",
  "execution_id": "...",
  "sandbox_id": "...",
  "workspace_path": "...",
  "worktree_path": "",
  "mode": "workspace",
  "branch_name": "...",
  "claimed_paths": [
    "src/goodteam_os/workers.py",
    "tests/test_workers.py"
  ]
}
```

The context is written to worker job metadata for auditability. It does not
include host credentials, GitHub tokens, API keys, or secret values. Workers can
use `workspace_path` as GoodTeam's workspace/evidence boundary context. It does
not replace Codex/Claude runtime sandboxing.

Worker status and invocation evidence record:

```json
{
  "runtime_adapter": "codex",
  "runtime_binary": "/path/to/codex",
  "runtime_version": "codex 0.61.0",
  "runtime_version_warning": false,
  "runtime_version_warning_reason": "",
  "runtime_version_failure_reason": "",
  "runtime_model": "gpt-5.4",
  "runtime_cwd": "...",
  "runtime_workspace_mode": "worktree",
  "runtime_workspace_status": "active",
  "runtime_sandbox_mode": "disabled",
  "runtime_approval_policy": "bypass",
  "runtime_unsafe_mode": true,
  "runtime_policy_warning": true,
  "runtime_policy_warning_reason": "runtime_policy_unsafe",
  "runtime_transcript_path": "",
  "stdout_path": "...",
  "stderr_path": "...",
  "result_path": "...",
  "workspace_context_available": true,
  "workspace_cwd_fallback": false,
  "workspace_cwd_fallback_reason": "",
  "pythonpath_workspace_src": true
}
```

GoodTeam records only fields it can observe locally. Runtime version detection
uses `<binary> --version` and is best-effort; missing binaries, timeouts, or
non-zero version commands record `runtime_version = unknown` plus
`runtime_version_warning_reason = runtime_version_unavailable`. GoodTeam does
not write tokens, secret values, or full environment dumps into runtime evidence.

Runtime policy fields are evidence, not enforcement. If GoodTeam can see an
obvious unsafe launch flag such as Codex
`--dangerously-bypass-approvals-and-sandbox`, it records
`runtime_sandbox_mode = disabled`, `runtime_approval_policy = bypass`,
`runtime_unsafe_mode = true`, and
`runtime_policy_warning_reason = runtime_policy_unsafe`. If sandbox or approval
policy cannot be verified, it records `unknown` values and
`runtime_policy_warning_reason = runtime_policy_unverified`. The warning does
not fail the worker in v0.

If `<runtime_cwd>/src` exists, the worker env prepends it to `PYTHONPATH` so
Python imports prefer the execution workspace while still allowing shared
repo-level `.venv`, pip cache, npm cache, pnpm store, and tool binaries. The
worker result, stdout, stderr, prompt, and status protocol files remain absolute
paths under the job root, not under the worktree.

Sandbox creation failure is deterministic: the orchestrator records
`sandbox_create_failed`, marks the execution failed, and does not run the worker.
Missing executions still fail before any sandbox artifact or missing-execution
ledger file is created.

`run_sandbox_command()` remains a narrow testable command-policy surface. It is
not the main Codex/Claude runtime path.

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
- `path_claims`
- `path_claim_warnings`
- `conflict_risk`
- `conflict_risks`
- `created_at`
- `destroyed_at` after sandbox destroy

## Ledger Events

Sandbox v0 uses existing `status_changed` ledger events with
`payload.transition` and `payload.source = sandbox`. It does not expand the core
ledger enum.

Current transitions:

- `sandbox_created`
- `sandbox_create_failed`
- `sandbox_conflict_risk_detected`
- `sandbox_command_executed`
- `sandbox_command_failed`
- `sandbox_command_rejected`
- `sandbox_cleanup_requested`
- `sandbox_destroy_failed`
- `sandbox_destroyed`
- `sandbox_retained_for_pr`

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
not destroy or corrupt sandbox metadata. Timeout, spawn, and unexpected command
execution failures write `sandbox_command_failed` with bounded, scrubbed command
evidence.

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

The orchestrator now infers `claimed_paths` before sandbox creation from local,
already persisted execution inputs:

- worker protocol files: `task.md`, `context.md`, and
  `expected_artifacts.json`
- worker status `changed_files` when an execution already has them
- persisted `pr-diff.json` changed files when a diff artifact already exists
- medium-confidence area mappings such as worker/runtime, sandbox/worktree,
  Review Workbench, ledger/evidence, and documentation

Path inference is intentionally conservative. It keeps repo-relative POSIX
paths, preserves directory claims such as `docs/`, converts globs such as
`src/goodteam_os/execution/*.py` to `src/goodteam_os/execution/`, rejects host
absolute paths, `..` parent traversal, URLs, and unsafe path-like values, and
de-duplicates while preserving inference order.

Each inferred path may also include a structured `path_claims[]` entry:

```json
{
  "path": "src/goodteam_os/workers.py",
  "source": "task_text",
  "confidence": "high",
  "reason": "explicit repo-relative path"
}
```

Broad or low-confidence inference records
`path_claims_broad_or_low_confidence`; unresolved inference records
`path_claims_unresolved`. These warnings are evidence for reviewers, not
execution blockers.

Expected behavior:

- no overlap: sandbox is created normally
- overlap with an active sandbox: sandbox is still created
- overlap writes `sandbox_conflict_risk_detected`
- metadata and Workbench show `conflict_risk = true`

Overlap detection is intentionally small in v0 and supports exact or directory
prefix relationships. Exact file overlap records high-confidence conflict
evidence; directory/file prefix overlap records medium-confidence conflict
evidence. It does not interpret glob patterns such as `src/**` after inference
normalizes supported globs to directory claims.

Sandbox v0 does not do automatic rebase, conflict resolution, scheduling, or
branch fan-in.

## Review Workbench Surface

`review_workbench.v1` projects sandbox ledger events into:

```json
{
  "available": true,
  "schema_version": "sandbox.v1",
  "status": "retained_for_pr",
  "cleanup_required": true,
  "cleanup_warning_reason": "sandbox_retained_for_pr",
  "runtime": {
    "runtime_adapter": "codex",
    "runtime_version": "codex 0.61.0",
    "runtime_cwd": "...",
    "runtime_policy_warning": true
  },
  "claimed_paths": ["src/goodteam_os/workers.py"],
  "path_claims": [
    {
      "path": "src/goodteam_os/workers.py",
      "source": "task_text",
      "confidence": "high",
      "reason": "explicit repo-relative path"
    }
  ],
  "path_claim_warnings": [],
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

The browser Workbench displays sandbox status, workspace/worktree paths, cleanup
required warnings, inferred claimed paths, claim source/confidence/reason,
runtime adapter evidence, stdout/stderr/result paths, command history, rejected
commands, conflict risks, lifecycle events, and redaction markers from ledger
evidence. Workbench remains read-only; cleanup still happens through explicit
API/CLI calls.

## Explicit Non-Goals

This PR does not implement:

- Docker or VM isolation
- production secret management
- a real GitHub write broker
- `gh pr merge` or `gh pr review`
- auto rebase or auto conflict resolution
- a large React dashboard rewrite

## Remaining #218 Gaps

- Deeper runtime-policy introspection when Codex/Claude expose machine-readable
  sandbox mode or approval policy beyond launch-argument evidence.
- Richer path-claim inference from future typed task contracts.
- Cleanup UX/actions outside the read-only Review Workbench surface.
- Optional Docker/VM future isolation for non-Codex/Claude runtimes.
- GitHub/CI typed broker only if needed for host-side operations.
