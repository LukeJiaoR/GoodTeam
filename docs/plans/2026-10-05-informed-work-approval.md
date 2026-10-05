# Informed Work approval: bounded #416 increment

## Scope

This implements the outstanding TaskAttention quick-approval finding in #416,
under the real-product acceptance work in #411. It is not a claim that either
issue is complete. #418/#419 are not advanced by this patch.

Only the server's explicit `approve` recommendation keeps the existing quick
path. Every other or absent recommendation uses a manual-continuation label,
shows the full existing evidence/risks brief, and requires a typed nonempty
reason. An absent/mismatched versioned target cannot become an unbound approval.
The producer's literal `missing` version is rejected, and the brief's approval
gate must agree with the action and target gate.

The reason and target remain inputs to the existing `onDecide` and approval API;
this patch does not create review evidence, change reviewer provenance, change
strict policy, or introduce a different ledger write. The server's action list
still determines whether any approval control exists.

The confirmation is remounted when project, run, artifact, or displayed evidence
changes. An unchanged payload keeps the draft reason even if object keys arrive
in a different order. Array order and actual evidence changes remain meaningful.
An in-flight request has a synchronous click/keyboard lock; failure retains the
reason for an explicit retry. A late response from an old confirmation cannot
change the new draft.

## Verification

Risk: Tier 4 browser approval path, with the existing Tier 3 approval/binding
contract preserved. Review the actual CI head; do not infer a pass from test
source or earlier runs.

Local command actually run after the first source-review corrections:

```sh
node --test frontend/src/features/runs/workspace/workApprovalContext.test.js
# 6 passed, 0 failed (isolated source copy, Node 22.16.0)
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
- A source diagnostic rerun and this PR's first CI run (#1093, head `300373af`)
  failed evidence upload on 2026-10-05 because GitHub reported artifact storage
  quota exhaustion. That run's 4 HTTP recovery tests passed, but frontend,
  browser and full backend verification were skipped. Do not weaken CI or fake
  an artifact pass to merge this work. Check the latest head's own CI result.
