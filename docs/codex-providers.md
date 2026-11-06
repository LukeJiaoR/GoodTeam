# Codex API providers in GoodTeam

## Outcome and scope

A project Manager and its ordinary Codex Workers can use a connection configured
inside GoodTeam across successive iterations. The existing Agent selector still
means `codex`, `claude`, etc.; an API provider is a separate connection owned by
that Agent. The initial supported Agent is Codex and its native OpenAI Responses
protocol. There is no protocol-conversion proxy or additional dispatcher.

The previous gap was that Worker execution and model discovery relied on the
machine's Codex configuration, while Settings had no connection-management or
usage surface. This change adds a Settings section and reuses the existing
Worker, model-profile, task-binding and PostgreSQL services from merged PR #670
(base `fc6ff85fa41705c253deba7c899df35486b9e6cb`).

## Use

1. Configure the existing GoodTeam `GOODTEAM_DATABASE_URL` connection.
2. Open **Settings → Agent providers → Add provider**.
3. Enter a name, Responses Base URL (usually ending in `/v1`), optional API Key
   and default model. **Fetch models** requests `<base_url>/models`; manual model
   IDs remain available when the endpoint is unsupported or unavailable.
4. Save and choose **Use provider**. Choose **Local Codex configuration** to
   restore the previous machine-owned connection behavior.

Provider defaults replace bundled Codex model-profile defaults. Explicit Team,
role and individual job models retain precedence. Model discovery for the Team
page uses the active provider, with no invented reasoning capabilities when
`/models` supplies only model IDs.

Each new ordinary Codex job captures the provider ID and revision. Switching the
active provider affects new jobs. A queued job keeps its captured connection;
editing/removing that connection causes dispatch to reject it before invocation.
An already running subprocess keeps its captured environment. Protected task
receipts bind the optional provider reference without changing historical
receipts that predate this field. Manager runtime verification also includes the
connection identity.

Explicitly admitted native Reviewer/Correction and protected Skill runtimes keep
their existing independently authorized connections. Settings do not replace their
admission policies or grant new execution/review authority.

## Persistence and credential boundary

- Provider settings, selected connections and API Keys are PG JSONB documents in
  the existing `state_documents` table. The operator-state path ending in
  `providers/connections.json` is a document identity, not a local credential file.
- Usage observations use `provider_usage` in the same database/schema and pool.
  They are scoped to the operator-state identity, matching the provider settings.
- Writes use the existing PG lock/transaction and backend-binding mechanism.
  There is no new JSON/SQLite fallback. Without PG, this feature is unavailable;
  previous local Codex behavior remains available on an unmigrated installation.
  A missing DB configuration on a bound store fails closed.
- API reads expose `has_api_key`, never the saved value. An omitted key on edit
  preserves it; an explicit empty value removes it. Keys are passed in a dedicated
  child-process environment variable, never command arguments or job metadata.
  Provider keys are redacted from captured output; the existing database
  environment filter still removes controller DB credentials from Workers.
- Configuration and model-fetch POSTs require the local GoodTeam origin and
  JSON content type. Model requests have a timeout/response limit and reject
  redirects rather than forwarding credentials to another endpoint.

## Usage semantics

Ordinary Codex execution uses `--json` and retains its existing final-message
file. Each actual invocation records its `turn.completed` usage in PG, including
retries and completed turns before a failure/timeout. Input, cached input and
output are shown by provider/model. Total = input + output; cached input is a
subset of input. Missing usage is unknown, with reported/total invocation counts.
A telemetry persistence failure is recorded in the job result and cannot turn a
completed model call into a retry.

The feature does not import historical Codex sessions, collect separately
admitted review-runtime streams, infer charges or enforce a token/spending cap.

## Verification

Tier: **control plane**, because connection identity and credential propagation
reach Worker execution. All PG and browser runs use a disposable PostgreSQL
container and isolated workspaces, with synthetic credentials.

- `tests/test_providers.py`: four representative checks for PG restart/revision
  and secret boundaries, authenticated model discovery/redirect rejection,
  subprocess connection pinning/retry/unknown usage (including an inherited
  relative Codex model catalog), and the existing two-round project pick →
  execute → verified delivery → pick-again path with a PG provider.
- Native `codex-cli 0.164.0` smoke against a loopback Responses fixture: one
  authenticated request with the configured model; observed input 10, cached
  input 2, output 3, total 13, recorded in PG. No paid model was invoked.
- Settings component test covers fetch/select/save/activate/edit while preserving
  an undisclosed key. Browser smoke covers actual PG persistence, saved-key model
  discovery, reload, cross-origin/content-type rejection and a 390px viewport.

Local verification commands and observed results (Node 24.22.1; disposable PG
configured through `GOODTEAM_TEST_DATABASE_URL` or the browser's
`GOODTEAM_DATABASE_URL`):

| Command | Result |
| --- | --- |
| `PYTHONPATH=src .venv/bin/python -m pytest tests -v --tb=short --durations=10 -o faulthandler_timeout=90 --junitxml=/tmp/goodteam-provider-backend-final.xml` | 421 passed in 687.45s; 8 multiprocessing fork deprecation warnings |
| `cd frontend && npm test` | 12 Node tests and 313 Vitest tests passed |
| `cd frontend && npm run lint && npm run build` | Passed; existing bundle/dynamic-import warnings |
| `cd frontend && npm run test:e2e -- providers.spec.js` | 1 PG-backed browser smoke passed |
| `PYTHONPATH=src .venv/bin/ruff check src/goodteam_os tests/` | Passed |

GitHub Actions remain owner-paused; no remote CI, paid-provider integration or
deployment was performed. The native CLI smoke used a loopback fixture instead
of a live commercial provider.

These tests preserve the existing project-iteration contract; they are not new
real-model semantic acceptance or completion of the repository's #419 goal.

## References

- OpenAI Codex advanced configuration: https://developers.openai.com/codex/config-advanced
- OpenAI Codex configuration reference (`wire_api = "responses"`): https://developers.openai.com/codex/config-reference
- CC Switch provider management and model-fetch behavior: https://github.com/farion1231/cc-switch
