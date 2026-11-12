# Connect the existing ordinary Role methods to the Manager task loop

This is a composition plus two actual integration fixes, not a new Runtime.

## Exact inputs

- Core Loop local candidate: discovery-refresh archive SHA256
  `0ec02c05fb51b2ddfd02f4b74952364c2650203d36f268fde572cf92005e4424`.
- Already-pushed ordinary resource/entry guard: `447b71ccc11b309ac056b89ca1c86b0e658de132`,
  two commits / seven files ahead of main `602ad62674d3c8d66e0235db56eabdf62f0b8f0b`.
- Saved reviewed first-party Role methods + ordinary Pi consumer + original
  dispatch workspace + existing authorized Web Search composition, exact file
  hashes retained in role-loop-composition.json and the original manifest.

Overlay inputs were checked against their exact old files; only trailing blank
lines were normalized in comparisons. The existing core Loop files were not
replaced. The withdrawn role-prompt wrapper and Pi read-only preparation are NOT
included. This combined tree does not enable actual Pi read-only admission.

## Actual missing connections fixed

The existing bounded-work package explicitly covers implementation and delegated
work, but implementation was absent from the new ordinary default Role mapping.
Manager-created implementation batches therefore remained legacy. They now reuse
that existing versioned method, without inventing an implementation-specific
workflow or equating identity prompts with Skill content. Custom bindings remain
authoritative, including custom-empty; historical requests keep their old mode.

A method-bearing ordinary job also discarded project_task_id / track_id before
publication. The original batch/report/observation path needs those associations.
Only ordinary_pi_methods.v1 with dispatch_isolated now retains exact associations
inside the immutable registered request and job metadata. Changed association
inputs conflict with the original job; empty fields are omitted so historical
request digests remain stable. Protected strict/read-only/review and task_input
protocols keep their old association restrictions. Associations are attribution,
not permission, and cannot alter tool or filesystem authority.

## Representative combined path

A deterministic Manager chooses two independent implementation tasks. The real
planner, queue, batch runtime selection, original dispatcher, task Skill capture,
job publication and PiMethodInput consume their original task/acceptance contracts
and shared exact method versions. Both jobs retain their own project task IDs;
neither receives the other's private task/acceptance text.

The test deliberately stops at the original Git workspace preparation boundary.
Pi capability discovery and Manager output are fixtures. Separate existing fake
RPC tests exercise the ordinary method consumer and authorized search composition;
no live Provider or paid search is called. The real Git workspace test remains
skipped when the original trusted-Git check rejects this environment; no bypass,
installation, strict-policy relaxation or claimed real-product acceptance.

This archive includes the full combined source tree, excluding caches, build
outputs and old validation directories, plus exact delta bases/evidence. It is a
saved candidate, not a pushed branch, PR, merge or deployment. Complete Role
method adherence, real Pi/Provider behavior and complete Loop acceptance remain
unverified under #445/#419.

## Results

Composed baseline without the new connections: 32 failed, 510 passed, 21 skipped, 5 errors in 81.98s (0:01:21)

Combined candidate: 32 failed, 511 passed, 21 skipped, 5 errors in 79.41s (0:01:19)

Same 37 existing failing/error IDs. Focused combined suite: 29 passed, 1 skipped. Ruff passed. Independent identity/strict-boundary review found no blocker. Existing prior feature tests are not counted as newly developed functionality.
