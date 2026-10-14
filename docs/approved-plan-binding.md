# Approved intent in normal candidate delivery

The local candidate delivery path and configured Operator execution now require
an explicit association to an existing approved workflow plan. This adds no
Planner approval gate. Greenfield authority remains the existing RFC and
security decisions on the exact same RFC bytes; the fix profile continues to
use `fix_plan`. A Planner-derived task is never described as human-approved.

## Local workflow

1. Complete the existing workflow RFC/security reviews, or the fix-plan review.
   The approval log must retain human decision operation IDs, exact artifact
   versions and project scope. No command below manufactures these decisions.
2. Prepare the derived task breakdown through the trusted planning process. Its
   existing Markdown task must have a unique heading at levels 2–6 and a nonempty
   body describing the bounded work. For fix workflows, select the task heading
   in the approved fix plan itself. A heading is selected by exact text, not a
   fuzzy title, issue label or semantic guess.
3. Intake the issue normally. Keep the returned execution ID. Before dispatch,
   explicitly associate that captured issue with the selected task:

   ```sh
   goodteam execution bind-plan --project-root /isolated/project \
     --execution-id '<captured execution ID>' \
     --workflow-run '<existing current workflow run ID>' \
     --task-anchor 'Implement the selected feature'
   ```

4. Run the existing `execution run` command with its local validation command,
   criterion indices and review base, or use the existing trusted Operator
   configuration. No workflow run ID is inferred from issue content, queue
   metadata, or whichever project happens to be selected in the UI.

Registration is an explicit **local derivation**, not semantic proof that an
arbitrary issue implements the plan. The captured issue task ID and input digest
are mapped to the actual selected heading, its bytes, and the whole breakdown's
version. Missing or duplicate headings are rejected. The full approved intent,
approval operation identities, and selected derived task are carried into the
Builder and independent Reviewer contexts. The Reviewer must assess conflicts
and scope against that intent in addition to the original acceptance criteria.
No program can establish semantic conformity from a matching digest alone.

## Failure and recovery

The binding is stored with the protected execution input, outside the Worker
checkout, with a separate required marker. Registration is commit-once and can
be replayed exactly. It is rejected after Builder execution or review capture;
it cannot retroactively describe already-executed work as approved-plan-bound.

Candidate delivery refuses missing bindings before dispatch. Every bound Builder
attempt, direct Worker retry, correction input, and Reviewer context rechecks the
current workflow run, project, latest relevant human decisions, operation IDs,
artifact paths, full artifact versions and task membership. Changed intent,
revoked/missing decisions, changed breakdown, another active run, a missing
binding/marker or project mismatch all fail closed. No check dispatches a model
or grants runtime admission, publication, or external access.

Older action logs omitted project scope. Their existing durable transition
checkpoints can prove it: the checkpoint must match the project, run, exact
operation and decision/artifact fields. Log location alone is insufficient.
An older approval without that proof, an operation ID or a matching artifact
version is insufficient. Review the current intent through the existing
workflow's review/revision flow (or start a fresh workflow when the old run is
no longer actionable), then register an unexecuted task. Do not edit historical
logs, fill in synthetic approvals or point old bindings at a new run. Once a
binding exists its exact source association is immutable: a changed plan needs a
fresh captured task/execution and new explicit association after reapproval.
The issue intake identity is per repository/issue, so a fresh issue is necessary
when the old execution is already bound. This bounded slice does not implement
in-place approval rebasing or task-state revision/CAS.

The unbound Builder-only API keeps its existing contract. It does not claim to
have approved-plan authority and cannot use normal candidate delivery. Once an
execution is bound, even a Builder-only retry validates its retained binding.
Standalone validation/review remains available without a workflow binding for
its existing use cases; it likewise does not claim approved-plan provenance.
The old graph-only implementation path is not migrated by this change.

## Verification

Tier 4 control-plane and dispatch change. Tests exercise real existing decision
persistence and offline local Builder/check/Reviewer processes in temporary
workspaces, including repeated and interrupted operations. No live provider,
production workspace, deployment, permission change or external model is needed.
