# Telegram Group Chat Collaboration Design

Status: product design, proposed
Date: 2026-07-05
Scope: design only, no runtime behavior change

## 1. Summary

Telegram can become GoodTeam's mobile collaboration room, not just an approval
notification channel. The target experience is a single Telegram group where
multiple GoodTeam role bot identities appear to collaborate: planner posts the
plan, review comments on risks, implementation reports progress, qa asks for
evidence, and the human operator only sees approval buttons when a real human
gate is reached.

This document is intentionally product-level. It does not add code, tests,
configuration, dependencies, bot credentials, or runtime behavior. It describes a
future path that builds on:

- `docs/plans/telegram-approval-ux-research.md`
- `docs/plans/tg-role-interaction-design.md`
- `docs/plans/tg-multiproject-context-design.md`

The recommended north star is:

- Multiple role bot identities in one Telegram group.
- One GoodTeam Telegram Gateway behind those identities.
- One GoodTeam state model, message bus, approval ledger, and worker dispatch
  policy.
- Approval remains separate from role chat and can only be performed by an
  allowlisted human through the existing approval path.

## 2. Current / Proposed / Open Matrix

Items marked `proposed` or `open` must not be treated as shipped behavior.

| Area | Status | Notes |
| --- | --- | --- |
| Single Telegram approval bot | Shipped | GoodTeam uses one bot credential, one configured chat, long polling, approval cards, callbacks, ForceReply notes, and read-only supervision commands. |
| Telegram approval ledger integration | Shipped | Telegram decisions write through the same approval path as Web and record Telegram audit metadata. |
| Run-scoped message bus | Shipped | `append_message()` requires `run_id`; M1 role interaction can only naturally record messages when a run context is known. |
| Single-role Telegram inbox design | Proposed | `/gt ask <role>` and `/gt tell <role>` are designed in `tg-role-interaction-design.md`; no command is shipped yet. |
| Reply-to-card command binding | Proposed | Commands should bind through the replied card's `message_id`, but this is not fully shipped. |
| Bare-command live-session disambiguation | Proposed | 0/1/N live-session handling is required before mutating text commands are multi-project safe. |
| Multi role bot identities | Proposed | Role bot usernames should be project-scoped, short, and collision-resistant, such as `@contentops_arch_a1b2bot`; they are UX identities routed by one Gateway, not independent GoodTeam systems. |
| Group mention routing | Proposed | `@contentops_arch_a1b2bot why SQLite?` should route to `architect` when a run context can be bound. |
| Group reply routing | Proposed | Replying to a GoodTeam message should inherit that message's project/run/gate context when available. |
| Role-to-role group replies | Proposed | Worker-authored role messages can appear as bot replies in the group, but they must still write through the message bus. |
| Group progress broadcasting | Proposed | Roles can post short progress updates to the group, ideally by editing existing run/status messages when possible. |
| Role answer dispatch | Proposed / later | A Telegram mention can later trigger a bounded worker answer, but only after adapter/model, cost, timeout, and audit policy are defined. |
| Bot-to-bot autonomous flow | Open | Do not rely on autonomous bot-to-bot loops for first implementation. Use GoodTeam orchestration as the source of flow. |
| Guest Bot / temporary participation | Open | Potential future UX option if Telegram platform support and product value justify it. Not needed for M1-M4. |
| Telegram forum topics per run | Open | A forum topic per project/run may reduce noise, but it changes chat topology and should be designed separately. |
| Global role inbox | Open / out of M1-M2 | A message to `architect` outside any live run needs separate storage and product semantics. |

## 3. Product Goal

Let the operator supervise and steer GoodTeam from a Telegram group without
turning Telegram into a second dashboard or an uncontrolled agent loop.

The group should make work feel conversational:

- Humans can ask a role a question by mentioning that role bot.
- Roles can post short progress updates in a readable thread.
- Review can object, ask for evidence, or clear a step.
- GoodTeam can advance automatically when no human decision is required.
- When a human gate is required, the group receives a compact approval card with
  explicit buttons.

The group must also be safe:

- Every role interaction is run-scoped in M1/M2.
- Ambiguous messages are not silently routed.
- Role chat does not mutate approval decisions.
- Role bot identities cannot approve gates.
- All state remains reconstructable in GoodTeam's own ledgers.

## 4. North Star UX

The Telegram group should feel like a workroom with named participants:

```text
planner:
PLAN ready · CONTENTOPS / RFC
I split the implementation into 4 tasks.

Next: review should inspect risk and sequencing.
```

```text
review:
Review notes · CONTENTOPS / RFC
No blocker. One caution: migration rollback needs explicit owner.

Next: architect should confirm rollback shape.
```

```text
human:
@contentops_arch_a1b2bot is SQLite still the right first storage layer?
```

```text
architect:
Answer · CONTENTOPS / RFC
Yes for M1. SQLite keeps local-first setup simple and avoids provisioning risk.
Postgres remains the planned scale-out path once multi-user hosting is real.

Refs: RFC storage section
```

When a gate needs human approval, the group should not become a debate thread.
It should present the decision clearly:

```text
Needs your decision · CONTENTOPS / PRD

You are deciding: whether to enter technical design.
Recommendation: approve
Evidence: CI passed · Smoke passed · Blockers 0

[Quick approve] [Review items]
[Request changes] [Reject]
```

## 5. Architecture Principles

### One Gateway, Many Role Identities

Multiple Telegram bot identities are a UX layer. They should not become multiple
GoodTeam control planes.

Recommended model:

```text
@contentops_plan_a1b2bot   -> role key: planner
@contentops_arch_a1b2bot   -> role key: architect
@contentops_rev_a1b2bot    -> role key: review
@contentops_qa_a1b2bot     -> role key: qa
@contentops_rel_a1b2bot    -> role key: release
```

All of them route into one GoodTeam Telegram Gateway. The Gateway owns:

- Bot credential lookup from environment or a sensitive value manager.
- Chat allowlist and user allowlist enforcement.
- Mapping Telegram bot identity to GoodTeam role key.
- Context binding to project/run/gate/message.
- Message bus writes.
- Approval callback forwarding to the existing approval path.
- Worker answer dispatch only when explicitly enabled in a later milestone.

### Bot Username Strategy

The important identifier is the Telegram bot username, because humans mention it
with `@...` and Telegram uses it for deep links. Display name can be friendlier,
but username should be deterministic, short, and project-scoped.

Recommended generated shape:

```text
@<project_slug>_<role_slug>_<hash>bot
```

Rules:

- Start with a normalized project slug so the bot clearly belongs to the project
  or workspace.
- Use short role slugs to preserve room for the project name:
  `plan`, `arch`, `rev`, `qa`, `rel`, `ui`, `impl`.
- Add a short stable hash or UUID fragment, for example 4 to 6 lowercase
  hex/base32 characters, to avoid global Telegram username collisions.
- Keep the full username short enough for Telegram username limits and for human
  readability in group chat.
- End with `bot`, matching Telegram bot username convention.
- If the generated username is unavailable, keep the project and role portions
  stable and rotate only the hash fragment.

Example policy:

```text
project_key: CONTENTOPS
project_slug: contentops
hash: a1b2

planner        -> @contentops_plan_a1b2bot
architect      -> @contentops_arch_a1b2bot
review         -> @contentops_rev_a1b2bot
qa             -> @contentops_qa_a1b2bot
release        -> @contentops_rel_a1b2bot
implementation -> @contentops_impl_a1b2bot
```

If the project slug is long, truncate it before adding role and hash:

```text
customer-data-platform -> custdata_arch_a1b2bot
```

### Identity Is Not Authority

A role bot identity represents who is speaking in the chat. It does not grant
authority by itself.

Rules:

- Human approval authority comes from `GOODTEAM_TG_ALLOWED_USERS` or a future
  equivalent allowlist.
- A role bot cannot approve, reject, or request changes on behalf of a human.
- A worker answer can recommend, object, or summarize; it cannot decide a human
  gate.
- All role messages should be auditable as GoodTeam messages, not just Telegram
  chat text.

### Run Context Is Mandatory For M1/M2

M1/M2 remain run-scoped. If a Telegram group message cannot be bound to
`project_key + run_id`, GoodTeam should not append it to the message bus.

Binding priority:

1. Reply to a GoodTeam message with stored context.
2. Reply to an approval card with stored session context.
3. Mention command with exactly one live session in the chat.
4. Mention command with multiple live sessions asks the human to choose.
5. Mention command with zero live sessions replies with a no-context message and
   does not write.

This is not a global role inbox design.

## 6. Telegram Platform And Framework Fit

GoodTeam does not need a multi-agent framework to implement the Telegram group
surface. GoodTeam already owns roles, gates, message bus, workers, and approval.
The useful open-source layer is a Telegram bot framework.

Recommended short-term stance:

- Keep `pyTelegramBotAPI` for the current synchronous, long-polling adapter
  because it is already in `pyproject.toml` and used by `telegram.py`.
- Do not migrate to an async framework just to write the first group-chat design.
- Reconsider framework migration only if multi-credential polling, webhook fan-in, or
  high-volume group handling becomes materially awkward.

Open-source framework options:

| Framework | Fit | Notes |
| --- | --- | --- |
| `pyTelegramBotAPI` | Best first fit | Already shipped in this repo. Good for preserving the current sync boundary. |
| `python-telegram-bot` | Strong Python option | Mature, rich handler model; newer versions are async-first, which may force an architecture change. |
| `aiogram` | Strong async Python option | Good for routers and FSM-style bot apps; higher migration cost for this repo today. |
| `grammY` | Strong TypeScript option | Excellent bot framework, but would introduce a Node service boundary. |
| `Telegraf` | Strong TypeScript option | Mature Node ecosystem choice, also a new service/runtime choice for GoodTeam. |
| CrewAI / AutoGen / LangGraph | Not a Telegram Gateway replacement | Useful as conceptual multi-agent references, but GoodTeam should keep its own role and approval control plane. |

Implementation should also verify current Telegram Bot API behavior for privacy
mode, group mentions, replies, message entities, and any future Guest Bot or
bot-to-bot feature before depending on it.

## 7. Message Context Model

Every GoodTeam-origin Telegram group message should carry enough hidden or
stored context for future replies.

Proposed references:

```text
telegram:chat:<chat_id>/message:<message_id>
telegram:bot:<bot_username>
telegram:user:<user_id>
goodteam:project:<project_key>
goodteam:run:<run_id>
goodteam:gate:<gate>
goodteam:role:<role_key>
goodteam:tg-session:<session_id>
```

For M1/M2, these references can live in existing message bus `refs` and in the
Telegram review/session store. Do not add new persisted schema fields until an
implementation needs them.

Recommended message bus row for a human mention:

```json
{
  "run_id": "run-123",
  "from_role": "human",
  "to": ["architect"],
  "kind": "ask",
  "gate": "rfc",
  "body": "is SQLite still the right first storage layer?",
  "refs": [
    "telegram:chat:12345/message:678",
    "telegram:user:111",
    "telegram:bot:contentops_arch_a1b2bot",
    "goodteam:project:CONTENTOPS",
    "goodteam:run:run-123",
    "goodteam:gate:rfc"
  ],
  "in_reply_to": ""
}
```

Recommended message bus row for a role answer:

```json
{
  "run_id": "run-123",
  "from_role": "architect",
  "to": ["human"],
  "kind": "assert",
  "gate": "rfc",
  "body": "SQLite is still the right M1 storage layer because it preserves local-first setup.",
  "refs": [
    "telegram:chat:12345/message:701",
    "goodteam:worker:job-abc",
    "goodteam:project:CONTENTOPS",
    "goodteam:run:run-123",
    "goodteam:gate:rfc"
  ],
  "in_reply_to": "msg:..."
}
```

## 8. Message Format Rules

Telegram messages must optimize for human scanning and machine binding.

General rules:

- First line: message type + project/gate.
- Second block: the content the human actually needs.
- Last line or button row: next expected action.
- Avoid long run IDs in visible text; keep them in refs/audit.
- Do not send localhost dashboard buttons. Use `/gt view` or a public HTTPS
  dashboard link when configured.
- Prefer editing a live progress/status message over posting duplicates.
- Keep every button tied to an exact session or disambiguation target.

### Progress Update

```text
Progress · CONTENTOPS / IMPLEMENTATION
implementation: Task 2 of 4 finished.

Changed: backend API contract and dashboard empty state.
Next: qa smoke check.
```

### Role Reply

```text
Answer · CONTENTOPS / RFC
architect: SQLite is right for M1 because the product is local-first and single-operator.

Caution: add a migration path note before hosted multi-user mode.
```

### Review Comment

```text
Review · CONTENTOPS / RFC
review: No blocker.

Caution: rollback owner is not explicit.
Next: architect can address this in the RFC.
```

### Approval Card

```text
Needs your decision · CONTENTOPS / PRD

You are deciding: whether to enter technical design.
Recommendation: approve
Evidence: CI passed · Smoke passed · QA unknown · Blockers 0

Reply /gt view for the full artifact when no public dashboard is configured.
```

Button hierarchy should follow the approval UX research:

- Show `Quick approve` as the primary action only when
  `recommendation=approve` and `blocker_count=0`.
- For caution, request-changes, reject, failed evidence, or blockers, lead with
  `Review evidence` / `Review items`.
- Keep approve behind a confirmation step for risky gates.

## 9. Conversation And Flow Rules

### Planning To Review

1. `planner` posts a compact plan-ready message.
2. GoodTeam records it as a role message tied to the run/gate.
3. `review` posts objections or clears the plan.
4. If review has no blocker and no human gate is configured, workflow can
   continue automatically.
5. If review has blockers, route back to the owner role.
6. If a human approval gate is configured, post an approval card.

### Review To Owner

```text
review:
Review · CONTENTOPS / PLAN
Blocker: migration rollback is missing.

Next: architect should update rollback section.
```

GoodTeam should record this as a role `reject` or structured objection in the
message bus, then route the next worker task to the owning role through normal
workflow logic. Telegram is displaying the flow, not inventing it.

### Human Mention

```text
human:
@contentops_qa_a1b2bot what should I check before approving staging?
```

M1 behavior:

- If bound to exactly one run, record the question to `qa`.
- Do not dispatch a worker answer.
- Confirm the message was recorded.

Later behavior:

- If `--answer` or equivalent explicit answer mode is enabled, dispatch a
  bounded `qa` answer job.
- Send the answer summary back to the group.
- Append the answer to the message bus with worker/job refs.

### Approval

Only the approval card can mutate the human gate. A role comment such as
`review: approve` or `qa: looks good` is evidence, not a decision.

## 10. Milestones

### M0: Approval Context Binding Safety

Ship before role or group chat work.

Required outcome:

- Reply-to-card commands bind by `message_id`.
- Bare mutating commands use live-session 0/1/N handling.
- ForceReply notes bind to the exact prompt/card.
- Stale disambiguation taps fail safely.

### M1: Single Bot, Run-Scoped Role Inbox

This is the first role-interaction increment from
`tg-role-interaction-design.md`.

Required outcome:

- `/gt ask <role> <question>` records a run-scoped `ask`.
- `/gt tell <role> <instruction>` records a run-scoped `propose`.
- 0 live sessions does not write.
- No worker dispatch.
- No global role inbox.

### M2: Multi Role Bot Identity And Group Routing

Required outcome:

- Add role bot identity mapping in configuration design.
- Generate candidate bot usernames from project slug, role slug, and a short
  stable hash/UUID fragment.
- Route group mentions to role keys.
- Route replies by stored Telegram message context.
- Continue writing through the same run-scoped message bus.
- Keep one GoodTeam Gateway.

### M3: Worker Pickup Reads Group Messages

Required outcome:

- Worker context reads role-targeted group messages through a centralized helper.
- Messages are advisory context.
- Unrelated roles do not receive private role-targeted messages.

### M4: Explicit Role Answer Dispatch

Required outcome:

- A human can explicitly request a role answer.
- GoodTeam resolves adapter/model through a documented policy.
- The worker answer is bounded, audited, masked, and posted back to Telegram.
- Invocation failures are visible in Telegram as status, not silent drops.

### M5: Group Experience Enhancements

Optional later work:

- Telegram forum topic per project/run.
- Guest Bot or temporary role participation if platform support is reliable.
- Bot-to-bot presentation improvements, while keeping GoodTeam orchestration as
  the source of truth.
- Message edit/dedup policies for lower noise.

## 11. Safety And Permissions

| Action | First allowed milestone | Policy |
| --- | --- | --- |
| Human asks role in group | M2 | Must bind to a live run context before writing. |
| Human leaves role instruction | M2 | Advisory message only. |
| Role posts progress | M2/M3 | Must originate from GoodTeam worker/state, not arbitrary bot text. |
| Role answers on demand | M4 | Explicit command, adapter/model policy, timeout, audit, masking. |
| Role approves a gate | Never | Role messages can recommend, not approve. |
| Human approves a gate | Shipped / M0 hardened | Only allowlisted users through approval cards or safe command binding. |
| Autonomous role-to-role Telegram loop | Not first version | GoodTeam workflow engine controls flow. |
| Global message to a role | Separate design | Requires storage outside run-scoped message bus. |

## 12. Acceptance Scenarios For Future Implementation

These are not tests added by this document. They are implementation-ready
acceptance names for later milestones.

- `test_group_mention_routes_to_exact_role`
- `test_group_reply_binds_to_original_message_session`
- `test_group_mention_rejects_without_live_run_context`
- `test_group_mention_disambiguates_multiple_live_sessions`
- `test_role_bot_progress_posts_use_single_gateway_state`
- `test_role_to_role_reply_appends_message_bus`
- `test_human_approval_button_still_uses_approval_path`
- `test_role_bot_cannot_approve_gate`
- `test_stale_group_button_fails_safe`
- `test_group_message_masks_sensitive_body`
- `test_role_bot_username_uses_project_role_hash_and_bot_suffix`
- `test_role_bot_username_truncates_long_project_slug`
- `test_multi_bot_credentials_are_not_persisted`
- `test_group_answer_dispatch_reports_adapter_failure`

## 13. Non-Goals

- Do not implement this document as part of the current docs-only work.
- Do not modify `src/`, `tests/`, dependencies, config, or runtime behavior from
  this design task.
- Do not create a global role inbox in M1/M2.
- Do not let role chat bypass approval gates.
- Do not let Telegram trigger arbitrary remote execution commands.
- Do not add autonomous bot-to-bot agent loops before GoodTeam workflow support
  exists.
- Do not split GoodTeam state across multiple bot-owned systems.
- Do not require a public webhook for the first version; current GoodTeam
  Telegram support is long-polling based.

## 14. Open Questions

1. Which role bots should exist in the first multi-bot group: all roles, or only
   planner, architect, review, qa, release?
2. Should GoodTeam use one Telegram group for all projects, one group per
   project, or Telegram forum topics per run?
3. How long should the default hash/UUID fragment be: 4, 5, or 6 characters?
4. Should mentions support aliases such as shorter project-local aliases, or
   only canonical bot usernames?
5. What is the default adapter/model for an explicit role answer?
6. Can a role answer command include adapter/model selection, or should that stay
   server-side only?
7. How much worker invocation evidence belongs in Telegram versus Web?
8. How should the product handle a human message that mentions two role bots?

## 15. Recommendation

Build toward group chat, but do it in layers.

The next engineering issue should still be Telegram command/context binding
safety. After that, ship the single-bot run-scoped role inbox. Only then add
multi role bot identities and group routing.

This order preserves the user experience goal without sacrificing the trust
model. The group can feel conversational, but every meaningful action remains
bound to one project, one run, one gate, and one GoodTeam ledger.
