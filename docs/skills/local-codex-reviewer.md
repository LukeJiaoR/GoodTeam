# Bounded local Codex Reviewer

This is a self-use execution adapter for the existing MBP Codex CLI and its
configured Responses provider. It can execute a captured, read-only review and
return a validated advisory report through the existing Worker, HTTP, retry and
cancellation paths. It does not implement strict bound Skill execution or close
#440/#445. A local report is labelled `local_cli`, never `verified_runtime`.

The default service remains closed. An operator must explicitly supply a
task-bound startup exposure policy. No request JSON, package directive,
environment variable or saved PASS record enables this adapter.

## Observed MBP prerequisites

On 2026-10-02 the native CLI is **Codex 0.160.0, macOS ARM64**, with SHA-256
`112fae7a5a1223e673c8a1791d32338f37df8b527ff1159bb8adac6c4dbf1b4b`.
The existing Codex configuration selects a custom HTTPS Responses provider and
contains its authentication material. GoodTeam captures only allowlisted
nonsecret routing metadata and configuration/binary digests. Native Codex reads
the original configuration and login in place; GoodTeam does not copy the
bearer into arguments, environment, staging, policy files or reports.

The existing Docker Desktop 29.4.1 and pinned Linux ARM64 image are also present.
Their existence does not supply a dedicated provider route, monetary quota,
native discovery proof or complete strict admission. The Linux adapter is
unchanged and still requires its independently verified host/provider authority.

## Relationship to #445

The missing prerequisite is an owned, task-bound execution path from startup
authority to a real provider result. This adapter uses #445's captured source,
Reviewer protocol, Worker, parent transport, durable report, cancellation and
retry paths; it does not build a second task/report system. The authority factory
also leaves the pinned Linux adapter's default admission unchanged. A no-Skill
sample can expose native configuration and result-channel incompatibilities
without giving an unverified host authority to execute a user's bound methods.

This prerequisite must not substitute for #445 acceptance. Strict bound Skills
still need these minimum actual capabilities:

- A dedicated existing provider route whose external authority enforces model,
  request/spend limits and retires each owned request context. The local CLI
  cannot enforce or verify this by itself.
- The pinned Linux container's independently verified filesystem, mount,
  discovery and egress envelope, including adversarial native checks. Docker
  and a cached image alone do not provide admission evidence.
- A live required-method delivery and optional-resource read witness through
  that envelope, with cancellation/cleanup and retry verified using its real
  provider authority. Local text-only reports cannot establish these facts.

Keep #440/#445 open until those checks succeed. No custom method is stripped,
replaced or silently downgraded to make this local path run.

## Execution and cost boundaries

| Boundary | Local adapter behavior |
| --- | --- |
| Task and Worker root | One exact task kind/project/logical ID, root, model and protected snapshot. |
| Methods | Captured default Reviewer protocol with zero methods. Any bound package, required Skill or unresolved/custom-empty selection is rejected. |
| Contexts | 1–4 explicitly accepted fresh CLI contexts; consumed durably before invocation, including failed preparation. Cross-process allocation uses the existing anchored file/flock primitives. |
| Deadline | 1–120 seconds per owned process; cancellation/revocation is polled while writing input, capturing output and awaiting exit. |
| Input/output | Complete input up to 1 MiB; existing bounded stdout/stderr/report capture. Overflow fails without truncation. |
| Invocation | Codex 0.160.0, read-only sandbox, low reasoning, empty working directory, ephemeral new session. Startup checks native feature gates with model-free `features list`; shell, code mode, apps, plugins/hooks, daemon startup, browser, image and agent gates must be off. Per-invocation MCP/discovery/notification restrictions are also supplied. Any observed tool item invalidates the report; this does not undo a tool action or independently prove tool absence. |
| Credentials | Existing user-owned private config and login files; sanitized child environment. No new login or persistent credential. |
| Retry | Explicit existing retry route, original captured sources/task/model, fresh context and another quota allocation. Replays never launch again. New authorization cannot silently replace an old task's provider/binary/invocation policy. |
| Cancellation | Parent requests stop and confirms its process-group cleanup before accepting a terminal result. Unconfirmed cleanup stays `stopping`; no successful report or retry permission. |
| Provider requests | **No enforceable hard ceiling.** CLI request/stream retries are disabled; this is not proof of one upstream request per context. A requested request-count ceiling is rejected. |
| Money | **No verified price or USD ceiling.** `usd_limit` must be null and unknown exposure must be explicitly accepted. Time, byte and context limits are not monetary caps. |
| Remote retirement | **Unsupported.** Killing the CLI cannot revoke work already accepted by the provider. No quota is refunded on failure/cancellation. |
| Host confinement | Invocation restrictions are a local candidate, not independently verified native containment/discovery/egress proof. Strict methods remain unavailable. |

Limits are recorded in the original protected session and exposed as
`runtime_boundaries` in execution detail, including null request/USD ceilings,
`strict_skill_binding=false` and `provider_retirement_supported=false`.
`content_delivered` is the existing parent-to-CLI pipe receipt, not an upstream
submission witness. Resource/script stages remain unobserved.

## Inspect without model calls

Supply the real absolute executable and existing Codex home. Symlinked
executables/configuration roots are rejected; select the actual release binary.

```sh
goodteam local-codex-inspect \
  --codex /absolute/native/codex \
  --codex-home /absolute/existing/codex-home \
  --model gpt-6.1-sol
```

Inspection reads nonsecret configuration metadata, hashes the binary and runs
`--version` and model-free `features list`. It never submits a model request, verifies the account/model
connection or approves spend. Preserve its `selection_digest` for the reviewed
configuration. Native/tool/provider acceptance still needs an explicitly
authorized isolated model smoke.

The CLI loads existing private configuration in place. This local adapter does
not use `--strict-config`: the MBP config contains legacy
`disable_response_storage` metadata rejected by that mode. It pins the complete
config digest and checks effective feature gates instead of editing that user
file. Native 0.160.0 always normalizes the unified-exec backend on unless managed
requirements override it; `shell_tool=false` controls the shell gate. Removed
patch/other compatibility flags are not treated as proof that tools are absent.

## Explicit startup policy

Store this nonsecret policy **outside the Worker root**, in an operator-owned
canonical directory, with mode `0600`. Select an expiry in the next 24 hours.
The initial example deliberately does not grant unknown-cost exposure. Set
`accept_unknown_cost=true` only after the operator accepts this provider/model
and the above limitations. Do not supply raw secrets.

```json
{
  "schema_version": "goodteam.codex-local-policy.v1",
  "authorization_id": "one-reviewed-local-batch",
  "worker_root": "/absolute/isolated/registered-repo",
  "task": {
    "kind": "direct_task",
    "project_key": "DEMO",
    "logical_id": "bounded-review-1"
  },
  "codex": "/absolute/native/codex",
  "codex_home": "/absolute/existing/codex-home",
  "model": "gpt-6.1-sol",
  "selection_digest": "sha256:<digest-from-inspection>",
  "max_contexts": 1,
  "deadline_seconds": 60,
  "expires_at": 0,
  "accept_unknown_cost": false,
  "provider_request_limit": null,
  "usd_limit": null
}
```

Expiry, policy, executable inode/ctime/mtime/mode and provider/config identity
are checked during execution. Removal or editing revokes the exposure. Context
receipts live under the existing Operator store's `codex-local-budgets/`; task
selection pins live under `codex-local-task-bindings/`. A reused authorization
ID with changed content conflicts; restart cannot reset consumed contexts.

Use a registered isolated project with default Code Review methods. Custom
method requests are rejected, not stripped or downgraded. Direct dispatch
captures the exact S6 protocol even when the project otherwise uses legacy
defaults. Other subjects, older contracts and custom-empty selections retain
their existing unresolved-method behavior.

```sh
goodteam dispatch --project-root /absolute/isolated/registered-repo \
  --track 'code_review:codex:Review this exact change' \
  --task-id bounded-review-1 --review-base <exact-base-sha> \
  --review-head <exact-head-sha> --review-model gpt-6.1-sol \
  --local-codex-policy /absolute/operator/local-review.json
```

The same policy may be explicitly composed into a foreground server:

```sh
goodteam serve --workspace /absolute/isolated/workspace \
  --local-codex-policy /absolute/operator/local-review.json
```

For Web use, bind the policy to the existing captured workflow task identity.
Current-run validation and queued run/retry use #473's workflow guard. A stale
HTTP request cannot allocate a context for the replacement task. Cancellation
uses the independent existing bound route. A legacy Worker without the
protected task protocol is rejected before CLI execution. Automatic workflow,
managed restart/supervisor composition and general coding Workers are outside
this adapter.

## Evidence and acceptance

The implementation tests use a clearly model-free executable that emits
native-shaped JSON events, real pipes/process groups, durable task/context
records and the real loopback HTTP server. They verify the control-plane
contracts, not native Codex, provider connectivity, discovery containment,
pricing, model quality or #445's optional-resource read acceptance.

Native metadata inspection is separate evidence: installed version/digest,
existing routing configuration and effective native gates were checked without
model execution. On 2026-10-02 the user authorized one existing-route
`gpt-6.1-sol` smoke, one fresh context, a 60-second deadline and no automatic
retry. The initial native invocation failed after about 0.26 seconds with
`review_process_failed`; no thread/turn event, usage or report was received.
An offline schema check found legacy config incompatible with `--strict-config`;
the adapter was corrected without editing the config. The spent context was
not refunded, its policy was revoked, and no second model invocation was made.
The corrected command still needs a separately authorized native smoke.

Public lookup did not establish a price or account-level monetary cap for the
configured custom route. No unrelated vendor tariff is substituted. One
context, low reasoning, byte limits and 60 seconds are meaningful execution
limits, not a verified dollar ceiling. No strict Runtime acceptance, merge,
deployment or new account is implied.

Version-specific options are checked against the
[official 0.160.0 configuration schema](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/config.schema.json).
Schema validity is an implementation prerequisite, not observed native tool
isolation. This branch stacks on #473; #474 is independent and is not included.

Native normalization and shell gating were checked against the official
[managed feature implementation](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/config/managed_features.rs)
and [tool configuration](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/tools/src/tool_config.rs).
