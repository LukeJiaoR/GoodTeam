# Conversation feedback (selective follow-up to #430 and #431)

Work retains #431's single persistent composer, explicit Message/Request changes
scope, exact change-request accumulation, and version-bound confirmation. This
increment adds Enter-to-send (Shift+Enter inserts a newline; IME and key repeats
are ignored), local delivery feedback and visible task/team proposal explanations.

Delivery feedback belongs to a project/run/message identity. A new message is
visible before the POST completes. It reconciles with the server timeline by ID,
not text. API acknowledgement is distinct from view refresh and never claims
execution or approval. Transport failures leave an unconfirmed status and retain
the input. Explicitly resending the same unconfirmed ordinary message reuses its
ID; there is no automatic retry. Recorded failed assistant requests still use the
existing identity/attempt-bound Retry action. Local feedback is not durable and
never appears in a different project, run or read-only historical view.

The change-discussion endpoint continues to receive #431's complete exact request.
It has no transport message-id contract, so ordinary-message replay semantics
are not claimed for that endpoint. Its existing durable Intent Commit remains
authoritative. When available, returned user event IDs reconcile its feedback.

Verification tier: Tier 4 (browser-visible message handling). Focused tests are
`conversationFeedback.vitest.js`, `ConversationFeedback.test.jsx`, the unchanged
`ConversationComposer.test.jsx`/`conversationRouting.vitest.js`/WorkPage tests,
and `e2e/conversation-feedback.spec.js`. The latter delays a real request and then
simulates a transport failure before forwarding; it uses an isolated fake-worker
project, not a real model or deployment. Full CI evidence is required before merge.
