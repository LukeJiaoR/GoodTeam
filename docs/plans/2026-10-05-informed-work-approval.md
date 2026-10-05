# Informed Work approval: bounded #416 increment

## Scope

This implements the outstanding TaskAttention quick-approval finding in #416,
under the real-product acceptance work in #411. It is not a claim that either
issue is complete. #418/#419 are not advanced by this patch.

Only the server's explicit `approve` recommendation keeps the existing quick
path. Every other or absent recommendation uses a manual-continuation label,
shows the full existing evidence/risks brief, and requires a typed nonempty
reason. An absent/mismatched versioned target cannot become an unbound approval.

The reason and target remain inputs to the existing `onDecide` and approval API;
this patch does not create review evidence, change reviewer provenance, change
strict policy, or introduce a different ledger write. The server's action list
still determines whether any approval control exists.

The confirmation is remounted when project, run, artifact, or displayed evidence
changes. An unchanged JSON poll keeps the draft reason. An in-flight request has
a synchronous click/keyboard lock; failure retains the reason for an explicit
retry. A late response from an old confirmation cannot change the new draft.

## Verification

Risk: Tier 4 browser approval path, with the existing Tier 3 approval/binding
contract preserved. Review the actual CI head; do not infer a pass from test
source or earlier runs.

Local command actually run while preparing this increment:

```sh
node --test frontend/src/features/runs/workspace/workApprovalContext.test.js
# 4 passed, 0 failed (isolated source copy, Node 22.16.0)
```

Additional verification required before merge:

```sh
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/
```

The new component tests cover exact payloads, blank reasons, busy/duplicate
submission, failure retention, changed project/run/version/evidence, and late
responses. The new browser test uses the existing isolated fixture and real
HTTP/ledger, with fake Workers. It verifies that the reason reaches the exact
bound approval and appears in the durable action log. Test existence is not a
pass; execution evidence belongs in the PR.

## Open boundaries

- ReviewPage/DecisionActions, canvas, task-detail, legacy and non-Web approval
  entrypoints are not changed. Their informed-continuation coverage remains part
  of #416 before that issue can close; this is not server-wide reason enforcement.
- #411 still needs its real-task campaign and genuine outcome/intervention
  evidence. No provider invocation, deployment, paid bridge experiment, or real
  project mutation was performed by this increment.
- A source diagnostic rerun failed to upload on 2026-10-05 because GitHub reported
  artifact storage quota exhaustion. Do not weaken CI or fake an artifact pass to
  merge this work. Check the new PR's own execution rather than assuming the same
  failure or claiming that the quota has cleared.
