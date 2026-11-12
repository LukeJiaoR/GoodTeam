# Runtime source governs artifact routing

User outcome: explicitly chosen Runtime configuration determines whether the
existing Workflow calls a Worker for an artifact, rather than accidentally using
an internal template because of an unrelated fixed Role member.

`graph.resolve_role_adapter` previously ignored runtime_source_worker_id. It is
used by should_generate_artifact_with_worker (including web artifact revision and
RFC regeneration) and web._workflow_requires_repository before workspace setup.
It could disagree with the original dispatch resolver: source Codex/Pi versus
rostered fake chose a template; the reverse could require Git unnecessarily.

Only when a non-Manager Team Role has an explicit source, adapter probing now
reuses resolve_worker_from_state. Disabled Role or unavailable source fails with
its original TeamDispatchBlocked; no automatic fallback. Project-intake retains
its existing Research alias. No-source, legacy project and Manager routing are
unchanged. No new permission, credentials, tools, Worker pool or scheduling lock.
The actual original dispatch remains authoritative for the complete execution
contract and private prompt isolation.

The existing Role-source representative covers both routing directions and
unavailable source. Its backend doctor is injected; no real Codex/Pi process or
model call occurs. Against prior source it fails fake != codex. Focused Role,
Planner/investigation and artifact-redaction checks: 35 passed; Ruff passed.
No full suite or live model/product acceptance was run. This fixes routing,
not the unrelated trusted-Git environment or complete autonomous acceptance.

Base full role-discovery recovery SHA256:
6c3ef538113f1504d32c5480fe98f49f328138b8eca200d3f4716bfbf0989df2.
Local candidate only; not pushed, merged or deployed.

Independent review: no blocker. Reviewer reran the representative (1 passed)
and separately checked project_intake maps to Research source, disabled Role
refusal, no-source behavior, legacy manual-to-human normalization and unchanged
Manager routing. All checks were local configuration/doctor fixtures only.
