# Work conversation reading continuity

The Work conversation keeps the manual-reading protection from #570. Background
attention updates, team decisions, recovery notices and new conversation events
must not steal a reader's position or draft. The existing **View pending items**
control moves focus without approving anything or enabling automatic following.
**Return to latest**, scrolling deliberately back to the bottom, and explicitly
sending a new message resume following.

## Browser-local checkpoint

`ProjectConversation` now mounts a reading controller per project, run and
read-only/archive scope. It saves a stable timeline event ID and relative viewport
offset, a last-read event ID, the follow/pause state, and a compact identity of
pending controls. It does not save message text, files, evidence, decisions or
credentials. The underlying timeline is still supplied by the existing API;
local storage is never a source of workflow or approval authority.

The checkpoint lives in local storage under
`goodteam:conversation-reading:v1`. Its versioned list retains at most 40 scopes
for 30 days, rejects records with invalid fields or unreasonable offsets and
ignores oversized input. A write strips unknown fields. Disabled storage, quota
failure, invalid JSON, unsupported versions and expired records cannot block the
conversation. Metadata is local to the current browser profile; it is not a
server-side unread ledger and is not synchronized between devices. Concurrent
tabs have independent live reading controllers; the last saved checkpoint for a
scope is used when that scope is opened again.

A first visit starts at the latest event and establishes a baseline. Leaving and
returning to the same project/run, reloading, and browser history navigation restore the event anchor rather than an absolute pixel offset. If the
anchor was removed from available history, the view starts at available history
and states that the checkpoint is unavailable. A missing read frontier does not
produce an invented unread count. Explicitly returning to latest establishes a
new frontier. Changed project, run and archive scopes cannot reuse one another's
positions.

## Changes while away and unread progress

A compact reading bar extends the existing return controls. It reports the count
of durable events after the last-read event, groups those updates as conversation,
review, result or work changes, and indicates changes to currently pending items.
Up to three short descriptions reuse the timeline's existing event formatter;
each opens the exact source event without acknowledging skipped unread events
or replacing the reading bookmark. These are deterministic summaries of the
existing timeline, not model-generated judgments. Reading never resolves an outstanding approval.

The frontier only advances when visible content is read or the reader explicitly
returns to latest. Optimistic sends do not become durable anchors. Scrolling back
to earlier history does not rewind the frontier. **Read new updates** moves to the
first unread event and acknowledges only complete events visible in the viewport;
it keeps following paused, even if this inspection lands near the bottom. Very
long events remain unread until their end is visible. Inspecting pending controls
does not change the saved reading anchor or unread frontier.

Hidden tabs do not move the viewport or consume new events. A response arriving
while hidden, or first arriving after the tab becomes visible, is compared with
the pre-away frontier. An unchanged visible poll cannot erase that boundary
before a delayed update arrives; it remains until a deliberate reading action.
Merely returning to a tab does not mark its new content as read. Hiding a tab
during pending/source inspection persists the logical bookmark rather than the
temporary inspection viewport. Fresh unchanged polling responses cannot move a paused reader or repeatedly
scroll a following reader.

## Verification

This is Tier 4 browser-visible behavior with no backend/control-plane mutations.
Focused helper/component tests cover scoped metadata validation, persistence,
A-B-A/reload restoration, monotonic frontiers, stale anchors, disabled storage,
manual reading/draft protection, explicit sending/return, pending inspection and
hidden/return response boundaries. The dedicated
`frontend/e2e/reading-continuity.spec.js` uses the real HTTP API and fake Workers in
the existing isolated temporary workspace. It covers repeated reload, project
A-B-A and Back/Forward, away updates, pending inspection, hidden in-flight replies,
stale/tampered storage, unavailable storage, draft preservation and mobile width.
It compares durable workflow decisions and Worker jobs before/after reading.

Local focused tests, lint and production build were run during development.
Browser acceptance execution in the current VM was blocked before any app page
opened: system Chromium could not create a local socket (`Operation not
permitted`), and the approved escalated retry failed during environment setup
with a read-only filesystem error. Therefore these browser scenarios are authored
but not locally verified; a successful supported-runner execution is required
before claiming the Tier 4 acceptance gate passed. This fixture does not exercise
real models, third-party adapters, production workspaces or deployment.
