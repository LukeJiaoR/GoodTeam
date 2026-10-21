# Artifact revisions and proposal authority

Editing a workflow artifact invalidates its approval gates and transitive derived
artifacts. The dependency map in `artifact_revisions.py` follows the greenfield
and fix producer pipelines. Earlier artifacts and independent sibling evidence
remain current. Historical files and audit records are retained; current artifact
references point to a new revision path, so a review of an earlier path cannot
become evidence for a later revision with identical bytes.

Gate-backed edits return to that artifact's earliest review gate. Intermediate
artifacts produced inside the bundled delivery stage have no safe resume node
that preserves an arbitrary human edit. Editing those artifacts blocks the run
with an explicit explanation rather than leaving a downstream approval actionable.
Automatic regeneration/resume for intermediate edits is not provided by this change.

## Recovering an intermediate delivery edit

For a greenfield run blocked after editing a task breakdown, UI specification,
implementation handoff, QA report, or code review:

1. In Work, inspect **Task paused**. It explains that downstream approvals need
   fresh evidence and offers **Revise upstream RFC**. Ordinary **Retry failed
   stage** is unavailable for this state; changing Worker settings is unnecessary.
2. Keep the edited report as a reference. Open **Revise upstream RFC**, choose
   **Edit Source**, and incorporate the requirements or acceptance criteria that
   the regenerated delivery must satisfy. **Save artifact** must save changed
   content; saving identical RFC bytes does not start a revision.
3. Inspect and approve the revised RFC, then inspect and approve the security
   gate again. Neither saving the RFC nor approving it alone dispatches delivery.
   This explicit upstream revision resolves only the artifact-revision blockers
   for its descendants. Unrelated blockers remain recorded.
4. Delivery runs again and produces fresh artifacts. Inspect the new QA,
   staging (when enabled), and release evidence before approving those gates.
   The manually edited intermediate report and earlier report files remain in
   history; regeneration produces a new current report rather than adopting the
   earlier manual report as fresh execution evidence.

The old downstream approval cannot be submitted while the run is blocked.
Reload an open review after recovery; proposals and approvals from the previous
revision remain stale. This procedure uses the existing artifact save and review
paths and does not add an automatic resume action.

## Dashboard mutation contract

`POST /api/artifact`, `/api/refine-artifact`, and `/api/answer-open-questions`
require `expected_run_id` and `expected_artifact_version` from the artifact read.
Missing bindings return HTTP 400; stale reads are rejected before mutation.

A refinement response and a read of `<artifact>_proposed` include
`proposal_binding` with `proposal_id`, `run_id`, `artifact_key`, `base_version`,
and `proposal_version`. Accept/discard require all five values, mapped to
`expected_proposal_id`, `expected_run_id`, `expected_artifact_key`,
`expected_artifact_version`, and `expected_proposal_version` respectively.
The server compares persisted identity and current file digests. Missing/stale
bindings return an actionable HTTP 400. Legacy proposals without identity must
be regenerated; they cannot be silently accepted by a newer client.

The frontend isolates proposal state by project, run, artifact, and review.
Navigating away discards in-memory actionability, and late responses cannot
populate a different review. A proposal must be read back and matched before
accept/discard is enabled. When refinement saves new base content, that base is
also read back and verified before displaying the proposal.

## Worker artifact ownership

The current job's complete markdown result is authoritative. Codex's output file,
stdout-based adapters, and Pi's final assistant response are captured under that
job's directory. Markdown-artifact prompts explicitly request the full document,
not a generic changed-files summary. An exact declared job-local deliverable is
also supported. Shared repository/docs files, arbitrary referenced files,
external symlinks, and adapter empty-output placeholders are not adopted.

A model that only writes a shared document and returns a summary must return the
full markdown or a job-local deliverable; otherwise refinement fails while the
human's persisted edit remains available. This change does not claim to repair
all release-note rendering or general shared-worktree attribution behavior.

## Validation boundary

Regression tests cover real persisted state, built-in model-free legacy/topo_v2
workflow execution, fix-profile invalidation, the product HTTP handler, and
real stdout-adapter capture with a synthetic subprocess transport. No paid model
or production execution is needed. Frontend routing tests cover navigation and
late responses; the separate Chromium regression requires a browser-capable
execution environment.

Focused recovery and browser commands:

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/test_artifact_revision_authority.py -q
npm run build --prefix frontend
npm run test:e2e --prefix frontend -- refinement-navigation.spec.js artifact-revision-authority.spec.js
```

The browser suite uses isolated fake-Worker projects with real HTTP mutations to
verify cross-tab proposal replacement, stale rejection without mutation, exact
accept/discard payloads, blocked-edit guidance, the RFC recovery link, renewed
RFC/security approval, regeneration, and retained historical bytes. A separate
router fixture verifies that a late proposal from a previous run cannot populate
the refreshed run. Real adapter output quality still requires a separate bounded
operator smoke; fake Workers do not establish that evidence.
