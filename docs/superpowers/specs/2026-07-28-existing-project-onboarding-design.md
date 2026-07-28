# Existing Project AI Onboarding Design

> Status: approved direction, pending written-spec review
>
> Date: 2026-07-28
>
> Scope: replace deterministic "repository understanding" with a snapshot-bound,
> model-driven onboarding workflow for imported existing projects.

## 1. Decision

GoodTeam will stop treating a bounded repository scan as project understanding.
The current scanner becomes a read-only `repository_inventory` producer. A new
`project_onboarding` Worker will use a bounded Repository Context Provider to
explore the repository over multiple model/tool turns and produce a structured,
evidence-backed `project_profile.v1`.

An existing project is ready only after:

1. a repository snapshot and low-cost inventory have been captured;
2. a real model-backed onboarding job has completed against that snapshot;
3. its profile has passed deterministic schema and evidence checks;
4. a user has accepted or corrected the profile and any declared limitations;
5. candidate commands and project constraints have been confirmed; and
6. the normal delivery Worker preflight has passed.

The code layer supplies facts and retrieval operations. It does not claim to
understand project purpose, architecture, data flows, or business risk.

## 2. Non-negotiable system invariants

The following invariants are normative. They override convenience, adapter
limitations, backward compatibility, and model capability. If GoodTeam cannot
mechanically demonstrate an invariant, it must fail closed and must not complete
`project_onboarding` or unlock the delivery workflow.

1. **The model reads the project only through the bounded Context Provider.**
   The onboarding runtime receives no direct repository mount, arbitrary shell,
   command runner, or unrestricted filesystem tool. Every repository byte shown
   to the model belongs to a persisted provider observation.
2. **All onboarding knowledge binds to one coherent repository snapshot.**
   Inventory, observations, claims, profile, review decision, confirmed commands,
   and constraints carry the same `snapshot_id`. Snapshot drift invalidates
   publication instead of producing mixed-version knowledge.
3. **Every material claim has evidence or an explicit non-evidence status.**
   A claim must cite provider observations, be marked `inferred` with its evidence
   and inference rationale, or be marked `user_confirmed` with actor and timestamp.
   Uncited model assertions are invalid.
4. **Limited coverage requires explicit acceptance.** A `partial` profile cannot
   unlock delivery through an ordinary acceptance. The user must choose
   `accept_limited`, and the accepted limitations must constrain later task
   intake.
5. **Later Workers receive the reviewed profile but reread current files.** A
   task dispatch is invalid when it contains only profile text. Its task-context
   packet must cite current-snapshot reads for issue-relevant files and tests;
   Workers must inspect current versions of files they plan to change.
6. **Onboarding neither writes to nor executes the imported project.** Job state,
   transcripts, profiles, and projections stay in the GoodTeam workspace. The
   provider exposes no project-command execution, and before/after evidence must
   show that the target root was not modified by onboarding.

These invariants are acceptance gates and test contracts, not prompt guidance.

## 3. Why the current contract is insufficient

The existing `repository_understanding` step calls `scan_existing_project()`,
which is intentionally bounded to 1,500 entries, four directory levels, and
256 KiB per selected file. It discovers manifests, key documents, candidate
commands, constraints, and entrypoint-shaped filenames. Every readiness step is
currently projected as `invokes_model: false`.

That behavior is a useful inventory and security foundation, but it cannot
support the product claim that GoodTeam understands an existing project. It does
not follow call paths, inspect domain behavior, compare implementation with tests,
or distinguish confirmed architecture from inference.

The current readiness artifact is bound to project root, execution, and attempt,
but not to repository content. The existing `.goodteam/context/` cache is also
insufficient as the new authority: it writes into the target repository, can be
generated without a model, and does not carry claim-level evidence or freshness.

## 4. Goals

- Make existing-project onboarding depend on a real model invocation.
- Preserve the current read-only import, descriptor anchoring, symlink rejection,
  atomic state publication, stale-run rejection, cancellation, and retry rules.
- Give the model bounded, auditable, provider-neutral repository retrieval tools.
- Keep every published claim bound to one coherent repository snapshot.
- Persist structured knowledge, explicit unknowns, coverage, model invocation
  evidence, tool observations, and user corrections.
- Reuse project knowledge in later tasks without allowing a stale profile to
  replace current-file inspection.
- Refresh incrementally after repository changes.
- Keep the target repository unmodified throughout onboarding unless the user
  separately authorizes a mirror operation.

## 5. Non-goals

- Reading or embedding every file in the repository.
- Executing discovered build, test, development, or deployment commands.
- Giving the onboarding model arbitrary shell or direct filesystem access.
- Building a complete language server for every supported language in the first
  implementation phase.
- Guaranteeing complete understanding of every module.
- Treating model confidence as proof.
- Allowing `fake`, `noop`, or deterministic generation to satisfy production
  onboarding readiness.
- Replacing issue-specific code inspection with a project profile.

## 6. Terminology

### Repository inventory

Deterministic facts collected without a model: bounded tree data, file-type
counts, manifests, instruction documents, command candidates, likely entrypoint
candidates, test locations, generated/vendor exclusions, large-file metadata,
and Git activity summaries.

Inventory fields that rely on conventions must be named `candidate` or
`heuristic`; they are not architecture conclusions.

### Repository snapshot

The content identity against which inventory, tool observations, model output,
and user review are bound. A snapshot is stronger than a commit SHA because an
imported repository may contain staged, unstaged, untracked, or submodule changes.

### Project profile

The canonical structured result of onboarding. Markdown documents are rendered
projections of this profile, not independent sources of truth.

### Observation and claim

An observation is a bounded Context Provider result. A claim is a profile
statement supported by observations, explicitly marked as inferred, or supplied
by a user during profile review.

## 7. Target architecture

```text
ProjectReadinessExecutor
  |
  +-- RepositorySnapshotService
  +-- RepositoryInventoryService
  +-- OnboardingBackendPreflight
  +-- ProjectOnboardingRunner
        |
        +-- ModelToolLoopAdapter
        +-- RepositoryContextProvider
        +-- OnboardingBudget
        +-- ObservationLedger
        +-- ProjectProfileValidator
  |
  +-- ProjectProfileReview
  +-- Command / Constraint Confirmation
  +-- Delivery Worker Preflight
```

The readiness executor remains the owner of lifecycle state. The onboarding
runner owns model turns and checkpoints. The Context Provider is the only
component allowed to read target-repository content for the model.

The existing `WorkerJobStore` lifecycle ideas can be reused, but onboarding jobs
must not use its current target-root storage or unrestricted project-root runtime.
Their state belongs in the GoodTeam workspace.

## 8. Persistence layout

Canonical onboarding data is stored outside the target repository:

```text
<workspace>/state/projects/<PROJECT_KEY>/
  readiness.json
  onboarding/
    snapshots/<snapshot-id>.json
    inventories/<snapshot-id>.json
    jobs/<job-id>/
      job.json
      status.json
      prompt.json
      observations.jsonl
      model-events.jsonl
      checkpoints/
    profiles/<profile-id>/
      project-profile.json
      artifact-manifest.json
      project-overview.md
      architecture.md
      module-map.md
      development-guide.md
      risk-register.md
```

Files under a completed profile version are immutable. A refresh creates a new
profile version that names the previous profile and the repository change range.
`readiness.json` stores only current refs and readiness transitions, rather than
embedding an unbounded model transcript.

Onboarding never creates or updates `<target>/.goodteam/context`. An existing
legacy context directory may be indexed as an untrusted optional source. A future
explicit export can mirror human-readable projections into a target repository,
but that is outside this design.

## 9. Repository snapshot contract

`repository_snapshot.v1` contains:

```yaml
schema: repository_snapshot.v1
snapshot_id: snap_<digest>
project_key: DEMO
canonical_root: /absolute/imported/root
root_identity:
  device: 123
  inode: 456
source_control:
  kind: git
  head_sha: <40-char-sha>
  branch: main
  dirty: true
  index_digest: sha256:...
  worktree_digest: sha256:...
  untracked_digest: sha256:...
  submodule_digest: sha256:...
content_scope:
  complete: true
  eligible_files: 412
  excluded_files: 38
  content_digest: sha256:...
captured_at: 2026-07-28T00:00:00Z
```

For a clean Git repository, `head_sha` plus submodule state identifies tracked
content. For a dirty repository, the snapshot additionally hashes the status
record and eligible staged, modified, and untracked file content. Generated,
vendor, binary, oversized, and ignored paths remain excluded and are recorded as
exclusions. For a non-Git project, `head_sha` is absent and the eligible-content
scope digest identifies the snapshot.

Snapshot scope is explicit. `complete: true` means every policy-eligible path was
represented in the snapshot identity, not that generated, ignored, binary, or
otherwise excluded content was read. If a non-Git or unusually large dirty tree
cannot be fingerprinted within the server-side snapshot ceiling, the snapshot is
`complete: false`, records the omitted path classes/counts, and forces profile
coverage to `partial` or `insufficient`. The model cannot claim facts about the
omitted scope.

`repository_inventory.v1` is a separate artifact containing `snapshot_id` and
its own `inventory_digest`. The inventory is computed after snapshot capture, so
identity is one-way: inventory refers to snapshot; snapshot does not depend on
inventory.

The snapshot service checks root device/inode before capture, after capture,
before each content observation, and before profile publication. The provider
also records a content hash for every excerpt. If relevant repository content
changes during onboarding, publication fails with `repository_snapshot_changed`.
The system never publishes a profile assembled from multiple snapshots.

## 10. Repository Context Provider

### 9.1 Security boundary

The provider opens the imported root through the existing descriptor-anchored,
no-follow boundary. Every requested path must be relative, normalized, contained
under the anchored root, and composed only of non-symlink directory entries.

The model receives no repository path that it can open directly, no arbitrary
shell, and no command-execution tool. Git inspection uses an internal allowlist
of read-only argv forms with a fixed working directory and sanitized environment.

Repository text is untrusted data. Instruction files can contribute candidate
project constraints, but cannot override GoodTeam safety policy, tool limits,
budgets, output schema, or higher-level Worker instructions.

### 9.2 Common result envelope

Every tool call returns:

```yaml
tool_call_id: obs_01J...
tool: read_file_excerpt
snapshot_id: snap_...
status: ok
truncated: false
limits:
  result_chars: 12000
observations:
- path: src/example.py
  start_line: 10
  end_line: 80
  content_hash: sha256:...
```

The full request, response metadata, returned text hash, model turn, timestamp,
and budget consumption are appended to `observations.jsonl`. Secret-like values
are redacted before persistence and before model delivery.

### 9.3 Tool surface

| Tool | Purpose | Required bounds |
| --- | --- | --- |
| `list_tree` | Explore a directory or bounded subtree. | Depth, entry count, path scope, exclusions. |
| `search_text` | Literal or regular-expression search over eligible text. | File count, match count, excerpt chars, timeout. |
| `search_symbols` | Find declarations from supported parsers or conservative textual fallbacks. | Languages, files, symbols, timeout; fallback labeled heuristic. |
| `read_file_excerpt` | Read selected line or byte ranges. | Per-call chars, per-file cumulative chars, no binary/oversized files. |
| `read_related_files` | Retrieve a small declared/import/test neighborhood. | Relationship type, fan-out, total chars. |
| `find_references` | Find bounded reference candidates. | Language-aware when available; textual results labeled candidates. |
| `find_entrypoints` | Return framework/manifest/code entrypoint candidates. | Candidate count and evidence; never presented as confirmed architecture. |
| `inspect_manifest` | Parse known manifests through structured parsers. | Manifest count and field allowlist; scripts are data only. |
| `inspect_git_history` | Read bounded commit/file activity metadata. | Commit count, time window, path scope, read-only argv allowlist. |
| `inspect_tests` | Locate tests and map test references to source candidates. | File count, mapping count, excerpt budget. |
| `inspect_recent_changes` | Summarize snapshot-relative changed paths and bounded diffs. | Commit range, files, diff chars, binary exclusion. |

The first provider release must support `list_tree`, `search_text`,
`read_file_excerpt`, `inspect_manifest`, `inspect_git_history`, `inspect_tests`,
and `inspect_recent_changes`. The remaining tools can initially use explicit,
conservative adapters for Python and JavaScript/TypeScript, then expand by
language. Unsupported semantic operations return `unsupported`, not fabricated
results.

## 11. Onboarding Worker

### 10.1 Role

The role key is `project_onboarding`, not `architect`. GoodTeam already has an
`architect` delivery role that turns approved requirements into an RFC. The new
role performs repository intake and must not produce implementation architecture
for a new feature.

Its stable boundary is:

- understand enough of an existing repository to support safe task intake;
- request evidence through Context Provider tools;
- distinguish confirmed, inferred, and unknown knowledge;
- never edit the repository or execute project commands;
- stop when the budget or evidence policy says to stop; and
- produce only the structured output contract and concise rationale.

### 10.2 Execution sequence

1. Load `repository_inventory.v1`, snapshot metadata, root instruction documents,
   manifests, README candidates, recent activity summary, and budget.
2. Identify likely project type, modules, entrypoints, risks, and evidence gaps.
3. Request focused provider observations.
4. Follow important request, data, persistence, external-provider, and test paths.
5. Emit a draft `project_profile.v1`.
6. Run deterministic schema, snapshot, citation, and minimum-coverage checks.
7. If checks expose resolvable gaps and budget remains, return gap questions to
   the same Worker session for another bounded exploration round.
8. Publish a sufficient or partial profile, or fail without publication when the
   profile is invalid, unsupported, or based on a changed snapshot.

The model is not asked to self-certify readiness. It reports knowledge and gaps;
GoodTeam applies the readiness policy.

### 10.3 Backend requirements

`onboarding_backend_preflight` runs before the onboarding job. It validates the
configured adapter, model, credentials-presence signals, tool-loop capability,
and read-only runtime mode. The completed job must record provider, model, adapter,
external invocation, timestamps, usage, and terminal status.

`fake`, `noop`, deterministic, or human adapters may exercise tests and UI
previews but yield `model_invocation_unverified` and cannot complete production
`project_onboarding`.

## 12. Budget contract

`onboarding_budget.v1` limits more than prompt size:

```yaml
max_model_turns: 24
max_tool_calls: 80
max_files_observed: 120
max_total_observation_chars: 500000
max_single_result_chars: 16000
max_file_cumulative_chars: 48000
max_elapsed_seconds: 900
max_estimated_input_tokens: 180000
max_estimated_output_tokens: 30000
max_cost_usd: 5.00
```

Values are configurable within server-side ceilings. The provider reserves
budget before executing a tool and records actual consumption afterward. Model
usage replaces estimates when the adapter reports authoritative usage.

Reaching a soft threshold asks the Worker to close gaps and finalize. Reaching a
hard threshold rejects further calls and requests a partial profile. A partial
profile is valid only when it honestly identifies uncovered areas and passes the
minimum profile contract. Budget exhaustion never becomes a complete-coverage
claim.

## 13. `project_profile.v1`

The canonical file is JSON, even when examples are shown as YAML for readability.
It contains:

```yaml
schema: project_profile.v1
profile_id: profile_...
project_key: DEMO
snapshot_id: snap_...
previous_profile_id: null
generated_by:
  job_id: onboarding_...
  adapter: codex
  provider: openai
  model: gpt-...
  external_invocation: true
project:
  purpose_claim_id: claim_...
  current_stage_claim_id: claim_...
architecture:
  overview_claim_ids: []
  modules: []
  entrypoints: []
  data_flows: []
  external_dependencies: []
operations:
  development: []
  test: []
  lint: []
  build: []
  deploy: []
constraints:
  coding_rules: []
  protected_areas: []
  prohibited_operations: []
risk:
  high_risk_modules: []
  legacy_areas: []
  weak_test_coverage: []
knowledge:
  confirmed_claim_ids: []
  inferred_claim_ids: []
  unknowns: []
coverage:
  status: partial
  dimensions: {}
  limitations: []
  recommended_followups: []
claims: []
evidence: []
created_at: 2026-07-28T00:00:00Z
```

Each claim contains `claim_id`, `kind`, `text`, `certainty`, and
`evidence_ids`. Confirmed model claims require at least one provider observation.
Inferred claims require evidence plus a concise inference explanation. User
corrections use `certainty: user_confirmed`, record actor and timestamp, and never
silently rewrite the original model claim.

Each evidence item cites an observation ID and its snapshot, path, lines,
content hash, and optional Git blob or commit identity. Line numbers are navigation
aids; content hashes preserve identity when lines later move.

## 14. Coverage and readiness policy

Coverage has three values:

- `sufficient`: all minimum dimensions are present with evidence or explicit
  user confirmation.
- `partial`: the minimum contract is met, but named modules or operational paths
  remain unexplored.
- `insufficient`: one or more minimum dimensions are missing or contradictory.

The minimum dimensions are project purpose, repository shape, at least one main
entrypoint or an explicit no-entrypoint finding, core module map, development/test
command findings, instruction/constraint review, high-risk area assessment, and
explicit unknowns.

`sufficient` can proceed to review. `partial` can proceed only through an explicit
`accept_limited` user decision that persists its limitations. `insufficient`
requires more exploration or a user-supplied correction and cannot unlock the
workflow.

This policy does not mean every module is understood. It means GoodTeam has an
evidence-backed map, knows its limitations, and will enforce those limitations
at task intake.

## 15. Readiness schema and state flow

Existing-project readiness moves to schema version 3 with this exact mode step
set:

```text
project_record
safe_preflight
project_mode
repository_inventory
onboarding_backend_preflight
project_onboarding
project_profile_review
command_verification
constraints_confirmation
worker_preflight
```

Step properties are no longer hard-coded globally:

| Step | Invokes model | Modifies target | Required artifact |
| --- | --- | --- | --- |
| `repository_inventory` | no | no | inventory + snapshot refs |
| `onboarding_backend_preflight` | no | no | preflight report |
| `project_onboarding` | yes | no | profile + invocation + observation refs |
| `project_profile_review` | no | no | review decision |
| `command_verification` | no | no | user input bound to profile/inventory |
| `constraints_confirmation` | no | no | user input bound to reviewed claims |
| `worker_preflight` | no | no | delivery preflight report |

The flow is:

```text
repository_inventory
  -> onboarding_backend_preflight
  -> project_onboarding
  -> project_profile_review
  -> command_verification
  -> constraints_confirmation
  -> worker_preflight
  -> ready
```

Profile review supports `accept`, `accept_limited`, `request_more`, and `correct`.
`request_more` creates another bounded onboarding attempt against the same
snapshot when it is still current. `correct` appends user-confirmed claims and
reruns deterministic validation.

The backend remains the sole owner of `start_workflow`. It verifies schema 3,
the exact step set, execution/attempt provenance, required immutable artifacts,
snapshot consistency, real model invocation evidence, accepted profile review,
confirmed commands/constraints, delivery preflight, and lifecycle state.

## 16. Error and recovery behavior

New stable error codes include:

| Code | Meaning | Recovery |
| --- | --- | --- |
| `repository_snapshot_changed` | Relevant content changed during onboarding. | Capture a new inventory and rerun onboarding. |
| `onboarding_backend_unavailable` | Adapter/model/tool loop is unavailable. | Configure backend, then retry preflight. |
| `onboarding_budget_exhausted` | Hard budget reached without a valid minimum profile. | Increase allowed budget or narrow/guide exploration. |
| `project_profile_invalid` | Schema, citation, or consistency validation failed. | Continue bounded gap resolution or retry Worker. |
| `project_profile_insufficient` | Minimum coverage is not met. | Request more exploration or provide corrections. |
| `model_invocation_unverified` | No acceptable external model evidence exists. | Configure a real model adapter. |
| `context_tool_limit_exceeded` | One provider request exceeded its bound. | Worker narrows the request; no automatic broad retry. |
| `context_path_rejected` | Path escaped policy, crossed a symlink, or was excluded. | Choose an eligible repository-relative path. |

Completed observations and model checkpoints are retained after interruption.
A retry may resume only when its snapshot and job identity still match; otherwise
it starts a new job and snapshot. Stale runners cannot publish artifacts.

## 17. Human-readable projections

The following files are rendered from `project-profile.json`:

- `project-overview.md`: purpose, stage, stack, coverage, and unknowns;
- `architecture.md`: evidence-backed architecture and data flows;
- `module-map.md`: module purpose, ownership candidates, entrypoints, and tests;
- `development-guide.md`: confirmed commands and operational notes;
- `risk-register.md`: high-risk, legacy, weak-test, and unknown areas.

Every projection displays profile ID, snapshot identity, coverage status, creation
time, and freshness. User review edits structured claims; projections are then
regenerated. Editing a rendered Markdown file does not mutate the profile.

## 18. Later Worker context

Later Workers receive two context layers:

```text
Project context
  reviewed profile summary
  constraints and prohibited operations
  profile snapshot/freshness/limitations

Task context
  issue and acceptance criteria
  issue-relevant profile claims
  current relevant files and tests
  changes since the profile snapshot
  on-demand Repository Context Provider
```

Task startup captures a current snapshot and computes changed paths since the
profile. Changes to root instructions, manifests, confirmed entrypoints, profile
evidence files, or high-risk modules produce `refresh_required`. Other changes
mark the profile `stale` but can proceed after task-context inspection when they
do not intersect profile limitations or relevant modules.

Workers must reread current files they plan to change. Profile text is orientation,
not current-code evidence.

## 19. Incremental refresh

Profiles are versioned rather than overwritten. An incremental refresh receives:

- the previous reviewed profile;
- previous and current snapshots;
- bounded changed-file and Git-history observations;
- claims whose evidence changed;
- modules related to changed files; and
- the same provider and budget controls.

The Worker revalidates affected claims, adds or supersedes claims, preserves
unaffected claims with their original evidence, and emits a new profile. A full
onboarding rerun is required when the root identity changes, history is rewritten
across the profile base, instruction boundaries change substantially, or the
changed-path impact cannot be bounded safely.

## 20. API and UI changes

The existing readiness start/input/retry/cancel resources remain. New or expanded
resources expose:

```text
GET  /api/projects/{key}/onboarding/jobs/{job_id}
GET  /api/projects/{key}/profiles/current
GET  /api/projects/{key}/profiles/{profile_id}
GET  /api/projects/{key}/profiles/{profile_id}/evidence/{evidence_id}
POST /api/projects/{key}/readiness/input
     step_id=project_profile_review
```

The project Home readiness center shows inventory, backend preflight, onboarding
progress, budget consumption, coverage, limitations, and recovery. The Knowledge
view renders profile sections and evidence navigation. The review UI supports
acceptance, limited acceptance, corrections, and targeted "read more" requests.

The browser never decides coverage, freshness, or workflow capability locally.

## 21. Migration

Schema 2 existing-project readiness cannot be relabeled as model onboarding.
During upgrade:

1. preserve the old `repository_understanding` artifact as legacy inventory;
2. create schema 3 steps without manufacturing model invocation evidence;
3. mark previously ready existing projects `attention_required` with
   `project_onboarding_required`;
4. keep their confirmed command and constraint inputs only when source artifacts
   can be validated against the new snapshot; otherwise request confirmation;
5. run real onboarding before restoring `start_workflow`.

This is intentionally fail-closed and changes the meaning of ready. New-project
readiness remains unchanged except for shared schema helpers. The frontend may
display legacy repository artifacts during migration, but must not label them as
an AI-generated project profile.

The legacy context generation API remains readable during transition but is not
used to satisfy onboarding. Once no active product path depends on it, target-root
generation can be deprecated separately.

## 22. Verification strategy

This is control-plane work and requires Tier 3 persisted-state validation plus a
Tier 4 isolated live path.

### Unit and security tests

- Snapshot identity for clean, dirty, staged, untracked, submodule, and non-Git
  repositories.
- Path containment, symlink traversal, replacement races, binary files, oversized
  files, ignored directories, invalid encodings, and root identity changes.
- Every provider tool's file, result, timeout, and character bounds.
- Budget reservation, accounting, soft close, and hard exhaustion.
- Prompt-injection content cannot alter provider policy or enable command access.
- Observation IDs, hashes, redaction, and snapshot consistency.
- Profile schema, claim/evidence rules, coverage, and projection determinism.
- Onboarding model runtimes have no direct target-root mount, shell, or fallback
  filesystem path when the Context Provider is unavailable.

### Executor and API tests

- Exact schema 3 step set and provenance requirements.
- Production readiness cannot complete with fake/noop/deterministic adapters.
- Model job success, failure, interruption, cancellation, retry, and stale publish.
- User accept, accept-limited, correct, request-more, and conflicting resubmission.
- Schema 2 migration fails closed without losing legacy evidence.
- Target-root before/after snapshots prove onboarding is read-only.
- Later task dispatch rejects profile-only context and persists current-snapshot
  observation refs for issue-relevant files and tests.

### Frontend tests

- Progress, partial coverage, unknowns, evidence, correction, retry, and stale
  profile states in English and Chinese.
- Backend capability remains authoritative.
- Refresh persistence and narrow mobile behavior.

### Live smoke

Use a disposable imported repository and GoodTeam workspace. Run one real model
onboarding job through the controlled provider, verify invocation evidence and
profile citations, restart the server, review the profile, complete confirmations,
and enter the workflow. Then change an evidence-bearing file and verify freshness
or refresh behavior. Never run the mutating smoke against an active project.

The implementation PRs must report the changed contract area, verification tier,
commands run, and any skipped live validation with a safety reason.

## 23. Delivery decomposition

This design is too large for one implementation PR. It will be delivered as five
reviewable tracks, each based on latest `origin/main`:

1. **Snapshot, inventory, and provider foundation**: contracts, storage, bounded
   read-only tools, observation ledger, and security tests. No readiness semantic
   change yet.
2. **Onboarding runner and profile contract**: model tool loop, budget, role,
   profile validation, projections, and deterministic fake-adapter tests.
3. **Readiness schema 3 and migration**: new steps, capability gate, recovery,
   workspace-scoped jobs, and backend APIs.
4. **Profile review product surface**: Home/Knowledge UI, evidence inspection,
   correction, limited acceptance, bilingual and browser coverage.
5. **Task context and incremental freshness**: downstream Worker pickup,
   task-context preflight, change impact, refresh, and isolated real-model smoke.

Tracks 2-5 depend on the contracts delivered before them. No track may weaken
the imported-root security boundary or claim model onboarding without invocation
evidence.

## 24. Acceptance criteria

The program is complete when all of the following are true:

1. The old scan is named and represented as inventory, not understanding.
2. Existing-project readiness includes a successfully invoked model-backed
   `project_onboarding` step.
3. The model can inspect repository content only through bounded Context Provider
   tools, and every returned observation is auditable.
4. The published profile, observations, inventory, user decision, and commands
   all bind to a coherent repository snapshot.
5. The profile distinguishes confirmed, inferred, unknown, sufficient, partial,
   and insufficient knowledge through validated structured fields.
6. Partial coverage requires explicit limited acceptance and constrains later
   task intake.
7. Onboarding writes no target-repository files and executes no project commands.
8. Later Workers receive reviewed project context plus current task context and
   reread current relevant files.
9. Repository changes produce deterministic freshness or refresh behavior rather
   than silently trusting stale summaries.
10. Backend, frontend, security, migration, restart-persistence, and isolated
    real-model smoke evidence pass at the required verification tiers.
