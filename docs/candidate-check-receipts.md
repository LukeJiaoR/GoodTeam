# Explicit candidate checks and acceptance visibility

This slice adds one usable path from the original captured issue contract to an
observed local command, an optional human record, and the existing Review
Workbench. Running a check does not launch a model or establish acceptance by
itself. The separate [automatic candidate review controller](automatic-candidate-review.md)
can consume an independent report and satisfy its captured review policy. The
old workflow `completed` status remains separate from the current candidate's
acceptance record.

## Run an explicitly chosen check

For a registered project with a captured issue execution, an Operator can run:

```sh
goodteam execution validate-candidate \
  --project-root /path/to/project --execution-id EXECUTION_ID \
  --head FULL_COMMIT_ID --operation-id check-1 --criterion 0 \
  --seconds 30 -- python3 -m unittest
```

Criteria are zero-based positions in the original frozen acceptance list.
Repeated text still has separate identities. Repeat `--criterion` to record more
than one mapping. A mapping expresses the Operator's intent; exit zero does not
prove that a command tests the mapped semantics. Commands are taken only from
this explicit CLI action or trusted service composition, never from issue text,
Worker claims, repository configuration, or a new HTTP command endpoint.

The local process uses an empty temporary HOME, a minimal environment without
inherited provider credentials, no interactive stdin, no `shell=True`, a maximum
120-second deadline, and a combined output budget of 1 MiB. The explicitly chosen
program can still execute code and cause side effects with the caller's OS
permissions. This is not a sandbox or permission platform. The Operator must
only choose a command they intend to authorize. Absolute interpreter paths are
supported; simple program names resolve on the system default PATH. Dependencies
must already be present; the service does not install packages or fetch Git data.

Only output SHA-256 digests, byte counts and completeness are retained, not raw
stdout/stderr, credentials or absolute working-directory paths. Nonzero exit is
a failed check; timeout, output limit or uncertain cleanup is unknown. A hash of
partial output is labeled incomplete. The protected plan retains the explicitly
supplied argv for identity comparison; the public view shows only program name
and command digest. Do not put credentials in command arguments.

## What candidate identity establishes

A full commit ID must equal the local HEAD. The service rejects staged changes,
tracked changes and nonignored untracked files. It also compares every tracked
regular file's raw bytes and executable mode with that commit's Git blob, rather
than trusting an index flag such as assume-unchanged. Unsupported sources such
as symlinks, submodules, sparse/missing files, filter-transformed bytes, or a
source over the bounded 10,000-file / 32-MiB budget block the check. The same
observation runs after the command and when displaying or replaying a result.
A moved HEAD, edited source or unavailable Git source makes the current check
unknown even if the saved historical command exited zero.

This is a clean tracked-source observation at those boundaries, not continuous
filesystem isolation. Ignored dependencies/caches, external inputs and the
runtime environment are not sealed by the Git tree. Concurrent edit-and-revert
or an unrestricted same-UID process is not excluded. No entire-environment or
semantic proof is inferred. Git inspection reuses the bounded source reader and
disables hooks, filters, replacement objects and network fallback.

## Durable attempt and human record

The original snapshot, explicit plan and independent started marker are durable
before entering the command call. The existing per-execution stable lock covers
plan selection, invocation and result publication. Repeating the same operation
with the same request returns its historical receipt after checking the current
source; it does not execute again. Different content under that operation is a
conflict. A retained started marker without its result is indeterminate. The
service does not infer that the command never ran, reconstruct empty history or
automatically retry. A separately chosen new operation is a new explicit action.
Loss or replacement of all proof is outside this application's rollback boundary.

An Operator can record a human attestation against that exact receipt and head:

```sh
goodteam execution record-review-decision \
  --project-root /path/to/project --execution-id EXECUTION_ID \
  --head FULL_COMMIT_ID --validation-receipt VALIDATION_RECEIPT_ID \
  --reviewer REVIEWER_LABEL --decision approve \
  --reason 'What was actually inspected and its limits'
```

This preserves the existing decision command; calls without the new binding
options retain legacy behavior and do not produce bound acceptance evidence.
Both binding options are required together. The head must still be clean and
current. The record binds the selected command's mapped criteria, candidate,
receipt and reviewer label. It is immutable/idempotent; a different judgment
requires a new explicitly selected operation rather than rewriting history.
The label is an Operator attestation, not an authenticated independent reviewer
identity. Unbound legacy dashboard decisions, QA text and branch CI observations remain
separate evidence and are not promoted into this record. The exact-candidate
Workbench form can now record the same bound human attestation; it cannot run a
command. See `supervised-correction-loop.md` for the connected correction rounds.

## Existing user-visible surfaces

`ExecutionAPI.get_evidence()` includes `candidate_validation`, and original
`task_input.acceptance_coverage` entries expose check and review status.
The existing `execution review` text, `/review-workbench/<execution_id>` HTML and
workbench export display the exact head, check result, machine judgment, optional
human record, current acceptance status and safe reason codes. They do not label a simulated task or
a passing check as verified delivery. Simulation is derived from the protected
original adapter (`fake`/`noop`); other adapters remain runtime-unverified here.

Only the explicitly selected latest validation operation is the current view;
prior immutable files remain history, not an automatically accumulated test
matrix. Unmapped criteria stay unknown. A passed check plus a bound machine
approval is `accepted_by_review_policy`; a passed check plus optional human
approval is `human_recorded`. Failed or unknown checks cannot be overridden by
approval. These are judgments with `semantic_verification=judgment_not_independently_verified`,
not independent semantic proof; overall verification stays `unverified`. No merge,
permission, memory-verified-experience or task-completion transition is granted.

## Verification boundary

Tests use synthetic registered projects and real temporary Git repositories and
local Python subprocesses. They exercise actual CLI-to-API/workbench rendering,
raw source mismatch, before/after drift, timeout/output limits, lost-result and
pre-call interruption recovery, and two-process same-operation deduplication.
They do not call a provider, prove a real coding task accepted, or validate a
production Runtime envelope. Browser rendering is tested as HTML, not claimed
as a real browser interaction unless separately recorded.
