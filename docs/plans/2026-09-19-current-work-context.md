# Step 1: persistent current task context (#417)

The compact Work context bar stays outside the optional hover drawer. It names
this project/task, projects the current state, and links to one existing surface.
It never approves, retries, starts work, confirms a change, or calls a model.
The drawer remains available for detailed stage/history information.

State freshness takes precedence over positive status. Historical results are
read-only and do not inherit live attention. Execution ending is not acceptance.
Project navigation remounts the Work page; historical reads are bound to their
requested project/run and ignore aborted responses. Focus also checks the
project/run scope before a scheduled scroll can run.

## Verification contract

Tier 4 presentation/navigation plus Tier 2 read-only projection. No backend,
ledger, approval, Worker dispatch, dependency, or permanent CI changes.

Added helper/component regressions cover stale/unknown data, project mismatch,
read-only history, draft cancellation, pending/uncertain change states, exact
focus scope and bilingual rendering. Added real HTTP/fake Worker browser cases
cover draft/preparation/approval/re-entry, no-mutation navigation and light/dark
desktop screenshots. Existing acceptance and recovery suites remain mandatory.

Local validation is static source review and `git diff --check` only, following
the CI-owned execution boundary. Exact-head CI results and screenshot inspection
must be reported in the PR before this step is considered verified. No local
tests/build/live model calls or real workspace mutations were performed.

## Product acceptance

This code change is not a successful real-model dogfood task. #411 and #419 stay
open until actual opt-in delivery evidence exists. The earlier paused real-model
workflow is not resumed by this step.
