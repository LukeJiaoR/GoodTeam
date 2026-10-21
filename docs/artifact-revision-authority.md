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
