# Project memory retrieval with PostgreSQL

Tracked by #679. Scope: help a later Manager/Worker pickup retrieve an earlier
project lesson even when the query uses different words. This extends the
existing memory service, Worker inputs and outcome-discovery adapter.

## Configuration

1. Use the existing `GOODTEAM_DATABASE_URL` and connection pool. Install pgvector
   on that PostgreSQL server, then have the database administrator run
   `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;` in the GoodTeam
   database. The application does not install extensions or elevate privileges.
2. Save a provider in Settings. It must support `POST <base_url>/embeddings` with
   `model`, a list of `input` strings, and `encoding_format: float`. Responses
   compatibility alone does not establish this capability. A local compatible
   service can use an HTTP address and no API key.
3. Under **Project memory retrieval**, select the saved provider and an embedding
   model. The coding provider/model stays independent. Saving does not make an
   embedding request; it verifies PG storage and records the configuration.
4. New machine pickups index eligible role memory on demand. Selecting **Keep
   existing memory retrieval** disables the new path. Existing BM25 and optional
   local FastEmbed behavior remains available when the new path is disabled.

Enabling this setting authorizes sending task queries and that project's eligible
role-memory text to the selected service, across GoodTeam projects using these
operator settings. Browser memory searches remain local and do not invoke the
embedding provider. Existing task inputs remain frozen; validation may remove
superseded facts but does not retrieve replacement facts.

## Storage and source boundaries

`memory_embeddings` is a derived index in the configured GoodTeam PG schema.
It stores vectors, event IDs and content digests, scoped by the operator store,
canonical project root, role and embedding configuration. Original text remains
in the existing role memory/WAL. The shared API key stays in the provider store.

Retrieval first reads the eligible, active source snapshot, then queries PG by
that exact set of event IDs and content digests. PG cannot contribute new memory
text, another project's records, superseded events or execution/approval records.
The existing WAL/source verification still runs after retrieval and again when
validating the captured pickup. Pickup provenance records the provider revision,
embedding model and configuration revision without credentials.

New or corrected events are indexed incrementally. Saving the embedding settings
or editing the selected provider produces a new index namespace, including when
the model name remains the same. This permits an explicit rebuild after a model
changes behind an alias. Old namespaces are ignored and may be retained; this
version does not add an automatic cache-deletion policy. Deleting the derived
table is recoverable on a later pickup; it never deletes the original memory.

The first version uses pgvector's exact cosine search over the scoped event set;
it does not create an approximate HNSW index or a second full-text index. It
retains the existing semantic relevance threshold and pickup size limits.

## Bounds and unavailable retrieval

Embedding inputs use at most the first 1,600 characters of each memory event and
4,000 characters of a query. The content digest still binds the complete original
event. Each HTTP batch contains at most 32 inputs; a pickup indexes at most 128
new events, with a 30-second request budget and at most 10 seconds per HTTP
request. Provider results must have one indexed, finite, nonzero vector per
input and consistent dimensions. Redirects are rejected; calls are not retried.

An oversized initial backlog continues incrementally on subsequent authorized
pickups. Successfully indexed batches remain cached. Until indexing completes,
or when the provider/PG fails, the pickup explicitly reports
`context_status: partial` and `retrieval_unavailable`, without silently replacing
the configured retrieval with lexical results. Existing verified summaries may
still be included. Frozen pickups and delivery authority are not changed.

The provider usage totals currently cover Codex invocations only. Embedding
usage/cost aggregation is not implemented and the settings UI discloses this.
The first version indexes role memory only, not repository files or an external
knowledge base; it does not share knowledge across roles/projects automatically.

## Verification

Tier 3: control plane (provider configuration and Worker input preparation),
with isolated PG/HTTP and browser integration checks for the live path.
Implementation tested: `8c2afd09801dff8d0b32735596b687d0bf4ef74c`,
based on `e99fdc9`.
The implementation was then rebased onto
`1cfb6372e7652118551c401a93e0f1be663d0aef` as
`d769d11ae9e218c99969a686f282e57658446ced`. `git range-diff` confirmed
the RAG patch was unchanged. The new base adds the existing Provider retry fix
and UI documentation; the overlapping Worker/provider contracts receive focused
post-rebase verification instead of repeating the unchanged full suite.

`tests/test_memory_embeddings.py` uses a disposable pgvector database and an
actual local HTTP fixture to verify incremental indexing, source/model/project
isolation, correction invalidation, visible failure and rejection of redirects.
It reuses the retained two-delivery production queue/Worker/review/discovery
fixture, supplying one learned fact after the first verified delivery and
checking that it reaches the next Manager through PG retrieval.

The fixture vectors and Manager decisions are synthetic. These checks establish
the integration and source boundaries, not real-model recall quality, autonomous
learning quality or completion of #419. Real-provider semantic evaluation remains
unperformed; no paid provider or production configuration is changed.

Local verification used a disposable `pgvector/pgvector:pg18` container
(PostgreSQL 18.6, pgvector 0.8.7), isolated project fixtures and a local HTTP
embedding service. No active project workspace was used for mutating smoke.

| Check | Observed result |
| --- | --- |
| Memory core and multilingual checks | 35 passed |
| New PG retrieval and existing provider checks | 7 passed |
| Complete backend suite on `8c2afd0` | 429 passed in 858.37 seconds; 8 multiprocessing fork deprecation warnings |
| Post-rebase PG RAG and Provider retry checks on `d769d11` | 9 passed in 97.94 seconds; Ruff passed again |
| Provider settings focused UI tests | 2 passed |
| Complete frontend checks on Node 24.22.1 | 12 Node checks and 315 Vitest tests passed |
| Backend Ruff, frontend ESLint and frontend build | Passed; build retains existing bundle-size and mixed-import warnings |
| Provider/settings and project-Manager browser paths | 2 passed |
| Business-loop browser path | Failed on the historical-run summary visibility assertion; the same failure was reproduced on unmodified base `e99fdc9` |
| Mobile provider settings | Screenshot inspected; no horizontal overflow observed |
| Independent read-only review | No blocking findings on `e99fdc9..8c2afd0`; also inspected the Provider retry update on the new base |

The business-loop failure is at `frontend/e2e/business-loop.spec.js:74`
(`Historical runs are read-only and visually separate from the active run.` is
hidden); this change does not modify that UI or test.

Commands for the complete frontend and related browser checks:

```sh
npm exec --yes --package=node@24.22.1 -- npm --prefix frontend test
npm exec --yes --package=node@24.22.1 -- npm --prefix frontend run lint
npm exec --yes --package=node@24.22.1 -- npm --prefix frontend run build
npm exec --yes --package=node@24.22.1 -- npm --prefix frontend run test:e2e -- providers.spec.js project-manager.spec.js business-loop.spec.js
```

Backend commands ran from the isolated implementation worktree, using its
`src` as `PYTHONPATH` and the existing repository virtualenv. The disposable
database was supplied as `GOODTEAM_TEST_DATABASE_URL`; production
`GOODTEAM_DATABASE_URL` was unset:

```sh
python -m pytest tests -q --tb=short --junitxml=/private/tmp/goodteam-rag-679.iBuTSf/backend.xml
python -m pytest tests/test_retry_provider_binding.py tests/test_memory_embeddings.py -q --tb=short --junitxml=/private/tmp/goodteam-rag-679.iBuTSf/final-focused.xml
ruff check src/goodteam_os tests/
git diff --check
```

The initial frontend attempt used unsupported Node 25.8.0 and failed with
`localStorage.clear` errors. Its log is retained separately; the successful
results above are from the corrected Node 24.22.1 run. The browser fixture was
configured with the disposable database, not a production database.

Local logs, JUnit output and browser traces are retained in
`/private/tmp/goodteam-rag-679.iBuTSf`. GitHub CI remains paused: no candidate
publication or Actions dispatch was performed. Test fixtures do not establish
real embedding-model recall quality. No production configuration or deployment
is included.
