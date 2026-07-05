# Telegram Implementation Roadmap

Status: planning closure
Date: 2026-07-05
Scope: documentation only

## Purpose

This document closes the Telegram planning pass by connecting the product design docs to the implementation issue chain. It is the short entry point for future work. Use the longer design docs for rationale and UX background, and use the issues below for execution.

Related design docs:

- `docs/plans/telegram-approval-ux-research.md`
- `docs/plans/tg-role-interaction-design.md`
- `docs/plans/tg-group-chat-collaboration-design.md`

Merged baseline:

- PR #246: Add Telegram approval and role interaction product docs

## Planning Boundary

This roadmap is not an implementation PR. It does not change runtime behavior, tests, dependencies, configuration, or Telegram integration code.

The current goal is to sequence the Telegram work so each milestone has one clear trust boundary:

1. Make approval context binding safe.
2. Add a passive run-scoped role inbox.
3. Let worker pickup read role-targeted messages as advisory context.
4. Define policy for explicit role answers.
5. Explore multi-role group identity as a platform spike.

## Execution Order

### Phase 0: Context Binding Safety

Parent issue: #245

This phase must land first. It prevents Telegram commands or replies from silently binding to the wrong approval card, project, run, or gate.

Subtasks:

1. #270 — Add session lookup helpers for card-bound commands.
2. #271 — Route approval commands through resolved card context.
3. #272 — Add bare-command context chooser.
4. #273 — Bind ForceReply notes to the originating review card.
5. #274 — Add context-binding regression tests.

Dependency shape:

```text
#270 -> #271
#270 -> #272
#270 -> #273
#271/#272/#273 -> #274
#270-#274 close #245 implementation scope
```

Exit criteria:

- Reply-to-card commands use the replied card session.
- Bare commands follow 0/1/N live-context routing.
- ForceReply notes attach to the originating prompt/card session.
- Stale chooser taps fail safely.
- The poll default project key cannot override an explicit reply context.

### Phase 1: Run-Scoped Role Inbox

Parent issue: #251

This phase adds the first role-interaction increment after #245. It should remain single-bot, run-scoped, and passive.

Fixed M1 decisions:

- Accept canonical role keys only.
- Do not support aliases in the first pass.
- Reply-to-card context wins.
- Bare command with one live context may proceed.
- Bare command with multiple live contexts must show a chooser.
- Bare command with no live context must not record anything.
- Success confirmation includes role, project, gate when known, and states that no worker was started.
- User text is masked before writing.
- Long text is truncated consistently rather than split.
- Existing message-bus dedupe is reused.

Non-goals:

- No global role inbox.
- No role answer execution.
- No approval flow changes.
- No multi-role group routing.

Suggested implementation checklist for #251:

1. Extend Telegram command parsing for role-message commands.
2. Validate canonical role keys and return a concise role hint for unknown roles.
3. Reuse the #245 context resolver for reply-to-card and bare commands.
4. Append run-scoped message-bus rows only when a context is bound.
5. Include Telegram chat/message/user references in message refs.
6. Send a clear confirmation back to Telegram.
7. Add tests for role validation, context binding, no-context behavior, masking, and no worker start.

### Phase 2: Worker Pickup Reads Role Messages

Parent issue: #252

This phase lets workers see role-targeted operator messages as advisory context.

Guardrails:

- Add one centralized reader/helper for role-targeted run messages.
- Keep messages advisory.
- Do not let messages for one role leak into unrelated role context.
- Do not treat role messages as approval decisions.

Open details to resolve inside #252:

- Exact placement in worker context or pickup packet.
- Sort/grouping by role, gate, or timestamp.
- Whether completed-run messages should later feed memory.

### Phase 3: Explicit Role Answer Policy

Parent issue: #253

This is a design spike before implementation. It should decide product and engineering policy for explicit role answers from Telegram.
Policy spike: `docs/plans/tg-role-answer-policy-spike.md`.

Questions to answer:

- Which roles are allowed to answer from Telegram?
- What adapter/model policy is used?
- Can a user request adapter/model selection from Telegram, or is it server-side only?
- What are timeout and failure-status messages?
- How much evidence appears in Telegram versus Web?
- How is answer provenance recorded?

No runtime change should happen in #253.

### Phase 4: Group Role Identity Platform Spike

Parent issue: #254

This is a platform spike for multi-role Telegram group UX.

Questions to answer:

- Which role bot identities exist first?
- One group per project, one shared group, or forum topics per run?
- Exact project-scoped username generation rules.
- Mention and reply routing rules.
- Privacy-mode and group-message visibility behavior.
- Credential handling and rotation model.
- Loop-prevention and dedupe rules.
- Behavior when a human mentions two role identities.

No runtime change should happen in #254.

## What Is Now Closed

The planning pass has closed these decisions:

- Telegram approval context binding is the root dependency.
- Role inbox must wait for the context resolver.
- Role inbox M1 is passive and run-scoped.
- Worker pickup, role answer dispatch, and group role identities are separate follow-up layers.
- Multi-role group UX is not part of the first implementation pass.
- The current docs are a product and planning baseline, not a direct implementation bundle.

## What Remains Open

These decisions intentionally remain open until their phase:

- Worker pickup formatting and memory fan-out: #252.
- Role answer policy: #253.
- Multi-role group topology and bot identity operations: #254.

## Recommended Next Action

Start with #270 and keep the implementation PR scoped to #245. Do not implement #251 in the same PR. Once #245 is merged, begin #251 as the first user-visible role-interaction increment.
