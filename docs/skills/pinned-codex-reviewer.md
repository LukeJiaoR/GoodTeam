# Pinned Codex Reviewer candidate (#440 / #445, after #467)

Status: **concrete adapter implementation; native/live acceptance NOT RUN**.
This continues the existing Reviewer, not a new Agent runtime, gateway, Skill
source, Role migration, RFC or deployment. The ordinary product composition
still has no admitted provider/host authority and stays closed. Do not close
#440/#445/#446 or describe this change as a usable live custom-Skill review.

## Existing execution path, not an independent parser

`WorkerJobStore.run_job` keeps original task/snapshot/publication/revocation
preflight. Its existing Reviewer selector now selects `PinnedCodexReviewRuntime`
when **server composition** supplies a `CodexReviewAdmission`; otherwise it
retains `ClosedReviewRuntime`. Job/Team JSON, a package, environment variable,
credential path, or a saved `verified=true` file cannot supply this dependency.
The internal construction is `WorkerJobStore(root,
codex_review_admission=operator_owned_admission)`; normal web/CLI construction
without that independent dependency does not gain native permissions.

The adapter reuses `SkillContextProvider`, the locked report protocol and exact
Git source bundle, `ReviewProcess`, `ReviewResultStore`, cancellation and retry.
It creates no substitute result, does not copy current Team methods, and never
changes approval, merge, deployment or memory-writeback state. It uses generic
`codex exec`, not `codex exec review` and its vendor review methodology.

## What is implemented

- Fixed **Codex 0.155.1 / Linux ARM64**, binary SHA-256
  `298d3d73d0bbc1367e58a370df5b6216fe30ce0a92e8b6b0afb0377a958dc335`.
  The command uses the pinned version's strict config, fresh ephemeral session,
  JSONL, ignored user config/rules and explicit model. Required content and
  original task/source data remain complete; oversized input fails, not trims.
- Explicit config disables bundled instructions, plugins/hooks, additional
  agents, MCP orchestration, remote model discovery, browser/search and relevant
  optional services. Config is checked against the pinned source schema. That
  check is **not** proof the native installed CLI or its isolation works.
- A standalone adapter pre-exec guard checks Linux/aarch64, UID/GID65534,
  physical read-only image/input filesystems, the actual executable digest and
  root ownership, a fresh exact environment and absent native config/discovery
  roots. It then execs only the pinned CLI. It emits no synthetic PASS/events.
  Guard source is included in the configuration digest. Reviewed `SKILL.md`
  remains source data under `sources/base` or `sources/head`, not a discovered
  method. Static checks alone do not eliminate prompt injection or prove the
  native inventory, explicit invocation and escaped-descendant cases.
- Only a fresh input directory is mounted read-only at `/review`. Operator,
  source checkout, credentials, host HOME/config and Docker socket are not
  mounted. Inputs are0444/directories0555 for container UID access beneath an
  unmounted0700 Operator parent. Stored package scripts are nonexecutable;
  this alone **does not** prohibit interpretation by a shell/interpreter.
  Separately authorized script/tool restrictions still require native admission.
- The concrete container uses an exact existing image and dedicated internal
  bridge/provider-container identity, no pull/build, no host network/privilege,
  cap-dropALL/no-new-privileges, deny-default pinned seccomp, user65534,
  read-only root, bounded tmpfs/PIDs/memory/swap/CPU, no healthcheck/restart/log
  persistence. DNS has no external resolver and only the explicit admitted
  provider alias/IP is mapped; IPv6 is disabled in the container. Complete
  actual inspect payloads are checked before attachment. Docker sends the JSON
  seccomp contents, not the client's profile filename, to its daemon.
- Prospective context/name is durably recorded before create; exact ID/name/
  image/owner are checked before cleanup. Create timeouts resolve only that
  owned name. `docker start --attach --interactive` uses the existing bounded
  parent transport. After its normal/error exit, the adapter stops the exact
  owned container if necessary, verifies stopped/PID0, removes it, checks
  absence, and retires the provider context **before** accepting any report.
  Uncertain engine/route retirement remains stopping, with no report or retry.
  A daemon permission error is not a reason to change host permissions.
- The first admitted execution pins the native contract to the original
  TaskSkillLock in protected Operator storage. New contexts/retries cannot
  silently change image, guard, network/provider policy or model. This is a
  runtime pin at first admission, not a claim those fields existed in older
  task-start records. Existing saved results recover without reacquiring a
  provider route or launching another process. Interrupted native containers
  are not automatically reattached; uncertain state remains blocked.

## Preparation failure and recovery

Reviewer ownership is now persisted before `CodexReviewAdmission.acquire`, not
only before container creation or report preparation. A lost acquisition reply,
unconfirmed unstarted-route retirement, or crash before `attempt.json` exists
must preserve an unresolved context and prevent a second reservation. A fresh
Worker object, default server construction, overlapping dispatch or a retry
with another Worker ID cannot erase that uncertainty. No model is launched to
"test" whether the earlier route has stopped.

An explicit replay is possible after the owning parent successfully retires a
conclusively unstarted lease. Its protected retirement receipt is bound to the
exact preparation, snapshot and context; it is not a public admission flag and
cannot itself retire an external provider route. Existing begun-attempt report,
cancellation and recovery semantics are unchanged. Unresolved acquisition still
requires actual operator/provider reconciliation; this change does not invent
an authorized provider implementation or enable normal Web/CLI dispatch.

## Resource-read evidence

The decoder requires paired native `command_execution` start/completed events
with stable item IDs and commands. Only a successful exact
`cat -- /review/methods/<skill_id>/<relative-path>` (optionally one non-login
bash/sh `-c` wrapper) with complete output equal to the locked package resource
is recognized. Prose/JSON inside an agent message is not an event. Aliases,
shell expressions/substitutions, relative paths, multiple files, login shells,
unknown versions and truncated/normalized output remain unobserved.

Receipts bind item IDs, both event hashes, transcript hash, context/session,
Skill/version/package/resource digests and byte count. They contain **no method
body**, credentials or physical Operator path. They are written with the
immutable report outcome, recovered with it, and checked against the original
package bytes on reads. Cancellation excludes late reports and their receipts.
A collapsed execution-detail section shows narrow receipt identities; frontend
rejects mismatched context/method/session receipts. No resource-read chat spam.

The exact byte match is evidence of returned resource content, **not model
comprehension, adherence, report correctness, script execution or live Runtime
conformance**. Fixture provenance is preserved on every receipt and in the UI.
Reads from a failed/unfinished stream are not promoted into completed-report
receipts. `content_delivered` is complete transport input delivery, not proof of
provider submission; such a witness still needs the actual admitted route.

## Admission dependency is intentionally not invented

`CodexReviewAdmission.acquire/check/finish` is a typed **internal dependency**,
not a production implementation supplied by this change. It must independently
validate the actual image/binary/platform, native discovery, confidentiality,
write restrictions, whole-container/escaped-descendant containment, allowed
model/provider route, request/spend reservations and current revocation. It must
reject unknown prices/limits before forwarding; timeouts/output bytes and
`request_max_retries=0` do not cap request count or money. Authentication stays
outside the Worker. No new proxy/service is built to simulate a missing approved
provider path, and no unrelated credentials are read.

An existing approved provider implementation and its integration into normal
server startup are **still missing**. Merely writing an implementation that
returns a lease or `verified_runtime` is not sufficient admission. The included
test authority always returns `contract_fixture` and is not product-registered.

## Verification and observed environment

Exact-source base: `4103f1114199e1c218fb50b0b61376a414aa6add`.

The new tests exercise the real Worker selection, task/content/staging, real
bounded constant child, strict native-shaped events, report/API persistence,
old-result recovery and failed cleanup. Docker inspect/create/stop responses are
scripted, and the attached executable is **not Codex**. Negative tests cover
foreign contexts/methods, changed runtime pins, stale resource digests, corrupt
receipts, wrong mounts/UID/network/DNS/policy, wrong owners, uncertain termination
and unadmitted execution. Browser fixtures use the same concrete adapter with
that explicitly labelled scripted daemon, not a public Runtime switch.

This session's read-only environment attempts are separate from product CI:

- Source capture `35891473495` verified exact main source and named tools.
- Pinned upstream capture `35891752435` used source
  `be2951ea34f0d295ed0becf97079f92fa5f6950e`. The existing x64 CI runner has
  `/usr/bin/docker`, but `docker version` returned permission denied at the local
  daemon socket. No sudo, group, socket, daemon or host-policy change was made.
- Separate hosted ARM64 channel `35892181254` failed before any job steps were
  reported. Its failure cause was not available in returned evidence; do not
  label that a native Codex failure or assume a billing diagnosis.
- The43 selected generated config fields validate against the pinned upstream
  JSON schema (SHA256 `786fee5181619dc73963f7544fa6afe8364236392d999df92d3dd6b3a8c8ac70`).
  Neither source/schema inspection nor mocked inspect responses are native
  ARM64/Docker execution evidence.

Exact final-head product CI, full backend, frontend and browser results belong
in the PR, including any failures. Native ARM64 execution, native discovery and
egress controls, escaped descendants, provider accounting and real custom-Skill
model acceptance remain **NOT RUN / unverified**. #440's nine earlier helper
checks are not substituted for these missing observations.

## Primary sources

- [Pinned Codex exec CLI](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/exec/src/cli.rs)
- [Pinned exec events](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/exec/src/exec_events.rs)
- [Pinned config schema](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/core/config.schema.json)
- [Pinned native roots](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/ext/skills/src/host_roots.rs)
- [Pinned extension discovery requirement](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/ext/skills/src/extension.rs)
- [Docker seccomp profile handling](https://docs.docker.com/engine/security/seccomp/)
