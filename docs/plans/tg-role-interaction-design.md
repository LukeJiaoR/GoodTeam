# Telegram Role Interaction / Role Inbox Design

Status: product design, proposed
Date: 2026-07-05
Scope: design only, no runtime behavior change

## 1. Summary

Telegram can eventually become more than an approval channel: it can become a
mobile doorway into GoodTeam's role system. The operator should be able to ask a
specific role a question, leave a role-scoped instruction, or request a compact
role answer without opening the dashboard.

This must not be mixed into the Telegram approval safety work. Approval actions
are high-trust gate mutations. Role interaction is a separate capability with a
different risk model: it starts as a message/inbox feature and only later becomes
an explicit worker-dispatch feature.

Recommended sequence:

1. Ship Telegram approval context binding first.
2. Add passive run-scoped role inbox through the existing message bus.
3. Add explicit role answer dispatch after permissions, audit, and cost controls
   are defined.

Follow-on group-chat design:

- `docs/plans/tg-group-chat-collaboration-design.md` extends this role-inbox
  model into a future Telegram group collaboration surface with multiple role
  bot identities in one group.
- That design depends on this document's run-scoped role inbox. It is not a
  global role inbox and does not change the M1 rule that 0 live sessions must
  not write to the message bus.

## 2. Current State

| Capability | Status | Notes |
| --- | --- | --- |
| GoodTeam role definitions | Shipped | Roles are defined in `src/goodteam_os/role_prompts.json` and loaded via `roles.py`. |
| Run-scoped message bus | Shipped | `messages.py` requires a non-empty `run_id` and stores messages at `state/projects/<project>/messages/<run_id>.jsonl`; it supports `ask`, `assert`, `reject`, `propose`, `from_role`, `to`, `gate`, `refs`, and `in_reply_to`. |
| Debate messages between roles | Shipped / partial | Debate uses the message bus for gate-focused structured role messages. |
| Telegram approval decisions | Shipped | Telegram can approve, reject, request changes, review items, and write audit metadata. |
| Telegram command to ask one role | Not shipped | No `/gt ask <role> ...` or role-targeted inbox command exists today. |
| Telegram-triggered role worker answer | Not shipped | No safe path exists for Telegram to dispatch a role and return its answer. |
| Role inbox UI | Not shipped | Web can surface related messages in approval contexts, but there is no general role inbox product surface. |
| Telegram group-chat collaboration | Proposed | See `docs/plans/tg-group-chat-collaboration-design.md`; multiple role bot identities are a future UX layer over one GoodTeam control plane, not shipped runtime behavior. |

Available role keys today:

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

## 3. Product Goal

Let the operator interact with a specific GoodTeam role from Telegram while
preserving GoodTeam's control plane:

- The run-scoped message bus remains the owned ledger for role-directed messages.
- Workers remain the only mechanism that can produce role-backed answers.
- Approval gates remain separate and human-controlled.
- Telegram never becomes arbitrary remote execution access or an unbounded agent loop.

Important scope constraint: M1 is not a global role inbox. It can only record a
message when GoodTeam can bind the Telegram command to `project_key + run_id`
through a live approval card/session. A global "message architect outside any
run" inbox would need a separate product and storage design.

## 4. User Stories

| User story | Example | Product value |
| --- | --- | --- |
| Ask a role a question | `/gt ask architect why did you choose SQLite first?` | The operator can clarify design decisions from a phone. |
| Leave a role instruction | `/gt tell qa focus regression on login and billing` | The next relevant QA task can pick up human guidance. |
| Ask for a compact role answer | `/gt ask qa summarize smoke failure` | The operator gets a short answer without opening Web. |
| Reply to an approval card and ask a role | Reply to a staging card: `/gt ask release what is the rollback path?` | The question inherits project/run/gate context from the card. |
| Audit the conversation later | Web shows the Telegram-origin role question and role answer. | Human decisions remain reconstructable. |

## 5. Interaction Levels

### Level 1: Passive Run-Scoped Role Inbox

Goal: allow Telegram to write a role-targeted message into the existing
run-scoped message bus. No worker is dispatched.

Command shape:

```text
/gt ask <role> <question>
/gt tell <role> <instruction>
```

Behavior:

- Resolve project/run/gate context from reply-to-card when available.
- If no card context exists, use the same disambiguation model as approval
  commands: 0/1/N live sessions in chat.
- If there are 0 live sessions, do not append to the message bus. Reply that no
  run-scoped context is available and suggest using the dashboard or starting a
  run. M1 does not support global role messages.
- Validate `<role>` against known role keys.
- Append a message bus row:
  - `from_role`: `human`
  - `to`: `[<role>]`
  - `kind`: `ask` for `/gt ask`, `propose` for `/gt tell`
  - `gate`: current gate when known, empty otherwise
  - `refs`: approval card/session/artifact references when known, plus
    `telegram:chat:<id>/message:<id>` and `telegram:user:<id>`
  - `body`: user text after applying the existing Telegram masking helper
- Confirm in Telegram that the message was recorded.

Non-behavior:

- Does not trigger model calls.
- Does not modify artifacts.
- Does not advance workflow.
- Does not create tasks automatically.

Example:

```text
你已留言给 architect
项目: CONTENTOPS
门: RFC
内容: why did you choose SQLite first?
```

### Level 2: Role Inbox + Next Worker Pickup

Goal: make future role workers aware of relevant Telegram-origin messages.

Behavior:

- Add a small query helper such as
  `read_role_messages(workspace, project_key, run_id, role_key, gate=None)`.
  It should centralize filtering by `to` / role and optional gate instead of
  forcing worker dispatch code to hand-roll message filtering.
- Worker task/context generation reads role-targeted messages through that
  helper for the current project/run/gate.
- Pickup packets can include a compact "operator messages for this role" section.
- Messages remain advisory context, not direct instructions that override task
  constraints.

Acceptance:

- A `qa` worker dispatched after `/gt tell qa ...` can see the message in its
  context.
- An unrelated role does not receive the message unless addressed or broadcast.

### Level 3: Explicit Role Answer Dispatch

Goal: allow Telegram to request a role-backed answer.

Command shape:

```text
/gt ask qa --answer why did smoke fail?
/gt ask architect --answer summarize tradeoffs
```

Behavior:

- Telegram records the question in the message bus.
- GoodTeam creates a bounded role worker job with read-only or advisory intent.
- The role answer is appended to the message bus as `assert`.
- Telegram sends a compact answer summary and a reference to the full result.

Required controls:

- Explicit operator command; never automatic from ordinary chat text.
- Allowed role list.
- Explicit adapter/model resolution policy.
- Cost/model visibility before or after dispatch, depending on product mode.
- Timeout.
- Read-only default.
- No write access unless a separate server-action policy allows it.
- Audit fields connecting Telegram message, worker job, and returned answer.

Non-behavior:

- No autonomous follow-up loops.
- No role-to-role dispatch chains from Telegram.
- No project-file mutation from a role answer command in the first version.

## 6. Command Design

Recommended first command set:

```text
/gt ask <role> <question>
/gt tell <role> <instruction>
/gt inbox [role]
```

Later command set:

```text
/gt ask <role> --answer <question>
/gt ask <role> --gate <gate> <question>
/gt ask <role> --project <project> <question>
```

Parsing rules:

- Extend Telegram command parsing to recognize `ask`, `tell`, and eventually
  `inbox`.
- Add `_process_telegram_command()` branches for role interaction without
  touching the approval decision path.
- Role names must match known role keys or documented aliases.
- A reply-to-card command inherits card context.
- A bare command uses live-session disambiguation when context is ambiguous.
- A bare command with 0 live sessions does not write a message.
- Unknown role returns a role list hint.
- Empty body returns usage help.

Role aliases can be added later:

| Alias | Role key |
| --- | --- |
| `pm` | `orchestrator` or `product_requirement` (open decision) |
| `product` | `product_requirement` |
| `arch` | `architect` |
| `frontend` | `ui` or `implementation` (open decision) |
| `test` | `qa` |
| `reviewer` | `code_review` |

Aliases are useful, but first implementation should accept canonical role keys
only unless product decides otherwise.

## 7. Context Binding

Role interaction must reuse the Telegram approval safety model:

1. Reply-to-card binding is primary.
2. Bare command with one live session is allowed.
3. Bare command with multiple live sessions requires disambiguation.
4. No sticky active project.
5. Stale disambiguation taps fail safely.

Role messages need context because the same role may be active across multiple
projects or gates. A message such as "is this safe?" is useless without project,
run, and artifact context.

M1 binding rule:

- Bound to one live session/card: append message.
- Multiple live sessions/cards: ask the user to choose.
- Zero live sessions/cards: do not append message; explain that M1 is run-scoped.
- Global role inbox: out of scope.

## 8. Data Model

The existing message bus is a strong fit. Proposed Telegram-origin row:

```json
{
  "id": "msg:...",
  "run_id": "run-123",
  "ts": "2026-07-05T...",
  "from_role": "human",
  "to": ["architect"],
  "kind": "ask",
  "gate": "rfc",
  "body": "Why did you choose SQLite first?",
  "refs": ["telegram:chat:12345/message:678", "telegram:user:111", "artifact:rfc"],
  "in_reply_to": ""
}
```

Potential extension fields for later, if needed:

- `origin`: `telegram`
- `actor_ref`: `telegram:user:<id>`
- `client_context`: chat id, message id, session id
- `delivery_state`: recorded, surfaced_to_worker, answered
- `job_id`: when an answer dispatch is created

Do not add these fields until implementation needs them; the current bus schema
already supports the Level 1 behavior through `from_role`, `to`, `kind`, `refs`,
and `body`.

M1 fixed choice:

- `from_role` must be `human`.
- Telegram actor identity belongs in `refs`, for example
  `telegram:user:<id>`.
- This avoids making downstream role/message UI treat each Telegram user as a
  GoodTeam role.

## 9. Permission And Safety Model

| Action | Risk | First-version policy |
| --- | --- | --- |
| Record role question | Low | Allowed for configured Telegram users. |
| Record role instruction | Medium | Allowed, but advisory only. |
| Dispatch role answer | Medium / high | Behind explicit command and role allowlist. |
| Dispatch role to modify project files | High | Out of scope. |
| Trigger role-to-role loop | High | Out of scope. |
| Approval decision | High | Remains in the separate approval flow. |

Security rules:

- M1 must apply the existing Telegram masking helper before appending user text
  to the message bus.
- The existing masking helper is limited. It handles common operational paths,
  hosts, and IP-like values; it is not a comprehensive sensitive-value detector.
- Expanding sensitive-value detection is a separate hardening task, not part of
  M1.
- Bot credentials stay environment-managed.
- Role answers must not expose hidden local paths, sensitive values, or raw failure logs unless
  masked.
- Telegram must not bypass existing server-action safety gates.

## 10. UX Examples

### Ask Without Dispatch

```text
User:
/gt ask architect why SQLite before Postgres?

Bot:
已记录给 architect
项目: CONTENTOPS
门: RFC
这会作为该角色后续工作的上下文，不会立即启动 worker。
```

### Ask With No Run Context

```text
User:
/gt ask architect should we use Postgres?

Bot:
现在没有可绑定的运行上下文。
M1 只能把问题记录到某个项目运行里；请回复一张审批卡，或先启动/选择一个运行。
```

### Ask With Ambiguous Context

```text
User:
/gt ask qa what should I inspect?

Bot:
现在有多个可用上下文。请选择要关联的问题:
[CONTENTOPS · STAGING] [GOODTEAM · QA]
```

### Unknown Role

```text
User:
/gt ask designer polish the dashboard

Bot:
未知 role: designer
可用 role: architect, ui, implementation, qa, code_review, release...
```

### Explicit Answer Dispatch

```text
User:
/gt ask qa --answer why did smoke fail?

Bot:
已派发 qa 回答这个问题
Job: ...abc123
我会把摘要发回这里。
```

Later:

```text
QA 回答
Smoke 失败在 checkout flow，主要原因是 test user session 过期。
建议: 重新生成测试账号并补一条 session-expiry 回归测试。
完整结果: job ...abc123
```

## 11. Milestones

### M1: Telegram Role Inbox

Scope:

- Add `/gt ask <role> <question>`.
- Add `/gt tell <role> <instruction>`.
- Extend Telegram command parser for `ask` and `tell`.
- Add role-interaction command handling without modifying approval decision
  behavior.
- Validate role key.
- Resolve context through reply-to-card or live-session disambiguation.
- Reject 0-live-session commands without appending to message bus.
- Append message bus row.
- Use `from_role="human"`.
- Put Telegram chat/message/user references in `refs`.
- Apply existing Telegram masking before append.
- Confirm in Telegram.

Acceptance tests:

- `test_tg_ask_role_records_message_to_exact_role`
- `test_tg_tell_role_records_propose_message`
- `test_tg_ask_role_reply_to_card_binds_project_run_gate`
- `test_tg_ask_role_disambiguates_multiple_live_sessions`
- `test_tg_ask_role_rejects_without_live_run_context`
- `test_tg_ask_role_rejects_unknown_role_with_hint`
- `test_tg_ask_role_uses_human_from_role_and_telegram_refs`
- `test_tg_ask_role_masks_body_before_append`
- `test_tg_ask_role_parser_does_not_touch_approval_decisions`
- `test_tg_ask_role_does_not_dispatch_worker`

### M2: Worker Pickup Reads Role Inbox

Scope:

- Include relevant role-targeted messages in worker context or pickup packet.
- Add `read_role_messages(...)` or equivalent helper before worker integration.
- Keep messages advisory.
- Do not affect unrelated roles.

Acceptance tests:

- `test_worker_pickup_includes_targeted_operator_messages`
- `test_worker_pickup_excludes_messages_for_other_roles`
- `test_worker_pickup_preserves_role_message_refs`
- `test_read_role_messages_filters_to_role_and_gate`

### M3: Explicit Role Answer Dispatch

Scope:

- Add `/gt ask <role> --answer <question>`.
- Create bounded read-only worker job.
- Resolve adapter/model through an explicit policy.
- Append role answer to message bus.
- Send Telegram summary when complete.

Acceptance tests:

- `test_tg_ask_role_answer_creates_read_only_worker_job`
- `test_tg_role_answer_appends_assert_message`
- `test_tg_role_answer_sends_masked_summary`
- `test_tg_role_answer_rejects_disallowed_role`
- `test_tg_role_answer_timeout_reports_clear_status`

## 12. Non-Goals

- Do not include this in Telegram approval context-binding Milestone 1.
- Do not let Telegram modify project files through role interaction in the first version.
- Do not add autonomous multi-agent loops.
- Do not make role answers approval decisions.
- Do not introduce a second message ledger.
- Do not create global role inbox storage in M1.
- Do not build a full chat UI in Telegram.
- Do not add per-role long-running subscriptions in the first version.

## 13. Open Questions

1. Should `/gt tell` use `propose`, or should the message bus add an `instruct`
   kind?
2. Should role aliases be allowed in M1, or only canonical role keys?
3. Should answer dispatch be available for all roles or only read-heavy roles
   such as `architect`, `qa`, `code_review`, and `release`?
4. How should Web show a role inbox: per role, per gate, or in the existing
   approval workbench?
5. Should Telegram-origin role messages fan out to memory after a run completes?
6. Should a global role inbox exist outside active runs? If yes, what storage
   owns it?
7. Which adapter/model should Telegram role answer dispatch use by default?
   Should the operator be allowed to specify adapter/model in the command?
8. How should Telegram display invocation evidence when an answer dispatch uses
   fallback, noop, fake, timeout, or a missing provider?

## 14. Recommendation

Build this after Telegram approval context binding is safe.

The first valuable increment is M1: write Telegram-origin role messages into the
existing message bus. It gives the operator a real "talk to the team" affordance
without dispatch cost, write risk, or new workflow semantics.

Only after M1 is visible in Web and worker pickup should GoodTeam add M3
role-answer dispatch. That second step is powerful, but it needs explicit
permissions, timeout behavior, cost expectations, and answer provenance.
