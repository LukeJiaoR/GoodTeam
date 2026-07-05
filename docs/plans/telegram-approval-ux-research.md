# Telegram Approval UX Product Research

> Status: product research. No code changes are proposed in this document.
> Date: 2026-07-05.

## 1. Executive Summary

Telegram is already a meaningful approval client for GoodTeam, not merely a
notification adapter. It can send approval cards, show compact decision
intelligence, support quick decisions, run step-by-step review, capture
request-change notes, and write through the same approval ledger as the Web
dashboard.

The product opportunity is to make Telegram trustworthy as the user's mobile
control surface. The current implementation is usable for one active project and
one active approval card. It becomes fragile when the user supervises multiple
projects, multiple pending gates, or several cards in a shared chat. The highest
leverage improvement is not more buttons or more evidence. It is stronger
context binding: every Telegram action must clearly and safely target the exact
project, run, gate, and review item the user thinks they are acting on.

This document distinguishes three states:

- **Shipped**: implemented and covered by existing code/tests.
- **Proposed**: product-approved direction, not yet implemented.
- **Open**: unresolved product choice.

Recommended product direction:

1. Treat Telegram as the "mobile gate cockpit" for quick supervision and common
   approval decisions.
2. Keep the Web dashboard as the deep workbench for long artifacts, diffs,
   history, and investigation.
3. Make all Telegram decisions card-bound or explicitly disambiguated.
4. Make high-risk decisions slower and safer, while preserving fast paths for
   low-risk gates.
5. Reduce chat noise by making notification routing profile-aware and by editing
   existing cards instead of sending new ones.

## 2. Product Context

GoodTeam's core product promise is not code generation alone. It is a local-first
AI engineering team that moves work through explicit human gates with evidence,
memory, and auditability. The approval moment is therefore one of the product's
highest value surfaces.

Existing product docs already frame this clearly:

- `docs/product-core-adapter-strategy.md` says the approval moment should be the
  strongest product surface and that Telegram parity should allow every gate to
  be reviewed and decided from Telegram for the happy path.
- `docs/plans/review-workbench-prd.md` positions Dashboard and Telegram as two
  clients for the same GoodTeam approval protocol.
- `docs/plans/tg-multiproject-context-design.md` identifies the main safety gap:
  Telegram text commands are still bound to a single poller project, while card
  callbacks are session-bound.

This research builds on those documents and focuses on the product experience:
what the user sees, what they believe they are approving, what can go wrong, and
what would make Telegram feel reliable enough for real daily use.

## 3. Shipped / Proposed / Open Matrix

This table is the guardrail for future implementation work. Items marked
`proposed` or `open` must not be treated as shipped behavior.

| Area | Status | Current behavior / target behavior | Primary references |
| --- | --- | --- | --- |
| Approval notification card | Shipped | Sends a project + gate card with artifact digest, recommendation, evidence line, quick approve, step review, reject, and optional public HTTPS full-report button. | `telegram.py`, `tests/test_telegram.py` |
| Localhost URL fallback | Shipped | Does not put localhost in Telegram URL buttons; uses a `/gt view` hint instead. | `tests/test_telegram.py` |
| Card dedup / refresh | Shipped | Reuses a live `(project, run, gate)` session and edits the existing message when possible. | `telegram.py`, `tests/test_telegram.py` |
| Step-by-step item review | Shipped | Allows ok / flag / skip / previous / summary / approve / request changes / reject on compact review items. | `telegram.py`, `tests/test_telegram_review.py` |
| Web + Telegram shared approval ledger | Shipped | Telegram and Web both write through `decide_approval`; audit can record origin, actor, review mode, evidence snapshot, and review items. | `web.py`, `tests/test_web.py` |
| Sensitive-data masking | Shipped | Masks paths, IPs, SSH targets, deployment dirs, and similar operational details in Telegram text. | `telegram.py`, `tests/test_telegram.py` |
| `/gt status`, `/gt workers`, `/gt evidence` | Shipped | Read-only remote supervision commands for the poller-bound project. | `telegram.py`, `tests/test_telegram.py` |
| `/gt view`, `/gt approve`, `/gt reject`, `/gt changes` | Partially shipped | Commands work for the poller-bound project; they are not yet reliably reply-bound or multi-project safe. | `telegram.py`, `docs/plans/tg-multiproject-context-design.md` |
| Reply-to-card command binding | Proposed | A command sent as a reply to an approval card should resolve by `reply_to_message.message_id` to that exact live session. | `docs/plans/tg-multiproject-context-design.md` |
| Bare-command disambiguation | Proposed | Bare mutating commands should scan live sessions in the chat: 0 -> no pending gate, 1 -> execute, N -> ask which project/gate. | `docs/plans/tg-multiproject-context-design.md` |
| ForceReply exact note binding | Proposed | A flagged-item note should bind to the original prompt/card session, not the first undecided session in the chat. | Product finding in this research |
| Stale disambiguation tap fail-safe | Proposed | If a disambiguation option points to a session decided elsewhere, the tap should fail safely with an explanatory message. | Product finding in this research |
| Risk-aware button hierarchy | Proposed | Quick approve should be primary only for clean gates; caution/request-changes/reject recommendations should lead with evidence/review and put approve behind confirmation. | Product finding in this research |
| Per-project chat routing | Open | Unknown whether all projects share one supervision chat or can route to separate chats. | Open question |
| Default notification policy by profile | Open | Greenfield and fix-profile gates may need different Telegram defaults. | Open question |

## 4. User Snapshot

| User | Need | Failure to avoid |
| --- | --- | --- |
| Founder / operator | Keep GoodTeam moving from a phone; approve clean gates quickly; slow down on risky gates; trust every tap targets the intended project. | Wrong-project approval, ignored noisy alerts, or vague warnings that force a dashboard visit. |
| Design partner / occasional reviewer | Understand what changed, what evidence exists, and how to request changes without learning GoodTeam internals. | Approving because the card looks official rather than because the evidence is clear. |
| Auditor / future reviewer | Reconstruct who approved, from which client, after what review path, with what evidence and notes. | Telegram decisions that lack durable context or item-level reasoning. |

## 5. Current Telegram Experience

### 5.1 Notification Card

Current card payload includes:

- Gate and project header.
- First meaningful artifact digest.
- Recommendation verdict and blocking count.
- Evidence line for CI, Smoke, QA, and blockers.
- Buttons for quick approval, step-by-step review, reject, and optionally full
  report if `GOODTEAM_DASHBOARD_URL` is public HTTPS.

Strengths:

- Stronger than a raw "waiting for approval" alert.
- Avoids full run UUID noise.
- Avoids localhost URL buttons that Telegram rejects.
- Uses plain text for top-level cards to reduce Markdown rendering failures.
- Refreshes an existing live gate card where possible instead of sending
  duplicates.

Product gap:

- The card does not yet say enough about the user's decision job. A user sees
  evidence status, but not always "what happens if I approve this gate now" or
  "why this is safe/risky."
- The quick approve button is equally prominent for low-risk and high-risk gates.

### 5.2 Step-by-Step Review

Current step-by-step review splits artifacts into compact review items. The user
can mark an item ok, flag it, skip it, move backward, exit to summary, request
changes, approve, or reject.

Strengths:

- Fits mobile constraints better than a long artifact dump.
- Captures item-level review state for the audit path.
- Allows concrete request-change notes.

Product gap:

- "Flag" requires a ForceReply note. The implementation currently resolves the
  reply by chat and undecided status, not by the exact card message. In a
  multi-card chat, this can attach a note to the wrong active session.
- Summary allows "approve anyway" after flagged items. That is sometimes useful,
  but the card should make the risk explicit and preserve the reason.

### 5.3 Text Commands

Supported commands include status, approvals, workers, evidence, view, approve,
reject, changes, and bug.

Strengths:

- Enables power users to act without hunting for buttons.
- `/gt view` makes localhost dashboard situations workable.
- `/gt approve|reject|changes` gives a fast mobile path and records audit
  metadata.
- `/gt bug` starts a fix workflow from the same supervision channel.

Product gap:

- Bare commands are bound to the project key passed to the poller. They do not
  naturally know which card the user means.
- Reply-to-card command binding is designed but not yet fully realized.
- Multi-project operation needs disambiguation rather than hidden defaults.

### 5.4 Web and Telegram Sync

The Web approval path calls the same decision API. Active Telegram review cards
can be updated when Web decides the gate.

Strengths:

- Web and Telegram share the state ledger.
- Decision origin, actor, review mode, client context, evidence snapshot, and
  review items are part of the approval audit contract.

Product gap:

- Telegram final state messages can do more to explain what happened next.
- "Already handled" and stale-button states should be more user-facing and less
  transport-level.

## 6. Core Product Problem

The problem is not "Telegram cannot approve." It can.

The problem is that approval is a high-trust action inside a low-context mobile
chat. A user must never wonder:

- Which project am I approving?
- Which run is this?
- Which gate is this?
- Did this card already become stale?
- Did my note attach to the correct item?
- Is "approve" safe when evidence is unknown or failed?

For a single project, the current experience is close. For multi-project or
shared-chat supervision, the experience needs stronger targeting and clearer
decision framing.

## 7. Product Principles

| Principle | Product rule |
| --- | --- |
| Card-bound by default | Every mutating action anchors to a specific approval card or explicit disambiguation choice. Avoid sticky "active project" state. |
| Fast when clean, slow when risky | Clean gates can be one tap. Caution, failed evidence, unknown evidence on high-risk gates, or blockers require review or confirmation. |
| Complete, not exhaustive | Telegram supports the happy path end to end; Web remains the surface for long diffs, full artifacts, dense provenance, and history. |
| No silent ambiguity | If more than one target is plausible, ask the user to choose. |
| Audit mirrors the user's mental model | Record what the operator saw and did: origin, actor, review mode, review items, warnings, notes, and evidence snapshot. |

## 8. Key Findings

### Finding 1: The Architecture Is Sound

Telegram decisions write through the same approval function as Web. This is the
right product architecture. Telegram should remain an adapter/client, not a
parallel workflow engine.

Product implication:

- Invest in Telegram UX without splitting state semantics.
- Keep all decision side effects centralized in the GoodTeam approval ledger.

### Finding 2: Card Callbacks Are Safer Than Text Commands

Modern approval cards use session IDs. Callback actions resolve to the stored
session, which includes project, run, and gate. Text commands still rely on a
single project key passed to polling.

Product implication:

- Buttons should remain the primary mobile interaction.
- Text commands should either be reply-bound or disambiguated.

### Finding 3: Multi-Project Is the Main Trust Gap

GoodTeam's product direction implies multiple projects and multiple active
workers. Telegram's current single-project command model will not hold as the
user supervises more than one run.

Product implication:

- The existing `tg-multiproject-context-design.md` should be promoted from
  design note to near-term product requirement.

### Finding 4: Current Cards Have Improved, But Need Decision Framing

The card already removed machine noise and added digest, recommendation, and
evidence. The next improvement is not more raw data. It is clearer decision
framing.

Product implication:

- Add copy that answers: "What am I deciding?" and "What happens next?"
- Make caution states visually and behaviorally distinct.

### Finding 5: ForceReply Is Valuable but Needs Exact Binding

Request-change notes are one of Telegram's strongest features because they let a
human send actionable feedback from a phone. But note capture must attach to the
exact card and item.

Product implication:

- Treat note binding as P0/P1 safety work, not polish.

### Finding 6: Notification Noise Can Undermine Trust

The current default notification gates include greenfield gates and fix-profile
gates. That may be right for a founder dogfooding GoodTeam, but it can become
too noisy for design partners or teams.

Product implication:

- Notification routing should become profile-aware and user-configurable.
- Major release gates and high-risk fix gates deserve different defaults.

## 9. Implementation Delta And Roadmap

### Milestone 1 / Engineering Issue: Telegram Command Context Binding Safety

Goal: eliminate silent context drift.

Priority: P0/P1. This is ready to become an engineering issue. It should be
implemented before broader card redesign work.

Issue title:

```text
Telegram command/context binding safety
```

User-facing behavior:

- If the user replies to a card with `/gt view`, `/gt approve`, `/gt changes`, or
  `/gt reject`, the command acts on that exact card.
- If the user sends a bare command and exactly one live approval is pending in
  the chat, the command acts on that card.
- If multiple live approvals are pending, Telegram asks "Which approval?" with
  buttons showing project and gate.
- If no approval is pending, Telegram says so and suggests `/gt approvals`.
- ForceReply notes attach to the exact review item that requested the note.

Implementation delta:

- Add session lookup by Telegram card `message_id`.
- Resolve reply-to-card commands by `reply_to_message.message_id`, not by the
  poller's default `project_key`.
- Add live-session scan for a chat for bare commands.
- Add 0/1/N routing for bare commands:
  - 0 live sessions: reply "No pending approval gate."
  - 1 live session: execute against that session.
  - N live sessions: send a disambiguation card with one button per
    `(project_key, gate, run_id)` candidate.
- Make disambiguation buttons session-bound and stale-safe.
- Bind ForceReply note responses to the original prompt/card session, not the
  first undecided chat session.
- Keep `project_key` as a compatibility fallback only when it cannot create
  silent ambiguity.

Acceptance:

- A shared chat with two pending projects cannot approve the wrong project using
  a bare command without an explicit choice.
- A note reply cannot attach to a different card's flagged item.
- A disambiguation tap for a session already decided in Web or another Telegram
  card is ignored with a clear stale/handled message.

Required test scenarios:

- `test_reply_to_card_approve_binds_exact_session`
- `test_reply_to_card_view_uses_replied_card_session`
- `test_bare_approve_executes_single_live_session`
- `test_bare_approve_disambiguates_multiple_live_sessions`
- `test_bare_approve_reports_no_live_sessions`
- `test_force_reply_note_uses_reply_message_session_not_first_chat_session`
- `test_disambiguation_button_ignores_stale_decided_session`
- `test_poll_project_key_is_not_used_when_reply_session_is_available`

### Milestone 2: Safer Decision Cards

Goal: make the approval card express decision risk.

User-facing behavior:

- Card states distinguish clean, caution, changes recommended, and reject
  recommended.
- Risky gates make "view evidence" or "step-by-step review" the dominant path.
- Quick approve remains available when evidence is clean, but is de-emphasized
  or confirmed when evidence is failed/unknown.
- Final state cards show the next workflow step or next expected notification.

Button hierarchy rules:

| Condition | Primary button | Approval path |
| --- | --- | --- |
| `recommendation.verdict == approve` and `blocker_count == 0` and no failed evidence | `✅ 快速批准` | One tap approval allowed. |
| `recommendation.verdict == caution` or any evidence is unknown on high-risk gates | `🔍 逐项 review` or `📖 查看证据` | Approve remains available but requires confirmation. |
| `recommendation.verdict == request_changes` or `blocker_count > 0` | `⮌ 送回修改` or `🔍 逐项 review` | Approve requires explicit "approve despite warnings" confirmation. |
| `recommendation.verdict == reject` or CI/Smoke/QA failed on release-sensitive gates | `❌ 拒绝` or `🔍 查看失败证据` | Approve is hidden or requires a typed/second-step confirmation, depending on product decision. |

The current shipped card keeps `✅ 快速批准` and `🔍 逐项 review` at the same
hierarchy for all recommendation states. That is intentionally listed as a
proposed delta, not shipped behavior.

Acceptance:

- A user can tell from the card whether GoodTeam recommends approval or caution.
- A failed evidence state cannot look visually equivalent to a clean gate.
- Final cards answer "what happened after my tap?"
- Tests cover payload/button changes for approve, caution, request_changes, and
  reject recommendation states.

### Milestone 3: Notification Routing and Attention Design

Goal: reduce chat fatigue.

User-facing behavior:

- Notification gates can be configured by profile or project.
- Fix-profile gates can use a separate notification policy from greenfield
  delivery gates.
- Repeated updates edit the same card where possible.
- Low-value informational updates are summarized rather than pushed one by one.

Acceptance:

- Users receive fewer but more meaningful alerts.
- Telegram remains actionable, not noisy.

### Milestone 4: Mobile Evidence Upgrade

Goal: let users approve common gates without opening Web.

User-facing behavior:

- `/gt evidence` includes the same high-level evidence categories as the card,
  plus the top blocker or top failing check.
- `/gt view` can target project and gate explicitly.
- Step-by-step items are tuned per gate:
  - PRD: user, scope, non-goals, acceptance, risks.
  - RFC: architecture, data flow, migrations, rollback, verification.
  - Staging: acceptance, health, deployment evidence, regression.
  - Release: release notes, rollback, known caveats, rollout checklist.
  - Fix plan: bug summary, root cause, proposed fix, verification.
  - QA: failed/passed checks, smoke evidence, residual risk.

Acceptance:

- A clean PRD/RFC/staging/release happy path can be approved entirely from
  Telegram.
- Any unclear or risky path has an obvious route back to the dashboard.

## 10. Non-Goals For The Next Implementation Pass

- Do not rebuild Telegram as a full dashboard.
- Do not add arbitrary shell or server-action execution from Telegram.
- Do not introduce sticky `/gt use <project>` active-project state as the primary
  binding model.
- Do not change the shared `decide_approval` ledger contract unless required by
  the binding work.
- Do not redesign all copy before the context-binding safety issue is complete.
- Do not solve per-project chat routing in Milestone 1; keep it as an open
  product question.

## 11. Suggested UX Copy Direction

The current copy mixes English command language with Chinese review language.
That is workable for the current founder user, but product-level copy should be
consistent.

Recommended tone:

- Short.
- Concrete.
- Decision-oriented.
- Avoid internal node names.
- Preserve technical terms only when they help.

Examples:

Top card:

```text
PRD 待审 · CONTENTOPS
你在决定: 是否进入技术设计
本次: ContentOps Studio v1 PRD
建议: approve · 无阻断
证据: CI 未知 · Smoke 未知 · QA 未知 · 阻断 0
```

Caution card:

```text
STAGING 待审 · CONTENTOPS
你在决定: 是否允许准备发布
建议: caution · 1 个阻断
主要原因: Smoke failed
下一步建议: 先看证据或送回修改
```

Final card:

```text
已批准 STAGING
工作流已继续到 Release 准备。
下一张卡将在 release 待审时推送。
```

Ambiguous bare command:

```text
现在有多个待审项。请选择你要处理的审批:
- CONTENTOPS · RFC
- GOODTEAM · QA
```

Stale card:

```text
这张卡已经不是当前审批。
最新状态: RELEASE 已在 Web 端批准。
发送 /gt approvals 查看当前待审项。
```

## 12. Metrics

### Trust and Safety

- Wrong-target approval incidents: target zero.
- Stale-card action attempts: should decline over time.
- Decisions with explicit project/run/gate binding: target 100 percent.
- Request-change notes with item-level binding: target 100 percent.

### Usability

- Median taps to approve clean gate.
- Median taps to request changes with a note.
- Percent of approval gates completed from Telegram without opening Web.
- Percent of risky gates where the user views evidence or step-by-step review
  before approving.

### Attention Quality

- Telegram cards sent per run.
- Telegram cards refreshed instead of duplicated.
- Notification open/action rate.
- Number of ignored or expired cards per run.

### Audit Quality

- Decisions with origin, actor, review mode, evidence snapshot, and review items.
- Decisions with notes when requesting changes or rejecting.
- Decisions where recommendation was caution/reject but user approved anyway and
  audit captured that context.

## 13. Product Risks

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Over-optimizing for speed | The product value is trustworthy delegation, not fastest possible approval. | Use risk-aware button hierarchy and confirmation for caution-state approvals. |
| Telegram becoming a second dashboard | Long evidence and historical exploration do not fit mobile chat well. | Keep Telegram compact, itemized, and linked to Web for full review. |
| Multi-project ambiguity | Hidden default project context can advance the wrong run. | Use reply-to-card binding, disambiguation buttons, and no sticky active project. |
| Notification fatigue | Equal-priority alerts train the user to ignore the channel. | Use configurable gate policies, edit-in-place refresh, and summaries for low-risk updates. |

## 14. Open Product Questions

1. Should Telegram default to one supervision chat for all projects, or should
   projects support separate chat routing?
2. Should quick approve be hidden for caution/reject recommendations, or only
   moved behind confirmation?
3. Should `prd` and `rfc` be default Telegram gates for all users, or only when
   Telegram is configured as the primary approval client?
4. How much English should remain in commands and labels for the current target
   user? Commands will likely stay English, but card copy can be localized.
5. Should Telegram support role-specific reviewers later, or remain an operator
   channel?
6. How should GoodTeam summarize debate objections in Telegram without creating
   too much text?

## 15. Source Map

Primary implementation references:

- `src/goodteam_os/telegram.py`: Telegram session store, notification payload,
  command handling, callbacks, ForceReply note capture, masking, polling.
- `src/goodteam_os/web.py`: approval gate notification, approval decision,
  audit event writing, evidence snapshot, approval reviews.
- `frontend/src/components/tabs/ApprovalsTab.jsx`: Web approval workbench and
  audit display.
- `tests/test_telegram.py`: top-level Telegram notification, commands, stale
  callback, and approval behavior tests.
- `tests/test_telegram_review.py`: interactive step-by-step review behavior.

Primary product references:

- `docs/product-core-adapter-strategy.md`: approval moment and Telegram parity.
- `docs/plans/review-workbench-prd.md`: Dashboard and Telegram as clients of the
  same approval protocol.
- `docs/plans/tg-multiproject-context-design.md`: reply-to-card and
  disambiguation model.
- `docs/current-progress.md`: history of Telegram UX improvements, dedup,
  localhost URL fix, digest card, and live verification notes.

## 16. Recommendation

Do not start by redesigning every Telegram card. Start with the trust layer:
reply-to-card command binding, bare-command disambiguation, and exact ForceReply
note binding. That makes the current product safe enough for realistic
multi-project use.

After that, improve decision framing and risk-aware button hierarchy. The next
implementation-ready issue is Milestone 1: `Telegram command/context binding
safety`. Milestone 2 should follow only after the shipped/proposed matrix is
updated to reflect the binding work.
