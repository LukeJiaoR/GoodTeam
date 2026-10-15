# Work reading position (#397)

The project conversation follows new content while the reader is near the end
(within the existing 64 px threshold). Scrolling up pauses automatic following.
A new approval, changed intent phase, assistant failure, or Team decision does
not move that manually chosen position or take focus from an unsent draft.

While following is paused, a small non-overlay strip offers:

- **View pending items**, when the current rendered state has an unresolved
  attention item, assistant recovery/Team decision, or clarification draft.
  This scrolls to the existing conversation controls and focuses their group;
  it does not approve, retry, dismiss, send, or substitute a new action.
- **Return to latest**, which resumes normal following. Manually scrolling near
  the bottom does the same. Sending a new message also retains the existing
  explicit-send behavior of returning to the latest conversation content.

The strip derives pending state from current props, not a second inbox or an
unread counter. A superseded blocker while execution is active is not advertised
as pending. Resolution removes the pending affordance. Project/task/history
scope changes clear the prior pause state; returning later does not resurrect
it. This does not implement durable reading-position restoration, a while-away
summary, or any new workflow semantics.

Validation tier is Tier 4, frontend-only viewport/layout behavior with no
control-plane changes: DOM regressions cover manual reading and drafts,
explicit navigation, follow/resume, attention resolution, scope round trips,
and no action callbacks. The full frontend unit/Node suites, lint and build are
also run. DOM geometry tests do not prove browser layout, physical keyboard,
or mobile behavior. Real browser acceptance remains separate because Chromium
cannot start in this VM's existing sandbox.
