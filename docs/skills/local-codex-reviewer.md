# Local Codex Reviewer candidate — production blocked

**Production admission is blocked** with
`local_codex_effective_config_unverified`, before any native subprocess, server
listener or context allocation. This PR has not connected real model execution.
A private startup policy, user-config digest and successful `features list` do
not prove the native process's effective provider or complete MCP inventory.
#440/#445 remain open.

The candidate retains task/quota, parent transport, advisory report and
cancellation/retry mechanics on the existing #445 pipeline. Positive tests use
an explicit model-free `contract_fixture` authority. No policy JSON, package,
environment variable or saved PASS record enables production execution.

## Effective configuration blocker

Native 0.160.0 loads package, MDM, system, cloud, user, profile, cwd/tree/repo and
runtime layers. After merging it applies required provider definitions;
required provider selection takes precedence over CLI flags. The user-config
digest can stay unchanged while a managed same-alias provider changes
URL/auth/retries. Feature states do not attest that route.

Recursive TOML table merging preserves inherited MCP entries when an override
is empty. Disabling only user-config server names misses other layers.
Orchestrator MCP gating does not close native MCP. A transport can start before
a report item, so post-hoc tool rejection cannot undo it.

The production gate rejects unconditionally until an execution-bound effective
configuration mechanism is implemented and verified. It does not guess that
other layers are absent, override managed requirements, or accept a JSON PASS
witness. The test-only authority accepts only its marked model-free executable
and labels successful reports `contract_fixture`.

The findings follow the official version-pinned
[loader](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/loader/mod.rs),
[state merge](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/state.rs),
[recursive merge](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/merge.rs)
and [provider selection](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/config/mod.rs).

## Observed MBP prerequisites

On 2026-10-02 the installed native CLI was **Codex 0.160.0, macOS ARM64**, SHA-256
`112fae7a5a1223e673c8a1791d32338f37df8b527ff1159bb8adac6c4dbf1b4b`.
The user config selects a custom HTTPS Responses provider and contains its
existing authentication material. The designed CLI invocation reads that
private config/login in place; it does not copy secrets into arguments,
environment, staging, policy or reports. That user-file selection is a candidate,
not proof of the actual managed/effective route.

Docker Desktop 29.4.1 and the pinned Linux ARM64 image are present. Their
existence does not provide a dedicated route, monetary cap, discovery proof or
complete admission. The existing Linux adapter remains closed without its real
host/provider authority.

## Relationship to #445

The missing prerequisite is an owned execution path from startup authority to a
real provider result. The candidate uses #445's captured source, Reviewer
protocol, Worker, parent transport, durable report and cancellation/retry paths.
It does not build another task/report system. A method-free sample can expose
native incompatibilities without granting unverified host access to bound
Skills. The mechanics are staged here; the effective-config blocker means this
PR is **not yet a usable self-use execution route**.

Strict Skill acceptance still needs externally enforced provider
model/request/spend/retirement authority, independently verified pinned Linux
filesystem/discovery/egress containment, and live required-method delivery plus
optional-resource read and cancellation/retry evidence. No custom method is
stripped or silently downgraded to make the candidate pass.

## Candidate boundaries

These mechanics are exercised only by the explicit model-free contract authority.
The production gate rejects before they grant native execution.

| Boundary | Staged behavior |
| --- | --- |
| Scope | Exact task/root/model and protected snapshot; fresh read-only Reviewer only. |
| Methods | Captured default S6 protocol may have zero methods. Any bound Skill, required method, unresolved/custom-empty selection or legacy Worker fallback is rejected. |
| Quota | 1–4 accepted contexts, durably consumed before invocation; process-safe allocation, no refund after failure/cancellation, restart cannot reset it. |
| Retry | Explicit retry, original source/task/model/selection and a new context allocation. Replay never launches again. |
| Deadline | 1–120 seconds; cancellation/revocation while writing, reading and waiting; unconfirmed owned process-group cleanup stays `stopping`. |
| Bytes | Complete input up to 1 MiB and existing bounded result/report channels; overflow fails without truncation. |
| Invocation | Empty cwd, ephemeral session, read-only sandbox, low reasoning; reported native gates inspected without a model turn. Restrictions are candidates, not independently verified complete tool/host isolation. |
| Results | Source-bound validated advisory report; no approval/gate/merge/deployment transition. Any tool item invalidates the report but does not undo actions. |
| Provider requests | No proven hard ceiling; retries off does not prove one upstream request. Requested request-count ceilings are rejected. |
| Money | No verified tariff or USD cap. Unknown exposure must be accepted; time/byte/context limits are not a dollar ceiling. |
| Retirement | Remote retirement unsupported. Closing/killing the local transport does not revoke accepted provider work. |

Original protected session boundaries are exposed as `runtime_boundaries`,
including null request/USD ceilings and false strict/retirement flags. A fixture
session retains `contract_fixture` provenance. Pipe delivery is not an upstream
submission witness; resource/script stages remain unobserved.

## Inspection is not admission

```sh
goodteam local-codex-inspect \
  --codex /absolute/native/codex \
  --codex-home /absolute/existing/codex-home \
  --model gpt-6.1-sol
```

The helper hashes the binary/user config, checks private login file metadata,
and runs model-free version/features commands. It does not validate connectivity,
spending permission, full native configuration or model quality. It explicitly
returns `admission_ready=false` and `effective_config_binding=unverified`.
`selection_digest` identifies only the user-config candidate.

The candidate omits `--strict-config` because existing legacy
`disable_response_storage` metadata is incompatible with that mode. This does
not resolve the effective-layer blocker. The user config is never edited.
Native unified-exec normalization also ignores user opt-outs; reported
`shell_tool`/other states are checked instead. Removed flags are not tool-absence
proof. See the official
[configuration schema](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/config.schema.json)
and [managed feature implementation](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/config/managed_features.rs).

## Candidate startup policy

The strict schema includes task/root/model, real binary/home paths, the candidate
selection digest, `authorization_id`, finite contexts/deadline, expiry, explicit
unknown-cost acceptance and null request/USD caps. The private file must be
outside Worker storage. **Even a valid policy cannot enable execution now.**
Both `serve --local-codex-policy ...` and direct dispatch with that flag fail
with `local_codex_effective_config_unverified` before listening or invoking a
native process. Arbitrary JSON witnesses remain invalid.

In fixture tests only, original selection pins and quota receipts live in the
existing Operator store's `codex-local-task-bindings/` and
`codex-local-budgets/`; changed policies cannot reset consumed quotas. Neither
fixture authority nor a test executable is registered as production admission.

## Evidence and remaining work

Model-free executables use real pipes/process groups and loopback HTTP. They
verify task/quota/retry/cancellation/report contracts, not real provider or
native containment. Adversarial extra-layer fixtures keep user config and
feature metadata green while managed/required-provider or unknown MCP content
drifts; production still rejects before any child or quota allocation.

The user authorized one existing-route `gpt-6.1-sol` native trial on 2026-10-02:
one context, 60 seconds, no automatic retry. It failed in 0.262 seconds with
`review_process_failed`, no thread/turn events, usage or valid report. The
source remained unchanged; the context stayed spent and its policy was revoked.
An offline legacy-config incompatibility was found; no successful connection or
unique raw diagnostic is claimed. No second model invocation was made. The
later effective-config review is a separate unresolved blocker.

Public lookup did not establish the custom route's tariff/account cap. No
unrelated vendor rate is substituted. USD charge and upstream request count
remain unknown. This branch stacks on #473; #474 is independent and excluded.

## Smallest supported next path

Official app-server `config/read` supports cwd, merged config, origins and
layers; `thread/start` supports an ephemeral, provider-specific read-only
session. Startup reloads config, its response exposes the provider alias rather
than a bound URL/auth/MCP snapshot, and a Responses socket can prewarm before
the response. Merely switching to one app-server process does not establish
complete pre-transport binding. No native session was started to inspect it.
See the official
[config service](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/config_manager_service.rs),
[thread protocol](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs)
and [thread startup](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/request_processors/thread_processor.rs).

Continue on #445's existing pinned Linux envelope on the MBP. Inspect its
immutable configuration sources/fixed inputs; add only a temporary task-owned
provider bridge with host-side authentication and actual upstream request
limits. Honor real managed requirements; incompatible routing needs a specific
provider/account decision. Mock-server tests must distinguish local transport
termination from remote work retirement. Unsupported USD caps/remote retirement
remain explicit. That bridge is a **separate unimplemented increment**, not
proof that this PR connects models.

No network/security setting, persistent credential, Dodo call, merge or
deployment is changed. If bridge wiring needs setup, its exact targets and risks
must be reviewable before requesting approval. Paid smoke needs its own unused
one-shot allowance after binding is independently verified.
