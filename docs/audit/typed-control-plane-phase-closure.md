# Typed Control Plane Phase Closure Audit

Date: 2026-07-10

Issue: #333, "Typed Control Plane Phase Closure Audit"

Baseline: `main` at `8c46fe1`, the #332 merge commit.

## Executive Summary

The typed evidence/control-plane phase is complete for the current GoodTeam MVP
boundary.

This does not mean GoodTeam is feature-complete as a full operator product. It
means the core execution-control surfaces now form one auditable chain from task
intent through runtime policy, external status, replay, review, and safe worker
backend readiness.

The completed chain is:

```text
task_contract.v1
  -> runtime_policy.v1
  -> github_broker_status.v1
  -> execution_replay_graph.v1
  -> safe local-only worker backend preflight
```

Recommendation: close #333 as completed after this audit PR merges.

## Completed Issue and PR Map

| Capability | Issue | PR | Status | Closure meaning |
| --- | --- | --- | --- | --- |
| Typed task intent and acceptance contract | #317 | #325 | Complete | Execution tasks can carry structured source intent, identity, instructions, constraints, expected artifacts, path scope, acceptance criteria, verification expectations, review expectations, and PR metadata. |
| Runtime policy and capability preflight | #326 | #327 | Complete | Executions expose machine-readable runtime policy, backend class, external invocation state, sandbox/workspace mode, path-scope policy, verification expectations, and fallback warnings. |
| GitHub/CI typed broker status | #328 | #329 | Complete | PR metadata, CI observations, review observations, blocker/readiness state, and merge readiness are normalized into replayable evidence. |
| Execution replay graph / timeline | #330 | #331 | Complete | Ledger events and typed surfaces are projected into an ordered execution graph with nodes, edges, timeline, raw event refs, and warning/fallback nodes. |
| Safe local-only worker backend preflight | #322 | #332 | Complete | Real backend readiness can be checked without exporting prompts, target files, repo content, or secrets; real invocation remains explicit opt-in. |

## Capability Map

### Task and acceptance contract

GoodTeam can now represent task intent and acceptance expectations as a typed
contract rather than relying only on issue text or ad hoc execution context.
Legacy executions without this data surface explicit fallback state instead of
fabricated contract fields.

### Runtime policy visibility

GoodTeam can report which backend class is selected, whether external invocation
is requested/allowed/blocked, which workspace/sandbox mode applies, and what
path-scope and verification expectations are in force.

This turns runtime policy into reviewable evidence before and during execution,
rather than hidden operator context.

### GitHub/CI broker status

GoodTeam can normalize GitHub-facing observations into a typed status surface:
PR state, commit/branch metadata, CI observation state, review state, blocker
count, and merge readiness.

The projection does not replace raw adapter behavior. It gives Review Workbench
and future replay tools a stable evidence surface over external lifecycle state.

### Replay graph / timeline

GoodTeam can reconstruct an ordered execution timeline across intake, contract,
runtime policy, worker job, sandbox lifecycle, artifacts, PR/CI/review status,
broker status, merge/finalize, memory provenance, reviewer decision, and
warnings.

The replay graph is a projection of ledger/evidence data. It does not replace
the append-only ledger.

### Worker backend readiness preflight

GoodTeam can classify real worker backends such as Codex, Claude/Anthropic,
Gemini, Agy, and OpenCode as `ready`, `warn`, or `fail` through local-only
preflight.

The preflight path only checks local CLI readiness, version readability, auth
readiness where safely knowable, selected backend, planned command shape, and
workspace/sandbox expectations. It does not send target files, prompts, repo
content, API keys, OAuth tokens, or local auth material to external services.

## Evidence Surfaces

The current phase makes these review surfaces first-class for the control plane:

- `task_contract.v1`
- `runtime_policy.v1`
- `github_broker_status.v1`
- `execution_replay_graph.v1`
- worker backend local preflight in full smoke `summary.json` and `report.md`
- Review Workbench text/browser projections
- evidence manifest fallback warnings for missing legacy data

Missing or incomplete evidence must remain visible as warnings or empty states.
The system must not invent success state for demo quality.

## Runtime and Worker Boundary

GoodTeam owns:

- task contracts
- execution identity and append-only ledger evidence
- evidence manifest projections
- Review Workbench surfaces
- runtime policy and path-scope visibility
- GitHub/CI/review typed broker status
- execution replay graph and timeline projections
- worker backend readiness preflight

External agent runtimes such as Codex, Claude, Gemini, Agy, and OpenCode own:

- model inference
- provider-side execution behavior
- runtime-native sandbox and permission model
- network/filesystem approval boundaries implemented by the external tool

GoodTeam does not default to real external worker invocation.

Deterministic acceptance remains fake/noop worker based. Real backend smoke uses
local-only preflight unless the operator explicitly opts into real execution.

## Acceptance and Validation Status

The phase was accepted through the merged implementation PRs and their recorded
validation:

- #325 added typed task contracts and kept legacy fallback explicit.
- #327 added runtime policy preflight and Review Workbench visibility.
- #329 added GitHub/CI broker status and readiness projection.
- #331 added the replay graph/timeline projection and partial/legacy warning nodes.
- #332 added safe local-only real-backend preflight and full-smoke reporting.

The accepted validation pattern for this phase is:

```bash
git diff --check
.venv/bin/ruff check .
PYTHONPATH=src .venv/bin/python -m pytest tests -q
scripts/full-acceptance-smoke.sh --output-root <tmp-output-root> --suite contentops --cycles 1 --worker-mode fake --issue '#<issue>'
```

#322 also established the real-backend dry-run smoke pattern:

```bash
scripts/full-acceptance-smoke.sh \
  --output-root /private/tmp/goodteam-full-smoke-worker-preflight-real \
  --suite contentops \
  --cycles 1 \
  --worker-mode codex \
  --dry-run \
  --issue '#322'
```

This closure audit is docs-only. No runtime behavior is changed by this PR.

## Non-blocking Follow-ups

The following issues remain useful next-phase work, but they do not block this
typed control-plane phase closure:

| Issue | Area | Why it is non-blocking for this phase |
| --- | --- | --- |
| #319 | Dashboard unknown-project error polish | Product polish over an existing backend error state. |
| #320 | Memory/Evidence/Review Workbench dashboard surfaces | Operator UX/productization over already-existing evidence surfaces. |
| #321 | Reviewer reason policy for request-changes/reject | Approval UX and audit-quality hardening, not a missing typed control-plane primitive. |
| #297 | Dashboard Docs tab | Product visibility, not execution/evidence correctness. |
| #254 | TG group role identity platform spike | Collaboration platform design, scoped away from this phase. |

## Recommended Next Phase

The next phase should move from core control-plane primitives to operator product
quality:

1. Approval UX and reviewer reason policy (#321)
2. First-class Memory/Evidence/Review Workbench dashboard surfaces (#320)
3. Styled dashboard error handling (#319)
4. Dashboard Docs tab (#297)
5. TG group role identity design spike (#254)

This sequencing keeps the rule from the production control plane sprint: demo
and dashboard quality should be projections of real control-plane evidence, not
substitutes for it.

## Closure Decision

The typed control-plane phase is complete.

GoodTeam now has a typed and replayable evidence chain for task intent, runtime
policy, external PR/CI/review status, execution timeline, and safe worker backend
readiness. The remaining work should be treated as next-phase product/operator
UX or platform follow-up rather than as blockers for the completed core phase.

Recommendation: merge this docs-only PR, then close #333 as completed.
