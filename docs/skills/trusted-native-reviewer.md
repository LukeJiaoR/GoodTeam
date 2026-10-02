# Existing Codex for a trusted self-use review

The default workflow page can start one Code Review with the existing native
Codex CLI, then display its validated, saved report. The operator starts the
server with `--trusted-codex-review-policy /absolute/private/task-policy.json`.
No HTTP request, task text or imported Skill creates this startup authority.
This is separate from the strict Skill/provider envelope in #445. The blocked
`LocalCodexReviewAdmission` configuration-binding gate remains blocked.

The original native login and managed configuration remain authoritative. Codex
reads its original `CODEX_HOME` in place; GoodTeam does not copy credentials,
write provider definitions, install accounts, grant new tools, or change personal
safety settings. Invocation requests a fresh ephemeral session, the named model
and `--sandbox read-only`. Native-managed tools remain available under their
existing permissions. Task/source text is review evidence and cannot authorize
extra actions. This route rejects third-party GoodTeam methods and scripts.

The private policy is mode 0600, outside Worker storage, with exactly these
fields (placeholder paths/model must be replaced by the operator):

```json
{
  "schema_version": "goodteam.codex-trusted-review-policy.v1",
  "authorization_id": "unique-single-review-authorization",
  "worker_root": "/absolute/registered/project/root",
  "task": {"kind": "direct_task", "project_key": "PROJECT", "logical_id": "CURRENT_RUN_ID"},
  "codex": "/absolute/existing/native/codex",
  "codex_home": "/absolute/original/native/home",
  "model": "explicit-existing-model",
  "max_contexts": 1,
  "deadline_seconds": 60,
  "expires_at": 0,
  "trusted_task_inputs": true,
  "accept_native_managed_configuration": true,
  "accept_unknown_cost": true,
  "provider_request_limit": null,
  "usd_limit": null
}
```

Set `expires_at` to a future Unix timestamp within 24 hours. Permission is bound
to the exact registered project root, current run, native executable/home and
requested model. Contexts are consumed durably before execution and are never
refunded on failure, cancellation or restart. The page uses the actual selected
Team Worker and requires one configured Codex Reviewer profile with this model
at a current Code Review gate. The existing Reviewer dispatch opt-in is retained.
For a default workflow, the Run action captures only a separate direct review;
it does not migrate other workflow roles or overwrite existing queued jobs.

In **Review execution details**, click **Start review**, then **Open review
report**. Failed or malformed output is not a successful report. Cancel targets
the displayed snapshot and exact context, reaps the owned native process group,
and does not claim a remote provider request was retired. An explicit retry
reuses the original captured bytes and methods; it requires an unspent authorized
context. A changed artifact is rejected rather than substituted into a retry.
GoodTeam does not automatically retry a failed parent invocation.

The UI and saved evidence distinguish native self-use from contract fixtures.
The requested model is not independently verified as the effective provider
model. The complete managed provider/tool inventory, upstream request count,
dollar ceiling, external-tool confinement and remote request retirement remain
unverified. A context/deadline bound is not a request or cost cap. Policies naming
a numeric provider-request or USD ceiling are rejected.

Before a real call, obtain explicit authorization for the existing native route,
requested model, session count/deadline and unknown cost. Do not automatically
create a production policy from this example. A previous trial's spent context
is not authorization for another call. The isolated HTTP/browser tests use a
labelled native-shaped subprocess and synthetic login files: their reports are
model-free product-path evidence, not proof that a real model invocation succeeds.
