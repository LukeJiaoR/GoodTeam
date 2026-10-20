# Goal history pagination

Work polling opts into `GET /api/state?project=KEY&goal_limit=1`. It receives the
latest complete Goal in `task_assistant_session.goals` plus
`goal_page.next_before`. The default state API and `TaskAssistantService.session`
without `goal_limit` retain their complete chronological Goal list.

`GET /api/goal-history?project=KEY&goal_limit=10&before=GOAL_ID` retrieves older
complete snapshots. The page size is 1–50 (default 20 on this endpoint). Omit
`before` for the newest page. Results within each page are chronological;
`next_before` is the oldest returned Goal ID when another page exists, otherwise
an empty string. The UI displays history newest first across pages.

Cursors are scoped to the project and validated against persisted Goal IDs.
Unknown or another project's cursors fail with HTTP 400. New Goals cannot shift
an older traversal. Each page reads fresh revisions; pages are not a frozen
cross-request snapshot. No Goal records, work units, assessment quotes, history
events or evidence references are truncated. This bounds the number of Goal
snapshots loaded per request, not the byte size of an individual record.

The latest Goal includes the only unfinished Goal, when one exists: creation
rejects projects with an active, awaiting-approval or blocked Goal. History is a
read-only disclosure and never supplies approval/retry/stop controls. Its full
record disclosure retains older work and evidence not shown in the compact card.
Viewing a legacy project without a Goal database does not create one. Projection
caches remain request-local. Old session/action callers are unchanged.

Verification: focused ledger/session/HTTP pagination contracts and frontend
request/isolation tests. No paid Worker or real-model acceptance is implied.
