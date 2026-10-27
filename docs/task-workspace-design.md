# Task workspace

The desktop Work layout follows the user's three-pane contract:

- **Left: Worker status.** Current workers, their real states, blockers and
  attempt inspection. Worker records are expanded by default. Task input tools
  remain here as secondary disclosures. Git recovery is accessed through the
  project tools disclosure in the workspace toolbar, closed by default.
- **Center: Chat.** The ongoing project conversation and composer occupy their
  own full-height pane. Execution details do not stack below the conversation.
- **Right: History & delivery.** Task/run history and context, queued drafts, Goal controls,
  historical activity and the selected task's delivery/evidence share this pane.

This restores the user requirement clarified on 2026-10-26. The intervening
left-hand task-history rail and execution-below-chat layout were a departure
from that requirement. Subsequent visual work must preserve these pane roles.
Narrow screens switch between the same three surfaces with tabs, defaulting to
Chat; Manage tasks opens History & delivery and closing it returns to the composer.

The visual direction remains informed by [Beautiful UI](https://www.beautifului.dev/):
quiet surfaces, compact records and a focused composer, using GoodTeam's actual
state rather than a timer-driven demonstration lifecycle.

## State and interaction boundaries

- Current and historical workflow runs are top-level task navigation. Outcome
  assignments are steps within the selected task; they are not additional runs.
- Queued requests remain editable drafts until the existing start flow accepts
  them. A saved draft is not a running worker.
- The conversation remains mounted while task navigation and execution details
  are opened. Existing draft, acknowledgement, and duplicate-submit behavior is
  retained.
- Historical views remain read-only. Stale or unknown data does not become a
  completed result or grant mutation capabilities through a visual change.
- Delivery summaries and skill-review reports keep their existing task,
  artifact-version, and receipt bindings. A completed process is not proof that
  every acceptance criterion was verified.
- Automatic candidate review retains its existing automatic protocol. This
  layout does not add a human approval prerequisite or change other workflows'
  server-side decision rules.

## Execution evidence entry

The optional `team_workspace.mission.execution_evidence_href` is projected by
the backend for the current project-bound Operator queue item. It points to the
existing same-origin `/review-workbench/{execution_id}?project_root=...` page.
The queue execution ID, joined run execution/task IDs, task repository, and
available issue number must agree. Both task and run records must exist in the
resolved project root.

Missing, malformed, or mismatched records produce an empty link. Ordinary
workflow missions have no inferred execution link: their `run_id` is never used
as an execution ID. The React surface consumes this field rather than searching
other executions or recomputing candidate acceptance. Link availability means
that the evidence page can be opened, not that the candidate is accepted.

## Verification scope

The read-only projection is tested for current project scoping, malformed and
mismatched records, missing roots, encoded root paths, and repeat-read
immutability. The generated link is passed through the existing Workbench page
handler and checked against the correct execution and task.

Frontend verification covers the persistent task list, mobile task access,
continuous conversation drafts, result bindings, and the evidence URL boundary.
Use the existing continuity and task-flow tests when browser execution is
available. DOM interaction checks and a successful production build do not
replace real browser layout, multi-size, or visual acceptance evidence.

## Three-pane restoration verification · 2026-10-26

Verification tier: Tier 4, browser-visible frontend composition and focus routing.
Changed contract: Worker status stays left, Chat stays center, and task history
and delivery stay right. Mobile task navigation returns to the complete composer,
including Send. No backend API, approval authority, evidence binding or Worker
execution contract changed.

Validation from the repository root:

- `NODE_OPTIONS=--no-experimental-webstorage npm run test:unit --prefix frontend -- --maxWorkers=2`:
  63 files / 859 tests passed. After the final focus/phone-height correction,
  the focused WorkPage and currentWorkContext suites passed 89 tests.
- `npm run lint --prefix frontend`, `npm run build --prefix frontend`, and
  `git diff --check` passed. Existing bundle-size/import warnings remain.
- `npm run test:e2e --prefix frontend -- workspace-continuity.spec.js current-work-context.spec.js project-attention.spec.js workspace-ux.spec.js`:
  final 9 tests passed without retries. Desktop bounds verify Worker/Chat/History
  ordering at 1440, 1280 and 1024px; the composer and Send remain usable at
  1280×720, 1024×768 and 390×844. Other shared surfaces are checked at
  375, 768, 1024 and 1440px.
- Related `delivery-reports.spec.js`, `git-recovery.spec.js`,
  `goal-continuation.spec.js`, and `goal-history.spec.js`: 6 tests passed before
  the final composer-only correction. These cover retained delivery identity,
  explicit Git actions and Goal/history controls at their new pane location.

The browser server, registry and project targets were test-owned temporary
workspaces with fake Workers. Goal command projections use the existing bounded
browser fakes; these checks do not establish live-model Goal execution.
Initial browser runs exposed obsolete mobile entry assumptions, a hidden
attention notice and clipped Send; these were corrected before the final pass.
The full backend suite was skipped because server contracts did not change.
No candidate branch was published and no remote CI or merge was performed.

## Chat readability and composer · 2026-10-26

The chat retains Geist and the existing neutral/accent palette. Its type scale
is explicit: message body and input 16px, controls 14px, metadata and keyboard
help 13px. Body line-height is 1.75; input line-height is 1.6. Assistant messages
use an open text layout; user messages keep a restrained background. The field's
accessible label, textarea, error and Send action share one editor with a focus ring.
The redundant header subtitle is removed. Optional context scrolls separately;
desktop and phone minimum heights reserve the input and Send action.

Applied the project-local `frontend-design`, `ui-ux-pro-max` and
`web-design-guidelines` skills. Targeted UX searches covered body readability,
form labels/focus and React controlled inputs. The current Vercel Web Interface
Guidelines were retrieved through Ego Browser. Review of the changed composer
identified missing input name/autocomplete attributes and a hidden-only label;
these are corrected. Keyboard/IME handling, paste, draft persistence and send
semantics remain covered by existing tests. This is a scoped review, not an
accessibility audit of every Workbench component.

Tier 4 validation: the focused ConversationComposer, ProjectConversation and
WorkPage suites passed 125 tests. The continuity, conversation-feedback and
conversation-change-handoff browser suites passed 6 tests. Continuity includes
1440×1000, 1280×720, 1227×763, 1024×768 and 390×844; initial runs exposed clipped
Send controls, corrected before the passing run. Chinese light/dark browser
inspection confirmed rendered 16/14/13px typography and no horizontal overflow.
Lint, production build and diff whitespace checks passed. Existing build warnings
remain. These tests used isolated temporary projects and fake Workers; backend
tests and remote candidate CI were skipped because only presentation changed.

Git entry follow-up: removed the Git panel from Worker status and placed it in
the toolbar's closed-by-default Project tools disclosure. Escape closes the
disclosure and returns focus to its trigger. The existing recovery flow is
unchanged. Tier 4 verification: WorkPage/GitRecoveryPanel unit suites passed
99 tests; Git recovery plus workspace continuity passed 6 browser tests.
The final toolbar-button CSS scoping correction passed both Git browser tests
again. Lint, build and `git diff --check` passed; backend validation was skipped
because no Git operation or server contract changed.

## User-selected ChatGPT direction · 2026-10-26

The user selected ChatGPT after reference inspection of its actual new-chat
screen at https://chatgpt.com/. Apply that direction to the existing three panes:
an open message canvas, a neutral user bubble and one rounded input container.
Mode controls, change-discussion entry and help now sit in the input footer;
Send is an upward arrow with its existing accessible name. The input label is
screen-reader-only. This supersedes the initial visible-label treatment above.
Ordinary execution-boundary help opens on demand. Pending change interpretations,
uncertain outcomes, errors and explicit task/change confirmation remain visible
through their existing flows. No attachment or microphone affordance was added
without an implemented action.

Message identity and complete timestamps remain available through native details
controls. User/assistant messages retain accessible identity, while delivery status
stays visible. System events remain individual readable records; this change does
not remove events, alter their order, or mark unread events as read.

Tier 4 validation: 127 focused unit tests passed. The continuity, send feedback,
change handoff and reading continuity browser suites passed 10 tests. After the
final focus/metadata presentation correction, chat presentation and continuity
passed 5 tests. Presentation checks cover timestamp disclosure, help access,
draft retention, Chinese light/dark themes and phone overflow. Their screenshot
reply text is explicitly a fixture rendered in an isolated fake-Worker project;
it is not a live model result. Lint, build and whitespace checks passed. Backend
and remote candidate CI were skipped because execution contracts did not change.

## Interactive chat answers · 2026-10-26

Assistant replies retain the same ChatGPT-inspired message canvas and three-pane
layout. Ordinary Markdown tables in chat gain numeric-aware, three-state sorting,
row filtering and TSV copy of the current view. Copy reports success or failure;
no-match filtering has an explicit empty state. Tables scroll within their own
container on narrow screens. Document and review Markdown remains static.

Charts, expandable steps and content previews use a fenced `goodteam-ui` block
inside the assistant's existing response string. Each block is JSON with a
nonempty `title` and one supported `type`:

- `chart`: `columns` names the label and numeric series; `rows` holds the values.
  A metric selector changes the displayed series. Hover, keyboard focus and
  click inspect a row. "View data" reveals the same values as an interactive
  table. Signed values use a zero baseline.
- `steps`: `items` contains `title` and `body`; each numbered step expands with a
  native disclosure control.
- `cards`: `items` contains `title`, optional `summary` and `body`; each card has
  an expandable text preview. Preview does not execute or approve work.

Example chart source:

```goodteam-ui
{"type":"chart","title":"Example hours","columns":["Stage","Planned","Actual"],"rows":[["Design",10,6],["Build",20,12]]}
```

Example steps and cards:

```goodteam-ui
{"type":"steps","title":"Plan","items":[{"title":"Inspect","body":"Read the current files."}]}
```

```goodteam-ui
{"type":"cards","title":"Options","items":[{"title":"Option A","summary":"Overview","body":"Details to preview."}]}
```

The manager chat prompt describes these formats while keeping the complete
response within its existing 4,000-character limit and preserving the outer JSON
protocol. Examples are not evidence; estimates must be identified. The renderer
accepts 1–100 chart rows, 2–8 columns and finite numeric cells; steps/cards accept
1–20 items. Titles are limited to 200 characters, column/row labels to 120,
item bodies to 10,000 and summaries to 1,000. A block over 100,000 characters,
incomplete JSON or unsupported/invalid data falls back to the existing code view.
Content is rendered as text and existing safe inline Markdown, without executing
HTML, JavaScript, event handlers or model-provided actions. Task intake, Team
commands, approval and dispatch authority retain their existing contracts.

Applied the project-local design/review skills and the local `animate` skill.
The only added motion is 140ms CSS color feedback on table-button hover, gated to
fine pointers; reduced motion removes the transition. Reading data stays still.
Controls remain keyboard accessible and use application theme tokens. Table and
chart controls are 14px, titles and preview bodies 16px, and auxiliary feedback
13px; phone search inputs use 16px. A targeted UX lookup and current Web Interface
Guidelines review informed the contained table scrolling, labels, focus states
and long-text wrap.

Verification tier: Tier 4 for browser-visible answer rendering, with backend
regression coverage for manager response parsing. The changed contract is
presentation inside the existing response text; no new execution authority or
workflow action is introduced.

- Focused frontend units: `NODE_OPTIONS=--no-experimental-webstorage npm run
  test:unit --prefix frontend -- --maxWorkers=2
  src/components/ui/ChatRichContent.test.jsx
  src/components/ui/MarkdownView.test.jsx
  src/components/ui/MarkdownView.links.test.jsx
  src/components/ui/MarkdownView.fences.test.jsx
  src/features/runs/WorkPage.test.jsx` passed 117 tests. The final chart input
  correction also passed the six rich-content behavior tests.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_manager_execution.py -q`
  passed 10 tests, including nested rich response text and discarded unrelated
  Team commands. `.venv/bin/ruff check src/goodteam_os/ tests/` passed.
- `npm run test:e2e --prefix frontend -- chat-presentation.spec.js
  conversation-continuity.spec.js conversation-feedback.spec.js
  conversation-change-handoff.spec.js reading-continuity.spec.js` passed seven
  related tests. The final `chat-rich-content.spec.js` passed its integrated
  table, clipboard, chart, keyboard disclosure, invalid-output fallback,
  Chinese theme and phone checks. An initial browser run exposed pointer hover
  overriding chart focus during scrolling; row inspection now responds to actual
  pointer movement, focus or click.
- Frontend lint, production build and whitespace checks passed. Existing
  dynamic-import and bundle-size build warnings remain.

The browser server, registry and projects were owned temporary workspaces with
fake Workers. Only answer content was replaced with explicitly labeled example
data for presentation coverage. Screenshots were visually inspected at
1440×1000 and 390×844, including dark mode, contained chart/table width and Send
in the phone viewport. These checks do not establish live-model output fidelity.
No real project workflow was mutated, no service was restarted for testing, and
no remote candidate CI was published.

An additional `PYTHONPATH=src .venv/bin/python -m pytest tests -q` run was
deliberately interrupted after 2,998 passed and 18 skipped (26m15s). Its log is
`/tmp/goodteam-rich-backend-tests.log`. This is **not** a full-suite pass. Focused
manager parsing checks and isolated browser acceptance cover this presentation
change; the extra run was stopped to honor the instruction to finish after the
minimum necessary verification. Execution identity, approval decisions, ledger
writes and dispatch implementation are unchanged. A merge candidate has not
been prepared or validated.

The existing local service was read-only checked to serve the current frontend
build. Refreshing the page loads the renderer. New manager prompt instructions
are source changes and require a backend process reload; the active service was
not restarted as part of this work.

## Stable resize typography and simple flowcharts · 2026-10-27

The shared Studio page-heading token now uses `1.5rem`, replacing its
viewport-width formula. Work banner and composer headings have fixed rem sizes;
the brand no longer changes size at the desktop sidebar breakpoint. Window
resizing changes available space and wrapping. Browser text-size preferences
and page zoom remain available; chat body and input retain their 1rem size.
Applied the local `better-typography` skill for this scoped correction.

Assistant responses also accept a display-only `goodteam-ui` flowchart block:

```goodteam-ui
{"type":"flowchart","title":"Example flow","nodes":[{"id":"check","label":"Input complete?","kind":"decision"},{"id":"plan","label":"Prepare plan","kind":"step"},{"id":"ask","label":"Ask for details","kind":"terminal"}],"edges":[{"from":"check","to":"plan","label":"Yes"},{"from":"check","to":"ask","label":"No"}]}
```

Support is deliberately bounded to sequential steps, branches and merges in a
directed acyclic graph: 1–12 unique node IDs, up to 20 edges, optional node kind
`step`, `decision` or `terminal`. Node labels allow 80 characters, edge labels 40;
unknown endpoints, duplicate IDs, cycles and invalid data retain the ordinary
code fallback. Node labels wrap and longer labels reserve more vertical space.
Long edge labels are shortened in the diagram, with full text in the native
title and the expandable connection list. Nodes and edges are inert text/SVG;
they cannot invoke workflow actions. The manager prompt documents the format
inside the existing response string and existing 4,000-character response limit.

The layout uses the connection graph rather than model-supplied coordinates.
Diagram labels do not scale to fit a larger or smaller window; narrow diagrams
scroll in their own keyboard-focusable container. It uses existing theme tokens
and adds no dependency or motion.

Verification tier: Tier 4, scoped frontend presentation and manager prompt text.
`chat-flowchart.spec.js` and `chat-rich-content.spec.js` passed three browser
cases in a temporary project with fake Workers. Computed sizes were checked at
1024, 1227, 1440 and 1920px: chat body/input 16px, Work title 24px, brand 17px.
Project-page headings stayed 24px at 1024/1440/1920px. Changing the root text
preference to 20px still enlarged the rem-based composer to 20px. This is a
computed preference check, not an OS-level zoom test. The browser checks also
cover fixed-size flow labels, branches, text alternative, phone horizontal
scrolling, no page overflow, dark mode and no POST requests during diagram use.

Focused frontend parser/layout/rich-content/fence tests passed 11 cases;
`tests/test_manager_execution.py` passed 10. Frontend lint/build, touched Python
Ruff and whitespace checks passed. Existing build warnings remain. Screenshots
use explicitly labeled example content rather than a live model response.
The full backend suite was not repeated: execution, approval, persistence and
dispatch code are unchanged. No service restart, candidate publication or merge
was performed.

## Proactive answer presentation · 2026-10-27

The shared manager chat prompt now asks the model to choose a useful presentation
without waiting for a format request. This covers project questions, discovery,
draft refinement and ongoing or historical task discussions through their
existing common manager entry point. Comparisons use tables, supported numeric
data can use charts, detailed procedures use expandable steps, process and branch
explanations use flowcharts, and options or deliverable previews can use cards.
Answers lead with a short conclusion; key questions, blockers and confirmations
stay visible. Usually one view is sufficient. Simple replies stay plain text,
proposed flows retain proposal labels, and chart values require evidence or
explicit estimate labels. The existing 4,000-character response limit remains.

Verification tier: Tier 1, scoped to prompt instructions with no dispatch,
parsing, state or rendering implementation changes. `PYTHONPATH=src .venv/bin/python
-m pytest tests/test_manager_execution.py tests/test_assistant_conversation.py
tests/test_assistant_intake.py -q` passed 37 tests; touched Python Ruff and
`git diff --check` passed. These regressions protect response parsing, discovery
and conversation authority; they do not establish live-model format selection.
The earlier renderer browser evidence remains applicable, so frontend builds and
the full backend suite were not repeated. Backend reload is still required for
new model turns to receive the updated prompt; no service was restarted.
