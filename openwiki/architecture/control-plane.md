---
type: Architecture
title: GoodTeam control plane
description: The local-first workflow, evidence, persistence, worker, and approval architecture that GoodTeam owns across software-delivery runs.
tags: [architecture, workflow, approvals, evidence, workers]
---

# GoodTeam control plane

GoodTeam’s core product decision is ownership: it owns the workflow ledger, approvals, artifacts, memory, and provenance; external systems produce evidence or perform bounded work. This makes adapters replaceable without making GitHub, CI, or an agent CLI the source of truth. The human-facing [web experience](../product/web-experience.md) renders this backend-owned state, and the [operator loop](../operations/operator-and-quality.md) relies on its evidence to make—not automate—approval decisions.

## Two workflow planes

### Idea-to-delivery graph

`graph.py`, `workflow_table.py`, and `state.py` implement the primary project/run lifecycle. An initial greenfield run starts at product requirements, creates durable state, and pauses at explicit human gates:

```text
product_requirement
  → PRD gate → architect → RFC gate → security gate
  → planning / implementation → staging gate → release gate
  → retrospective memory → complete
```

A gate outcome is durable and explicit: approval advances, changes requested routes to a `revise_<gate>` node, rejection terminates the run, and pending waits. Reviewer recommendations and automated checks inform the decision but do not substitute for it. The graph also has a distinct fix profile (`fix_plan → fix_implement → code_review → fix_qa → qa → fix_close`).

Topology version 2 validates a workflow plan before it is used, but execution remains sequential; parallel-level execution is deliberately deferred. See `src/goodteam_os/workflow_table.py`, `src/goodteam_os/graph.py`, `src/goodteam_os/nodes/review_gate.py`, and contracts in `tests/test_workflow_table.py`.

### Issue-to-PR execution plane

The `src/goodteam_os/execution/` package drives a more bounded GitHub-oriented flow: intake an issue, create a stable execution identity and worker job, prepare a sandbox, observe PR/CI/review state, collect evidence, and finalize provenance. It complements rather than replaces the delivery graph.

Execution identity derives from repository/issue and attempt inputs. It binds a deterministic execution ID, branch, PR, head SHA, CI evidence, and idempotency keys so evidence cannot silently attach to unrelated work. `execution/ledger.py` persists task/run projections and append-only events; `execution/evidence.py` and provenance closure record the resulting evidence.

The [operator loop](../operations/operator-and-quality.md) applies this identity chain when it reviews or merges GitHub work.

## Durable information model

GoodTeam uses two local persistence scopes:

| Scope | Holds | Key locations |
| --- | --- | --- |
| GoodTeam workspace | Current run control state, checkpoints, generated artifacts, and readiness state. | `state/projects/<KEY>/current.json`, `state/projects/<KEY>/checkpoints/`, `artifacts/runs/<run-id>/…` |
| Target project | Work that should travel with the target: worker evidence, local memory, tasks, execution data, and provenance. | `<target>/.goodteam/workers/jobs/`, `.goodteam/memory/`, `.goodteam/tasks/`, `.goodteam/executions/` |

`StateStore` writes project-scoped current state and collision-safe checkpoints. Artifact writes use `artifacts/runs/<run-id>/<section>/`; optional target-repository document mirrors are best effort rather than a second control authority. Worker jobs preserve task/context/constraint inputs, expected artifacts, results, standard streams, and status under their job directory.

Memory is project- and role-scoped. It persists Markdown horizons and append-only event data, then retrieves context using optional local embeddings or a BM25/token fallback. That memory is a support mechanism for future work, not an approval authority.

## Worker and sandbox boundary

`workers.py` resolves a global worker backend plus optional role-specific overrides and persists jobs before running them. Fake/noop/human paths are completed internally; CLI and persistent-session paths are dispatched through adapter code and record readiness, results, failure classification, and memory/provenance context. `doctor` reports safe readiness signals, not secret values.

The execution orchestrator uses a constrained sandbox/worktree lifecycle. Worker activity is scoped to that environment; pending PR worktrees are retained, while completed non-pending work can be cleaned up. The [operations page](../operations/operator-and-quality.md) documents the additional GitHub/CI/SHA safeguards that sit around this bounded execution.

Current code also routes several CLI/session modes beyond the narrower list in older docs. Treat the backend abstraction and doctor output as stable; consult `workers.py` before relying on an exhaustive backend-mode list.

## Relationship to project preparation and UI

The delivery graph **is gated by** [project readiness](../workflows/project-readiness.md): backend code computes `start_workflow` only after the imported root and required preparation execution are bound and complete. The React UI **is surfaced by** [the web experience](../product/web-experience.md), but API clients cannot bypass state transitions or capability checks.

## Where to change safely

- Workflow progression and profiles: `graph.py`, `workflow_table.py`, `nodes/review_gate.py`; start with `tests/test_workflow_table.py`, `tests/test_graph_sample.py`, and `tests/test_fix_profile.py`.
- State/artifact durability: `state.py`, `artifacts.py`; use `tests/test_state.py` and `tests/test_artifacts.py`.
- Workers and persistent sessions: `workers.py`, `session_runtimes.py`, `pi_rpc.py`; use `tests/test_workers.py`, `tests/test_pi_rpc.py`, and `tests/test_pi_session_runtime.py`.
- Issue execution/evidence/sandbox: `execution/{orchestrator,ledger,idempotency,evidence,sandbox}.py`; use the `tests/test_execution_*.py` suites.

Changes touching approval, execution identity, or evidence provenance warrant the full backend suite under the Tier 3 guidance in [operations and quality](../operations/operator-and-quality.md).
