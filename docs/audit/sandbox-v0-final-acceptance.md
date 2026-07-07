# Sandbox v0 Final Acceptance Audit

Date: 2026-07-07

Target issue: #218, "P0-4: Agent Sandbox v0 (Execution Safety & Isolation Boundary)"

Baseline: `origin/main` at `cf92f27`, the #312 merge commit.

Issue state note: `gh issue view 218` returned `CLOSED` during this audit. This
PR still uses `Refs #218` rather than `Closes #218` so that issue state remains
an explicit maintainer decision.

## Executive Summary

Conclusion: Sandbox v0 MVP is complete for the GoodTeam-controlled execution
path after the #309 through #312 chain.

#218 asked for a minimal but real execution sandbox for agent actions in
GoodTeam. The merged chain now creates per-execution workspace or worktree
metadata, routes `run_execution()` through sandbox lifecycle by default, runs
worker CLIs from the execution workspace or worktree when available, records
sandbox lifecycle and command evidence to the execution ledger, captures runtime
adapter evidence and cleanup state, infers path claims, surfaces advisory
conflict risk, and projects the result into Review Workbench JSON and HTML.

Recommendation: close #218 as Sandbox v0 MVP completed after this audit PR
merges.

Do not close #218 automatically from this PR unless the PR body intentionally
says `Closes #218`. This PR should say `Refs #218`.

## Scope Summary

GoodTeam uses a two-layer model. GoodTeam owns the orchestration, evidence, and
workspace boundary that it can enforce inside its own execution API. The native
agent runtimes still own their lower-level sandbox and permission systems.

GoodTeam owns:

- execution workspace/worktree lifecycle
- runtime cwd selection
- path claims and advisory conflict evidence
- runtime adapter evidence
- sandbox metadata and ledger evidence
- Review Workbench projection
- cleanup/recovery state

Codex/Claude own:

- native runtime sandboxing
- command permissions
- filesystem/network controls
- approval policy
- model/tool execution safety

GoodTeam does not replace the Codex or Claude runtime sandbox. GoodTeam creates
and records the execution boundary it controls, warns when native runtime policy
is unsafe or unverified, and exposes that evidence for human review.

## PR Chain

| PR | Title | Merge commit | Main capability | #218 acceptance contribution |
| --- | --- | --- | --- | --- |
| #309 | `feat(sandbox): add auditable sandbox v0 boundary` | `fcf73a0f671885f25f15bcacb26ffbc2e3a14cf9` | Added sandbox primitives, workspace/worktree metadata, command policy surface, secret scrubbing, sandbox ledger events, destroy lifecycle, and Review Workbench sandbox evidence. | Established the sandbox layer, lifecycle metadata, command ledger events, and evidence projection required by all four acceptance criteria. |
| #310 | `feat(sandbox): run executions through workspace lifecycle evidence` | `7ef26b4f20ea787c7c1451101c47f5f707b4c292` | Routed `run_execution()` through sandbox creation by default, selected worktree mode for git repo roots, passed sandbox context to workers, ran worker CLIs from the repo-backed workspace/worktree when available, preserved shared `.venv`, retained successful worktrees for PR/diff handoff, and kept cleanup explicit. | Satisfies the GoodTeam-controlled execution path for "all agent executions run inside sandbox layer" and "no direct filesystem or repo mutation occurs outside sandbox" for the worker coding path. |
| #311 | `feat(sandbox): capture runtime adapter evidence and cleanup state` | `6aadfc0bd37e0a968bf2c41cf0d97c3682cbdbbf` | Captured runtime adapter, binary, version, model, cwd, workspace status, native runtime policy warnings, stdout/stderr/result paths, cleanup requested/destroyed/failed state, and retryable cleanup recovery. | Strengthened ledger/workbench observability and failure containment, including failed cleanup evidence that does not overwrite the execution outcome. |
| #312 | `feat(sandbox): infer execution path claims` | `cf92f277e66d19a090dd05b421f339c33db61598` | Added path claim inference from task/context/artifact evidence, path claim warnings, exact/prefix advisory conflict risks, and Workbench claimed paths/conflict surface. | Strengthened filesystem mutation visibility and advisory conflict detection for overlapping execution scopes. |

## Acceptance Criteria Mapping

| #218 acceptance criterion | Audit result | Evidence | Caveat |
| --- | --- | --- | --- |
| All agent executions run inside sandbox layer | MVP satisfied for the GoodTeam-controlled execution path. | `ExecutionOrchestrator.run_execution()` creates sandbox context by default when `sandbox_enabled=True`; `auto` mode selects `worktree` for git repo roots and `workspace` otherwise; worker jobs receive sandbox context; sandbox create failure records failure and prevents the worker run. | Future external integrations are not guaranteed until they route through the execution API. `sandbox_enabled=False` remains a legacy/explicit escape hatch. |
| No direct filesystem or repo mutation occurs outside sandbox | MVP satisfied for the worker coding path. | Worker CLI cwd selection uses the sandbox workspace/worktree when it is a repo-backed workspace; main repo root is not the coding cwd when a repo-backed sandbox exists; successful worktree executions are retained for PR/diff handoff; cleanup is explicit; shared `.venv` is preserved rather than copied into the worktree. | Codex/Claude native runtime filesystem/network controls remain the runtime responsibility. GoodTeam records runtime policy warnings but does not yet fully introspect a machine-readable native policy. |
| All actions are logged to ledger | MVP partially satisfied and scoped. | Sandbox lifecycle events include create, create failure, command executed/rejected/failed, cleanup requested, destroy, destroy failure, retain, and conflict risk. Runtime invocation evidence is captured in worker status and Review Workbench. Path claim conflict evidence and cleanup recovery state are projected into Workbench JSON/HTML. | Codex/Claude internal tool-by-tool transcripts are not fully normalized into the GoodTeam ledger unless the runtime/adapter exposes them. Machine-readable transcript ingestion is a follow-up. |
| A failed execution does not corrupt system state | MVP satisfied. | Sandbox create failure returns a failed execution path before worker dispatch; worker failures/exceptions record failure while sandbox cleanup still runs; destroy failure records `cleanup_failed` and `sandbox_cleanup_recovery_required` without overwriting execution outcome; retained worktrees preserve PR/diff handoff; cleanup is retryable/idempotent; advisory conflict risks surface overlapping path claims. | This is v0 containment for GoodTeam-owned workspace/worktree lifecycle, not enterprise VM isolation. |

### Detailed Acceptance Notes

#### All agent executions run inside sandbox layer

MVP satisfied for the GoodTeam-controlled execution path:

- `run_execution()` defaults to sandbox lifecycle creation.
- Git repo roots use worktree isolation through `auto` mode.
- Worker CLI cwd uses the repo-backed workspace/worktree when available.
- Fallback to project root is explicit runtime evidence, not silent.
- `sandbox_enabled=False` remains a legacy/explicit escape hatch.

Caveat:

- Not every future external integration path is guaranteed until routed through
  the execution API.

#### No direct filesystem or repo mutation occurs outside sandbox

MVP satisfied for worker coding path:

- Worker cwd is the execution workspace/worktree when the workspace is a repo.
- Main repo root is not the coding cwd when repo-backed workspace exists.
- Worktree is retained for PR/diff handoff.
- Cleanup is explicit.
- Shared `.venv` is allowed, but mutable outputs/artifacts stay separate.

Caveat:

- Codex/Claude native runtime sandbox controls are external runtime
  responsibility.
- GoodTeam records runtime policy warnings but does not fully introspect
  machine-readable policy yet.

#### All actions are logged to ledger

MVP partially satisfied and scoped:

- GoodTeam sandbox lifecycle events are logged.
- Command policy surface logs sandbox command execution, rejection, and failure.
- Runtime adapter evidence is captured in worker status and Workbench.
- Cleanup and path claim conflict evidence are logged.
- Review Workbench projects evidence.

Caveat:

- Codex/Claude internal tool-by-tool transcript is not fully normalized into the
  GoodTeam ledger yet unless exposed by runtime/adapter.
- Future machine-readable transcript ingestion can be follow-up.

#### A failed execution does not corrupt system state

MVP satisfied:

- Sandbox create failure prevents worker run.
- Worker failure/exception records failure and cleanup evidence.
- Destroy failure records `cleanup_failed` / recovery required and does not
  overwrite execution outcome.
- Retained worktree prevents PR/diff loss.
- Cleanup is retryable/idempotent.
- Advisory conflict risk surfaces overlapping path claims.

## Evidence Surfaces

The following evidence surfaces are now available:

- sandbox metadata
- sandbox ledger events
- runtime invocation evidence
- path claim inference
- conflict risks
- cleanup recovery state
- Review Workbench JSON
- Review Workbench HTML

Concrete local surfaces include:

- `.goodteam/executions/<execution_id>/sandbox.json`
- execution ledger events whose payload has `source = sandbox`
- worker job status and `invocation_evidence`
- worker job `stdout.txt`, `stderr.txt`, and `result.md`
- Review Workbench `sandbox` object from `review_workbench.v1`
- browser Sandbox evidence section rendered by Review Workbench HTML

## Remaining Gaps That Are Not v0 Blockers

The following items should remain follow-up candidates rather than blocking
Sandbox v0 closure:

- machine-readable Codex/Claude runtime policy introspection
- typed task contract for stronger path claims
- Workbench/CLI cleanup action UX beyond read-only display
- GitHub/CI typed broker for host-side write operations
- optional Docker/VM isolation for non-Codex/Claude runtimes
- normalized Codex/Claude transcript ingestion if runtime exposes it

These gaps are important hardening work, but they are outside the minimal v0
boundary now implemented and evidenced by the #309 through #312 chain.

## Verification Recommendation

This is a docs-only audit PR. The recommended validation level is Tier 0 plus
the #218 focused regression set because the document asserts current
control-plane behavior:

```bash
git diff --check
.venv/bin/ruff check .
PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution_path_claims.py tests/test_agent_sandbox_v0.py tests/test_execution_sandbox_integration.py tests/test_review_workbench_sandbox_evidence.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution*.py tests/test_review*.py -q
```

Full backend suite is optional for this docs-only PR unless maintainers want a
fresh repository-wide signal before manual issue closure.
