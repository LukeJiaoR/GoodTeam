# Cross-page workspace

This is the next bounded Beautiful UI-inspired UI/UX slice after #512, tracked
by #530 under #379 and #397. It joins the existing Work experience to Projects,
Knowledge, Team and Review rather than treating each page as a separate visual
product.

## Design and functional boundary

[Beautiful UI](https://www.beautifului.dev/) was revisited on 2026-10-12. Its
Task Rows, Context Cards, Records/Filter Table, Sidebar Nav and Search patterns
inform compact scanning, clear sources, secondary details and restrained
navigation. They do not supply GoodTeam execution state, permissions or
acceptance evidence. The functional basis remains
[the UX research](functional-ux-redesign-2026-10-11.md).

- `workspaceSurface.css` owns shared light/dark material, product chrome,
  controls, search/filter affordances, focus and reduced motion.
- `PageHeading` gives Projects, Knowledge, Team and Review the same title,
  description, existing actions and summary hierarchy. Each page retains its
  data, actions, errors and disclosure behavior.
- `workspacePages.css` owns the scan/read/inspect layouts: project rows,
  knowledge catalogs and sources, Team roles and configuration, Review
  queue/document/evidence. Review embedded in Work remains an inspector.
- `workspaceDocuments.css` keeps source documents and results readable in both
  themes. Work continues to own task navigation, conversation, execution and
  outcomes through its existing scoped workspace styles.
- Superseded shared neumorphic styles and duplicate global Work chrome are
  removed rather than left as competing imported layers.

## Return to the same work

The project navigation remembers visited Work, Review, Knowledge and Team URLs
in browser session storage. A user who opens a historical task, looks up a
memory source, and visits Team can return to the same task/source instead of
being silently reset to the current task or default Knowledge overview.

Only URLs already visited in this tab are saved; no workflow state, unsent
conversation text, credentials, execution permissions or API responses are
stored here. At most 20 project records are retained in that tab session.
Destinations must match the exact decoded project and the expected supported
page route. Off-site, cross-project, malformed and wrong-section values fall
back to the canonical page. Storage failure leaves ordinary navigation usable.
The active URL always wins over remembered history, including Back/Forward and
an explicit return to current Work.

Review's existing unsaved-change blocker still controls route transitions. No
navigation performs a mutation or bypasses task/artifact/source identity
checks. Remembered links may reference subsequently removed resources; those
pages continue to show their existing unavailable/error states. This does not
capture unsaved form edits or guarantee that an old source remains available.

## Scope and verification

Two source-baseline router regressions reproduced historical task and review
selection loss; the fixed navigation preserves both. Focused tests cover exact
memory queries, project isolation, Back/current selection, corrupted or denied
storage, bounded retention and the existing dirty-review blocker. Relevant
page behavior suites, full frontend lint and production build are recorded in
the implementation PR after validation.

This is source/DOM/build evidence. Actual browser layout, screenshots, multi-size
and visual acceptance remain unperformed because the supported browser routes
are restricted in this environment. Do not infer a visual pass from CSS or
jsdom. No new dependency, CI workflow, backend contract, runtime admission or
deployment is part of this slice. #409 and #420 stay parked; #411/#418/#419 keep
their real-task/browser acceptance requirements. Current-attempt context and
result integration, full recovery journeys and observed usability remain
separate unfinished work.
