# Skill content preparation contract (S5 / #445)

**Status: non-activated contract implementation, not complete S5 acceptance.**
S4b #462 is merged. #440 has not supplied a verified Runtime/environment and no
production adapter is enabled here. The independent real loading, discovery,
read/write isolation, network and result-return tests remain NOT RUN. Do not
merge/close #445 as fully delivered using these synthetic contracts alone.

## Implemented contract

`SkillContextProvider` accepts an existing Worker store and a registered Skill
job ID, never an arbitrary package directory or URL. Before preparing or reading,
it resolves protected job ownership, verifies original Worker files and the
publication receipt, then uses the existing TaskSkillLock preflight to verify
scope, snapshot, loader, exact package digest, retention pins and current
availability/revocation. No current Team/default/latest lookup is used.

A server-owned `ContentAuthority` is an internal dependency. It must independently
validate its real adapter and actual binary/version/config/model/session on every
operation; the returned `ContentSession` identity is a record, **not a verifier**.
It must not be constructed from public job/request metadata or model output.
Default construction uses `ClosedContentAuthority`: there is no configurable
allow flag, test authority registry, or supported Runtime allowlist in this PR.
Tests inject an explicitly identified `contract_fixture` authority. Those values
must never be treated as #440 conformance or real Codex support.

The normal `SkillContextProvider(workers, job_id)` construction uses that closed
server-side authority. It does not alter `run_job` or open the lower CLI/session/
fake helpers. Real Worker prompt/env/CLI integration is still required after
#440's adapter decision; this is not an automatically running loader. No process,
HTTP, MCP, model call or alternate script runner is added.

## Exact content, catalogs and resources

Preparation stores a private complete generation-input object under Operator
storage outside the Worker root. Required SKILL.md bytes (including frontmatter)
are preserved as UTF-8 text with their exact byte digest. Task-required methods
are included even when their binding activation is on_demand. Optional packages
initially contribute only their name, description, exact identities and fixed
resource manifest. Resource paths remain relative to the package; no mutable
source URL/path or user directory is used as a resource read target.

The internal adapter input includes original immutable Worker inputs for binding
and audit. It is **not** a ready-to-send prompt or a public API payload. A future
verified adapter must transform its input into the supported controlled injection
and resource channel without exposing Operator/private source paths to the model.

Each controlled broker read takes a context reference, bound Skill ID, relative
resource and logical call ID. Unbound packages, traversal, unknown resources,
changed scope/session, missing pins, revoked/unavailable versions or changed
bytes fail. Revocation and adapter identity are rechecked after byte capture
before a successful return. This does not promise erasure of bytes already
returned before a later revocation.

Complete serialized original input plus required/catalog content has a bounded
byte budget. The catalog, package count and individual resource also have limits.
An oversized value is rejected without truncation. This is **not** a tokenizer
or a claim that every model context window has been validated; actual adapter
budget enforcement remains part of verified Runtime integration.

Unknown descriptive extensions are preserved with warnings. Runtime-specific
fields (including allowed-tools, hooks or model switching), dynamic command
syntax and empty instructions block the portable contract. Scripts may remain
fixed readable resources, but nothing here runs them or installs dependencies.

## Recovery and truthful evidence

A new context re-prepares required content from the original task snapshot. Same
context replay checks the same immutable input, budget and session, and reads
current authority again. Atomic publication prevents a partially written context
from becoming readable; conflicting/corrupt published records are not repaired.
Original mutable research sources are not reopened.

`describe` is a narrow read-only preparation projection: references, version
identities and `prepared_only`. It contains no private bodies, credentials,
commands or storage paths. **materialized, catalog_presented, content_delivered,
resource_read_observed and script_execution_observed stay unobserved.** Merely
writing a private JSON object is not materialization in a Runtime-visible tree.
A broker receipt records `broker_read_completed=true` separately from
`runtime_read_observed=false`. A model-written claim/JSON event cannot advance
these states. No synthetic result is shown as real loading or adherence.

This projection is a server-side contract, not yet a new frontend execution
panel. No Work conversation message is emitted for each resource read.

## Still required in this same S5 issue

The #440-approved actual adapter, version-specific prompt/environment/CLI
integration, isolated package materialization and authenticated resource transport,
independent context-bound delivery/read observations, final report return, and
minimal execution-detail UI are still required. They must pass their real
isolated smoke with an explicitly authorized Runtime, credential source and
call/cost boundary. Until then #445 remains Open and its PR remains Draft; no
strict-binding activation, deployment or #449 completion follows from this code.

## Verification

Tier 3/4: real package/library/task/Worker persistence plus a test-only authority,
negative cases, replay, byte budgets, concurrency and interruption. Existing
forbidden Runtime helpers ensure zero provider/fake completion. Run product tests
only in the authorized CI channel: full backend/Ruff and the unchanged isolated
HTTP/browser suite. Exact head/base, logs, artifacts and any failure belong in the
PR. These fixtures are not a replacement for the missing live acceptance.
