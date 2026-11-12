# Role Runtime configuration source (#445)

A Role-only task may reuse an explicitly selected active member's Runtime
configuration without creating a permanently rostered member of that Role.
This is not Skill Runtime activation or a new execution pool.

Use the existing Team command endpoint or authorized Manager Team command:

```json
{"kind":"set_role_runtime_source","role":"planner","worker_id":"research-a"}
```

The source must exist, be active, and name a model. Dispatch rechecks its current
provider eligibility and model. The task retains its own Role, task Context and
Role Skill binding. The source supplies only adapter/model/reasoning settings;
its additional prompt is not copied, even for a same-Role explicit source.
The original worker_id/name remain configuration provenance, not a new permanent
Role assignment. Existing dispatch pool, per-job identity and capacity gates are
unchanged; this selection requests one instance, not the source Role's roster
size. Runtime or isolation admission remains required.

Disabled target Roles still fail. Missing/inactive/ineligible selected sources
fail without selecting a different member or model. Named legacy member tasks
retain their strict Role match. Unconfigured Roles retain their old same-Role
selection; no Manager Runtime or cross-Role default is silently borrowed.
An explicit empty worker_id clears the selection. Team audit events, execution
snapshots and continuation authority include this choice; old grants cannot gain
it through display-name changes or replay.

The model-free representative test uses the original graph resolver and Worker
job creation/execution with a fake transport. It proves configuration reuse,
Role/prompt separation, replayable stored configuration and fail-closed gates,
not real Skill delivery, optional-resource reads or complete project autonomy.
#445's ordinary Skill Runtime consumer and admission remain incomplete.

The canonical capability view uses the same source eligibility check as dispatch:
missing, deleted, inactive, model-less or unsupported sources are degraded, never
ready. Derived legacy YAML reflects eligible selected source settings without its
prompt; unavailable selections omit that Role's derived settings instead of
reporting an old same-Role member. Legacy YAML cannot encode the full source or
failure contract and is not an execution authority; canonical Team resolution
must still gate every actual task.
