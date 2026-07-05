# Telegram Role Answer Policy Spike

Status: product design, proposed
Date: 2026-07-06
Scope: design only, no runtime behavior change

Parent issue: #253

## 1. Summary

This spike defines the policy boundary for a future Telegram command that asks a
GoodTeam role to produce a bounded answer and return a compact summary to
Telegram.

The future capability is intentionally narrower than general remote execution.
Telegram role answers should be explicit, run-scoped, bounded, auditable, and
advisory by default. They build on the passive role inbox from #251 and worker
pickup from #252, but they do not change either behavior in this PR.

This document does not implement `/gt ask --answer`, worker dispatch, callback
handlers, message bus schema changes, approval changes, or Telegram group-role
identity work. The group-role platform design remains separate in #254.

## 2. Product Boundary

Future role answers are opt-in and explicit. A Telegram user must ask for an
answer through a command shape that is visibly different from the passive role
inbox:

```text
/gt ask <role> --answer <question>
```

Plain role inbox commands remain passive:

```text
/gt ask <role> <question>
/gt tell <role> <instruction>
```

The distinction matters because passive inbox writes are low-risk advisory
messages, while answer dispatch may spend model budget and create a worker job.
GoodTeam should never infer an answer dispatch from ordinary chat text or from a
plain `/gt ask <role> ...` command.

Product rules:

- Telegram must not become arbitrary remote execution.
- Role answers are advisory unless a later workflow explicitly promotes or uses
  them.
- Role answers must not approve, reject, or request changes on behalf of the
  human.
- Role answers must not mutate project files, artifacts, workflow state,
  approval decisions, memory, or external systems in the first answer version.
- The full control-plane state remains in GoodTeam ledgers; Telegram receives a
  compact rendering and references.

## 3. Proposed Command Shape

Recommended future command:

```text
/gt ask <role> --answer <question>
```

Examples:

```text
/gt ask architect --answer why is SQLite still enough for M1?
/gt ask qa --answer what should I inspect before approving staging?
/gt ask release --answer what is the rollback path?
```

Usage behavior:

- Missing role returns usage help and a compact list of allowed roles.
- Unknown role returns an unknown-role error and the allowed role list.
- Missing `--answer` keeps the command in passive inbox mode.
- Missing question after `--answer` returns usage help.
- Repeated `--answer` flags are invalid.
- Flags that look like adapter/model selection are rejected in the first
  version unless a separate policy explicitly enables them.

The first implementation should parse this as one command family rather than a
new top-level verb. That keeps the mental model simple:

- `/gt ask <role> ...` records a question for the role.
- `/gt ask <role> --answer ...` records the question and asks for a bounded
  answer.

## 4. Allowed Roles

Role matching should start with canonical role keys only. Aliases are useful for
Telegram ergonomics, but aliases are out of scope until product decides their
mapping and audit wording.

Canonical role keys currently include:

- `orchestrator`
- `project_intake`
- `research`
- `product_requirement`
- `architect`
- `review`
- `planner`
- `ui`
- `implementation`
- `qa`
- `code_review`
- `release`
- `retro_memory`

Recommended initial allowlist:

- `architect`
- `product_requirement`
- `qa`
- `code_review`
- `release`

Rationale:

- These roles can provide useful decision-support answers without naturally
  needing file writes.
- They map well to questions a human might ask from a phone: scope, design,
  validation, review risk, and release readiness.
- They avoid first-version ambiguity around orchestration, intake, UI
  generation, implementation, research browsing, and memory writeback.

Open safer alternative: start with only `architect`, `qa`, and `release` until
adapter/model cost and audit UX are proven.

Roles outside the allowlist should fail with a policy-denied message, not fall
back to passive inbox behavior. The user can still use plain `/gt ask <role>
<question>` to record a passive message for any canonical role if that feature is
enabled.

## 5. Context Binding

Role answer dispatch must reuse the #245/#251 context model:

1. Reply-to-card wins.
2. A bare command with exactly one live session in the chat may bind to that
   session.
3. A bare command with multiple live sessions requires a chooser.
4. A stale chooser fails safely.
5. A bare command with zero live sessions does not dispatch.

The first answer version must not support global role answers outside a run.
The answer needs `project_key`, `run_id`, and preferably `gate` or artifact refs
so the role can answer in context and so the result is auditable later.

Binding outcomes:

| Context state | Behavior |
| --- | --- |
| Reply to a live approval/status card | Bind to that card's project/run/gate. |
| One live session in chat | Bind to that session and mention the binding in the accepted message. |
| Multiple live sessions | Ask the user to choose; do not dispatch until a fresh chooser selection is made. |
| Stale chooser selection | Report that the context expired; do not dispatch. |
| Zero live sessions | Report that no run context is available; do not dispatch. |

The implementation should record the question in the run-scoped message bus only
after context is resolved. A failed context binding should not write a question
or create a worker job.

## 6. Adapter And Model Policy

Role answers should use existing role/backend resolution. Telegram must not
carry a hidden adapter/model default that differs from dashboard, CLI, or worker
dispatch policy.

First-version policy:

- Resolve adapter/model through the same role-aware configuration used by worker
  dispatch.
- Do not let Telegram users specify adapter/model in the command.
- Show the resolved adapter/model in audit metadata.
- Prefer a fast failure before queuing when the selected adapter is unavailable.
- If the adapter becomes unavailable after acceptance, mark the job failed and
  send a compact failure notice to Telegram.
- If fallback behavior exists in the worker backend, record both the requested
  adapter/model and the actual adapter/model used.

Required visible audit metadata:

- requested role
- resolved role
- requested adapter/model, when known
- actual adapter/model, when different or after fallback
- backend profile or worker backend key, when available
- timeout budget
- failure class and provider error class, when safe to expose

Telegram wording should be compact. Detailed provider errors belong in GoodTeam
artifacts or ledgers after masking, not in chat.

## 7. Timeout And Resource Policy

Role answers must be bounded. They should feel like asking for a short
advisory answer, not starting an autonomous agent run.

First-version resource rules:

- Use a fixed server-side timeout.
- No autonomous follow-up loops.
- No role-to-role dispatch chain.
- No project file mutation.
- No artifact mutation.
- No approval mutation.
- No memory writeback.
- No shell command or server-action access by default.
- One answer job per command.
- One Telegram summary per completed answer, with optional refresh/edit status
  while it is running.

Recommended initial timeout: 90 seconds.

Rationale: 90 seconds is long enough for a compact model answer over existing
run context, but short enough to avoid turning Telegram into a long-running work
launcher. The open decision is whether production should use 60, 90, or 120
seconds after observing real adapter latency.

Timeout behavior:

- If the answer has not completed before timeout, mark it timed out.
- Send a Telegram message that the role answer timed out and no project state was
  changed.
- Preserve the question and timeout metadata for audit.
- Do not automatically retry unless a later policy adds explicit retry controls.

## 8. User-Visible Status Flow

The Telegram flow should be clear without exposing internal machinery.

Accepted:

```text
已收到 architect 回答请求
项目: CONTENTOPS
门: RFC
状态: queued
```

In progress or delayed:

```text
architect 还在整理回答
如果超时，我会在这里说明，不会改变项目状态。
```

Success:

```text
architect 回答 · CONTENTOPS / RFC
SQLite is still acceptable for M1 because it keeps local setup simple and the
current write volume is single-operator. Move to Postgres when hosted multi-user
coordination or concurrent writes become real.

Refs: msg:abc123, job:ans_456
```

Failure:

```text
architect 回答失败
原因: adapter unavailable
项目状态没有改变。
Refs: msg:abc123
```

Rendering rules:

- Keep the Telegram answer compact.
- Prefer a short answer plus one recommendation or next step.
- Link or reference the full artifact/message when available.
- Mask sensitive text before sending.
- Do not expose raw local paths, raw stack traces, bot tokens, provider
  credentials, or unmasked logs.

## 9. Audit Linkage

Every future role answer must be reconstructable across Telegram, the message
bus, and worker/job ledgers.

Required linkage:

- message bus question id
- Telegram chat id
- Telegram command message id
- Telegram user id or actor ref
- bound project key
- bound run id
- bound gate, when known
- bound approval/session/card refs, when known
- worker job id, if implementation dispatches one
- role answer message id, if appended back to the message bus
- adapter/model metadata
- timeout metadata
- failure class, when failed

Recommended refs shape:

```text
telegram:chat:<chat_id>/message:<message_id>
telegram:user:<user_id>
project:<project_key>
run:<run_id>
gate:<gate>
session:<session_id>
job:<job_id>
```

If the answer is appended to the message bus, it should use `kind="assert"` from
the answering role to `human`, with `in_reply_to` pointing at the question
message id. This keeps answers visible to future workers and human review without
adding a second conversation ledger.

Open decision: whether message bus `assert` is sufficient, or whether answer
jobs need an additional answer-specific ledger for cost, provider, token, and
retry metadata.

## 10. Failure Reporting

Failure classes should be stable enough for tests, support, and audit.

| Class | Telegram behavior | Audit behavior |
| --- | --- | --- |
| Unknown role | Show allowed role list. | No question or job is created. |
| Role not allowed | Explain answer dispatch is not enabled for that role. | Record policy denial if a security/audit ledger exists. |
| No run context | Explain that a live run/card is required. | No question or job is created. |
| Ambiguous run context | Show chooser. | No job until a fresh choice is made. |
| Stale context | Ask user to retry from a current card/session. | Record stale chooser/session ref when available. |
| Adapter unavailable | Fail before queue when possible. | Record adapter/model resolution failure. |
| Timeout | Say the answer timed out and state did not change. | Record timeout budget and elapsed time. |
| Worker failed | Send compact failure class. | Link to masked detailed failure artifact/log. |
| Policy denied | Say the requested answer is not allowed. | Record denial reason when available. |
| Rate limit | Ask user to retry later. | Record provider/platform rate-limit class. |
| Auth/network | Say the answer could not reach the configured provider. | Record safe provider/network class only. |

The failure message should never encourage the user to paste secrets, tokens, or
private logs into Telegram.

## 11. Security And Privacy Rules

Role answer dispatch inherits the Telegram approval and role-inbox safety model.

Rules:

- Reuse masking for Telegram-origin text before storing or rendering.
- Reuse masking for role answer text before sending it to Telegram.
- Do not expose hidden local paths in Telegram.
- Do not expose raw sensitive logs in Telegram.
- Keep bot tokens and provider credentials environment-managed.
- Enforce Telegram chat and user allowlists before accepting commands.
- Do not let Telegram bypass approval gates.
- Do not let role answers approve, reject, request changes, or advance workflow.
- Do not mutate artifacts or workflow state in the first answer version.
- Do not allow role answer commands to run shell commands or server actions.
- Do not allow a role answer to dispatch another role.
- Treat all Telegram-provided text as untrusted input.

Privacy note: Telegram is a third-party chat surface. The compact answer should
include only what is useful for mobile decision support. Full evidence, raw
logs, and detailed artifacts should remain in GoodTeam-owned storage and be
referenced rather than pasted into chat.

## 12. Recommended Implementation Phases

### Phase A: Policy / Doc Only (#253)

This PR. No runtime change, tests, schema, worker dispatch, or Telegram command
changes.

### Phase B: Queue Bounded Read-Only Answer Jobs

Add explicit `/gt ask <role> --answer <question>` parsing and dispatch a bounded
read-only worker job only after context binding succeeds. Enforce role allowlist,
adapter/model resolution, timeout, and no-write policy.

### Phase C: Append Answer To Message Bus

Append the answer as `kind="assert"` from the answering role to `human`, linked
to the original question with `in_reply_to`. Preserve Telegram, job, adapter, and
timeout refs.

### Phase D: Compact Telegram Answer Rendering

Send a short masked answer summary to Telegram with refs to the full message or
artifact. Keep failures compact and class-based.

### Phase E: Optional UX Enhancements

After audit and cost controls are proven, consider richer UX:

- answer progress edits instead of extra messages
- cost visibility
- manual review before Telegram send
- limited retries
- role aliases
- expanded role allowlist
- Web inbox views for Telegram-origin questions and answers

## 13. Open Decisions

1. Should the initial allowlist be `architect`, `product_requirement`, `qa`,
   `code_review`, and `release`, or the smaller `architect`, `qa`, `release`
   set?
2. Should the first timeout be 60, 90, or 120 seconds?
3. Should answers use existing worker adapters only, or a separate answer adapter
   profile?
4. Should answer messages be stored as message bus `assert` rows, or should they
   also get a dedicated answer-job ledger?
5. Should answer summaries require manual review before Telegram sends them?
6. How should model cost or token use be displayed to the operator?
7. Should adapter/model fallback be allowed, or should any fallback require
   explicit operator visibility before dispatch?
8. Should `planner`, `review`, or `research` enter the allowlist after the first
   version?
9. Should role aliases be added before answer dispatch, or only after canonical
   commands have proven safe?
10. Should timed-out jobs continue in the background for audit-only completion,
    or should timeout cancel them?

## 14. Recommendation

Ship role answers only after the passive role inbox and worker pickup behavior
are observable and trusted.

The first implementation should support only canonical role keys, a small
allowlist, one bounded read-only job per command, existing role/backend
resolution, no Telegram adapter/model flags, no file or workflow mutation, and
complete audit linkage from Telegram command to question message, worker job,
answer message, and adapter/model metadata.

This keeps Telegram useful as a mobile advisory surface without making it a
second dashboard, an execution console, or an approval bypass.
