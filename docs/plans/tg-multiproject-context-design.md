# Design — Telegram multi-project context safety

> Stage: **architect / planner**. Captures how Telegram commands stay bound to the
> correct project when GoodTeam manages more than one project at once. Founder
> decision recorded in §2.

## 1. The problem (grounded investigation)

There is an asymmetry in how the Telegram bot resolves *which project* an
interaction targets (verified against `src/goodteam_os/telegram.py` on `main`):

| Interaction | Project resolution | Safe under multi-project? |
| --- | --- | --- |
| Approval **card + button callback** | `session = store.get(session_id)` → `session["project_key"]` (`telegram.py:1146-1162`) — each card's review session carries `project_key + run_id + gate` | ✅ Yes — pressing a button acts on that exact card |
| **Text commands** (`/gt approve`, `/gt view`, …) | `_read_command_state(workspace, project_key)` (`telegram.py:608`) using the **single** `project_key` the poller was started with (`poll_telegram_approvals(..., project_key=...)`) | ❌ No — bound to one baked-in project; ambiguous/wrong with multiple |

So button presses are already project-safe (session-bound), but text commands
silently target whatever single project the bot was launched with. With two
projects both holding pending gates, `/gt approve` cannot know which is meant.

## 2. Founder decision in force

**Binding model = reply-to-card (primary) + disambiguation prompt (fallback).**
Explicitly **not** a stateful "active project" (`/gt use X`) — a sticky per-chat
project is the single biggest source of silent context drift, which is exactly the
failure to avoid.

## 3. The binding model

### 3a. Reply-to-card (primary, unambiguous)
A text command sent as a **reply** to a specific approval card resolves its target
from that card's review session — `project_key + run_id + gate` — not from any
default. This extends the existing ForceReply handling (`telegram.py:1044-1103`),
which already looks up the session by chat; here we look it up by the
`reply_to_message`'s `message_id` → the session whose `message_id` matches.

- Reply to the RFC card for project A with `/gt approve` → approves **A's rfc**,
  regardless of what else is pending.
- This is how a human already thinks about it ("I'm replying to *this* approval").

### 3b. Bare command + disambiguation (fallback)
A bare `/gt approve` (not a reply) resolves by scanning live, undecided sessions
for the chat:

- **Exactly one** project/gate pending → execute against it (preserves today's
  single-project ergonomics).
- **More than one** pending → the bot replies "Which one?" with inline buttons,
  one per pending `(project, gate)`. The user taps; the tap is a normal
  project-safe callback. **Never** silently pick one.
- **None pending** → "No pending approval gate."

### 3c. `/gt view <project> <gate>` explicit form
Read-only commands keep an explicit form for convenience, but `view` is harmless
if mis-targeted, so it can also use the disambiguation fallback.

## 4. Bot becomes multi-project aware

- `poll_telegram_approvals` stops baking in a single `project_key` as the command
  default. The notification side is already per-project (web's
  `notify_current_approval_gate` is called per project and each card is
  project-specific), so only the **command default-resolution** path changes to
  the binding model above.
- **Every approval card title must carry the project name explicitly.** The
  step-by-step summary card already shows `*project_key*` (`telegram.py:360`), but
  the top-level approval notification card (`send_approval_notification`) must add
  the project label so a human scanning multiple cards always sees which project
  each belongs to.

## 5. Decision audit stays correct

`decide_approval(...)` already receives `project_key` + `actor_ref` +
`client_context` at each callsite. Under the binding model the `project_key`
passed is the one resolved from the replied-to card / disambiguation tap, so the
append-only audit row records the right project with no change to the audit
contract.

## 6. Track decomposition (for dispatch)

- **Track BOT (codex), single lane `telegram.py` + tests:**
  1. Resolve a reply-to-card command via `reply_to_message.message_id` →
     matching live session → bind `project+run+gate`.
  2. Bare-command disambiguation: scan live undecided sessions for the chat; 0/1/N
     handling with an inline-button "which one?" prompt for N.
  3. Add the project label to the `send_approval_notification` card title.
  4. Drop the single `project_key` default in `poll_telegram_approvals`'s command
     path (keep it only as a last-resort default when exactly one project exists).

This is one cohesive bot increment, parallel-safe with the defect-intake bot work
(both touch `telegram.py` — sequence them, don't run two codex agents on the same
file).

## 7. Open questions

1. **Chat ↔ project scoping** — if different projects should notify *different*
   Telegram chats (per-project chat routing), that's a larger change; this design
   assumes one supervision chat receiving all projects' cards. Confirm that's the
   target topology.
2. **Disambiguation TTL** — the "which one?" prompt references live sessions; if a
   gate is decided elsewhere (web) between prompt and tap, the tap must fail safe
   ("already decided") — reuse the existing decided-session guard.
