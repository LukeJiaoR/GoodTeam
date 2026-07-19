---
type: Operations Guide
title: Operator loop, service management, and quality
description: Safe operation of GoodTeam's managed service, issue-to-merge operator loop, external adapters, and tiered verification strategy.
tags: [operations, operator, service, testing, integrations]
---

# Operator loop, service management, and quality

GoodTeam is designed to make a human operator better informed, not autonomous over delivery decisions. The operator runs and observes [the control plane](../architecture/control-plane.md), which owns workflow/evidence state; external GitHub, CI, Telegram, and worker systems are bounded adapters. [Project readiness](../workflows/project-readiness.md) is a preceding safety boundary that prevents workflow start until local preparation is complete.

## Managed local service

`goodteam restart`, `goodteam status`, and `goodteam stop` manage a detached supervisor process (default port 21001); `--workspace` and `--port` override the defaults. `service.py` records process identity in a state directory, serializes lifecycle actions, waits for readiness, and rejects an unmanaged process already occupying the port rather than taking it over.

`supervisor.py` runs and health-checks web, Telegram, and operator children independently. It has bounded restart behavior and pauses active operator work when recovery is exhausted. `goodteam-os` remains the compatibility command for the same CLI entrypoint and its run/serve/doctor commands.

Before service startup, build frontend assets:

```sh
npm --prefix frontend install
npm --prefix frontend run build
goodteam restart
goodteam status
```

The [web experience](../product/web-experience.md) explains where those assets and routes are served.

## GitHub issue-to-merge operator loop

The operator queue persists a FIFO, one-active-item state machine with atomic projections and an append-only event log. The representative path is:

```text
queued → hydrating → preflight → executing → pr_ready → observing_ci
→ review_ready → waiting_telegram → approved / changes_requested / rejected
→ merging → completed
```

GitHub intake accepts canonical issue URLs only and resolves them against locally registered project configuration. Unknown repositories are rejected rather than silently adopted. Preflight requires a ready worker backend plus explicit required CI checks from configuration or branch protection; missing checks fail closed.

The operator creates worktree-sandboxed work and persists external-operation intent/results. If recovery cannot establish whether a mutating external action completed, it pauses as ambiguous instead of replaying it. Merge requires all of the following for the same PR head SHA:

1. Telegram approval binds to that SHA.
2. Required CI passes on that SHA.
3. Independent review matches that SHA and has no blockers.

These safeguards depend on the [control plane’s execution identity and evidence model](../architecture/control-plane.md). Do not weaken SHA binding, required checks, idempotency, or intent reconciliation when extending operator actions.

## External adapters and safety boundaries

- **Workers:** Real backends are explicit configuration choices. Persistent Pi/Pi-RPC session support stores local protocol/session evidence and validates resume lineage; the normal job/worktree policy remains GoodTeam-owned.
- **Telegram:** Long-polling bot configuration uses environment-variable names documented in source/README. Review decisions route through the same durable queue and bind the approved PR head; bot output redacts selected sensitive patterns. Do not document or place token values in source/wiki content.
- **Smoke:** `smoke` and `full_smoke` run isolated acceptance loops. Real worker invocation is blocked unless explicitly allowed; dry runs do not call external workers. The self suite refuses target roots inside this repository.
- **Project configuration:** use the committed [`projects/example.yaml`](../../projects/example.yaml) as schema guidance. Private local configs use `projects/<key>.local.yaml`; project-local work evidence is under `<target>/.goodteam/`.

## Verification tiers

`docs/testing-strategy.md` defines the repository’s change-sensitive verification policy:

| Tier | Typical scope | Baseline |
| --- | --- | --- |
| Tier 0 | Documentation-only | `git diff --check`, link/manual review. |
| Tier 1 | Helpers/parsers | Focused pytest plus Ruff. |
| Tier 2 | APIs/state projections | Focused API/web tests plus Ruff; frontend lint/build where relevant. |
| Tier 3 | Approval, ledger, execution, evidence | Focused subsystem tests, full backend suite, Ruff. |
| Tier 4 | Browser, adapter, or live paths | Tier 3 plus isolated live smoke/Playwright and observed evidence. |

Core commands:

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os tests
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
```

Select focused tests first; run the full backend suite for high-consequence state, approval, execution, and provenance changes. The test tree is intentionally flat under `tests/`: service/supervision tests, operator queue/state-machine tests, execution contract tests, worker/session tests, web/API tests, and smoke tests collectively enforce the boundaries above.

## Primary change anchors

- Service/supervision: `src/goodteam_os/{service.py,supervisor.py}`, `tests/test_service_cli.py`, `tests/test_supervisor*.py`.
- Operator/GitHub: `operator.py`, `operator_queue.py`, `operator_github.py`, `tests/test_operator_*.py`.
- Telegram/session controls: `telegram.py`, `telegram_runtime_controls.py`, `session_runtimes.py`, `pi_rpc.py` and their tests.
- Smoke: `smoke.py`, `full_smoke.py`, `tests/test_smoke.py`, `tests/test_full_smoke.py`.

For operational changes, preserve the distinction between observing evidence and authorizing a decision. The human approval gate remains central even when CI is green or a reviewer recommends acceptance.
