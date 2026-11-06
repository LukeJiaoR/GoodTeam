# Manager conversation and PostgreSQL migration

Date: 2026-11-07. This is a bounded implementation trial, not a production
cutover or acceptance of open-ended project autonomy.

## Project outcome and observed gap

The Manager should hand over the current batch in the project conversation,
answer a question without losing that handoff, and accept a subsequent explicit
reply against the same current target. After verified delivery, the existing
authorized continuation service can choose a different iteration from the
observed result. A restart must preserve the question, and an intervening Stop
must invalidate the old confirmation.

Previously `assistant_conversation` only discussed work, while current
continuation/batch/attention facts were missing from the interpretation context.
Task Assistant session and task ledgers were JSON files, and bounded Goals used
SQLite. The smallest migration keeps their schemas, identities and protected
services and replaces storage and the conversation wait/resume mechanism.

## State ownership

| State | Storage in PostgreSQL mode | Authority |
| --- | --- | --- |
| Task Assistant conversation, queue, requests, continuation and batch records | `state_documents` JSONB | Existing Task Assistant/session/batch services |
| Project task ledger | `state_documents` JSONB | `LocalTaskStore`, including original IDs and delivery receipts |
| Workflow current, archived state and checkpoints, including approvals and interrupted transitions | `state_documents` JSONB | Existing Workflow/approval services |
| Exact-target change discussions | `state_documents` JSONB | Existing Intent Commit service |
| Team membership, decisions and audit events | `state_documents` JSONB | Existing Team command/revision services |
| Project preparation and readiness | `state_documents` JSONB | Existing importer/executor, directory identity checks and readiness capability gate |
| Project registration | `state_documents` JSONB | Existing project registry; YAML configuration remains a file |
| Historical Operator queue and execution task/run/event ledgers | `state_documents` JSONB | Existing queue state machine and execution identities |
| Bounded Goals, work units, operations, events and outbox | Existing relational schema in a workspace-specific PG schema | `GoalStore` and its existing invariant validation |
| Manager dialogue cursor and pending question | Official LangGraph PostgreSQL checkpoint tables in the same database | Conversation position only; never a grant or delivery receipt |

All of these use `GOODTEAM_DATABASE_URL` and the same connection pool. The
optional `GOODTEAM_DATABASE_SCHEMA` defaults to `goodteam`. Goal table schemas
are derived from this namespace and the existing workspace identity, preserving
the isolation previously provided by one SQLite file per workspace. JSONB keys
are the canonical original store paths; no task identity or execution ledger is
added. This trial assumes the same local workspace paths after restart.

Readiness writes retain the importer's existing verify/commit/rollback contract:
the new PG document is staged until its pinned directory chain is verified,
and a competing document change or directory replacement prevents publication.
Team/Operator snapshot and event writes commit together under the original
store's shared database lock. Ordered event records keep their original IDs.

Repository/configuration files, project/role knowledge documents, Worker raw
job/session/provenance evidence, retained candidate/review evidence, artifacts,
source code and logs remain under their existing file services. They are not
copied into LangGraph. The migration command imports the business stores listed
above; it is not a generic importer for every JSON file.

An unset DB setting retains compatibility for installations that have never
migrated. A configured PG failure never selects file/SQLite storage. An explicit
import writes a local backend marker; removing the DB configuration afterward
cannot reopen the stale backup through these stores.
The marker binding is cached for the service lifetime; cutover requires stopped
writers, and the importer refreshes that cache for same-process migration tools.
Model subprocesses (including CLI discovery and Pi RPC) do not inherit the
GoodTeam database URL or dedicated test database URL.

## Conversation boundary

`manager_dialogue` uses LangGraph 1.2.20 and
`langgraph-checkpoint-postgres` 3.2.0. It wraps the existing read-only Manager CLI,
its logical message/attempt identities and Worker evidence. It does not install
the LangChain agent API, change providers, or add an execution dispatcher.

The current slice exposes only explanations, stopping the current continuation,
and proposing confirmation of an already landed batch. The model supplies an
operation kind and exact continuation identity; it cannot supply arbitrary
commands, grant parameters, Reviewer authority or delivery hashes.

Before proposing delivery, the server obtains the current batch report and Git
head and uses the original landed-candidate/quality checks. The conversation
shows the batch and exact candidate/landed commits and asks the user to confirm
they reviewed that candidate and its verification evidence. The graph then
persists an interrupt. Questions resume interpretation without accepting the
pending operation. Explicit confirmation phrases are bounded; a longer or
ambiguous statement must continue discussion rather than silently approve.

After confirmation the original delivery service revalidates the target and
returns its durable receipt. A stale target, changed authorization or intervening
Stop prevents the old confirmation. A lost graph checkpoint never replaces the
service's idempotency receipt. Interrupted service nodes are not automatically
replayed merely because another message arrived. Explicit model retries retain
the existing logical message identity and incremented attempt.

Completed/exhausted/blocked/stopped continuation transitions append a deduplicated
handoff to the original conversation, using the grant identity and its own
batch. These are status messages, not new execution authority. Asking why work
stopped does not import/reprepare a legacy project. Planner-call limits remain
separate from ordinary conversation turns.

## Explicit offline import

Do not use the live StockFlow workspace or its stopped/exhausted grants for a
migration smoke. Stop the services and Workers that write the selected workspace
before importing a real installation. Keep a complete backup of its state,
target `.goodteam` directories, configuration and evidence.

With the reviewed source and a private environment providing the DB URL:

```sh
python -m goodteam_os.database_migration \
  --workspace /absolute/workspace \
  --project-root /absolute/target-one \
  --project-root /absolute/target-two \
  --offline-confirmed
```

Pass every target whose task ledger belongs to this workspace. The command
also imports the application's configured project registry; `--registry` can
select its exact path explicitly. Preserve that same registry setting after
cutover. Backend markers also protect the registry's containing directory.
The command preserves source files, rejects duplicate/non-finite JSON, validates the legacy
Goal ledger read-only, and copies original Goal row order and full snapshots.
Business data imports share one PG transaction; an import receipt binds each
source digest. Repeating the same import does not overwrite later PG mutations.
A changed source or existing unimported PG record is a conflict. Only successful
imports write backend markers. Do not restart services after an error until it
is resolved and the complete import succeeds.

After a cutover, unsetting the URL is not a rollback. Stop writers, preserve the
current PG state and its newer receipts, and prepare a reviewed export/recovery
before changing the active backend. This patch does not perform a live cutover
or implement a reverse exporter.

## Verification

Tier: control plane. Changed contracts: persistence, request identity,
conversation handoff, exact-target delivery confirmation and bounded continuation.

The optional `tests/test_postgres_manager.py` requires
`GOODTEAM_TEST_DATABASE_URL` pointing to a dedicated disposable database. Each
case uses its own schema and isolated workspace. It covers source-preserving
import and conflicts; process restart, a question and confirmation; duplicate
message replay; Stop while waiting; the original two-run Goal/Workflow path; and
the original model-failure retry contract. Import checks compare readiness,
Team/Operator events and execution replay before and after migration, and reject
reopening stale files after removing the PG setting. The retained preparation
API scenario also runs against PG; a replaced directory prevents a staged
readiness update from committing.

The project-iteration case reuses the retained production queue/dispatch fixture:
iteration A is picked and executed in an isolated Git workspace, actual
candidate/landing evidence is confirmed in conversation, and the unchanged
continuation service learns that delivery and picks dependent iteration B.
Provider executable outputs and Manager interpretations are offline fixtures.
This is not evidence of real-model language understanding or a new real business
delivery, and does not complete #419/C3.

Run the full backend suite with that test URL, backend lint, and related
frontend unit/lint/build checks. Record actual commands, source SHA, outcomes and
skipped checks in the change's verification record. GitHub CI is paused: do not
publish candidate refs or dispatch Actions. No merge, deployment, live business
budget or additional #668 Reviewer attempt is authorized by this trial.

Observed local results (2026-11-07), production source
`01460a2fc2b065d4e210077aba67a59bedf03dc9`, base
`acb673e293f1a2fabdcb09eae28f89cbc5a38d7e`:

| Command / evidence | Observed result |
| --- | --- |
| `PYTHONPATH=src .venv/bin/python -m pytest tests -q` with the disposable test DB URL | 409 passed, 1 failed at the 30-second batch test wait; 8 fork deprecation warnings; 563.56 seconds |
| `tests/test_postgres_manager.py` within that full run | All 6 PG integration cases passed |
| Focused batch test after changing only its bounded settle wait from 30 to 60 seconds | Passed in 39.71 seconds; provider-overlap deadline, concurrency/isolation/idempotency and evidence assertions retained |
| `npm --prefix frontend test` | 12 Node and 312 Vitest checks passed |
| `npm --prefix frontend run lint`, `npm --prefix frontend run build` | Passed; retained bundle-size and ineffective-dynamic-import warnings |
| `npm --prefix frontend run test:e2e -- project-manager.spec.js business-loop.spec.js` with PG enabled | Both isolated live browser paths passed again in 38.6 seconds |
| `ruff check src/goodteam_os tests/`, `git diff --check` | Passed |

The same batch test on unmodified main took 30.02 seconds; a separate run on
the new code passed in 29.29 seconds. Its repeated full-run failures were still
collecting Git evidence at the old deadline. The final test-only wait adjustment
retains a finite bound and does not change any business execution grant. The
full suite was not repeated after that test-only adjustment; the 409 other
passing checks exercised the same production source. This is a full run plus a
targeted recheck, not a claim of one fully green suite invocation. The PR remains
Draft without final independent review or real-provider semantic acceptance.

Official package/API references:

- https://pypi.org/project/langgraph-checkpoint-postgres/
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/oss/python/langgraph/persistence
