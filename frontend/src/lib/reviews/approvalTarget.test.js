import assert from 'node:assert/strict';
import test from 'node:test';
import { usableApprovalTarget, requiresInformedApproval, canConfirmApproval, approvalConfirmationKey } from './approvalConfirmation.js';

const target = () => ({ run_id: 'run-1', target_type: 'review', target_id: 'prd', artifact_key: 'prd', artifact_version: 'sha256:v1' });

test('the exact displayed target is required; no current-run or artifact defaults', () => {
  assert.equal(usableApprovalTarget(target()), true);
  for (const field of ['run_id', 'target_id', 'artifact_key', 'artifact_version']) {
    for (const value of ['', ' ', undefined, null, 123]) {
      assert.equal(usableApprovalTarget({ ...target(), [field]: value }), false);
    }
  }
  assert.equal(usableApprovalTarget(null), false);
  assert.equal(usableApprovalTarget({ ...target(), target_type: 'task' }), false);
});

test('an absent material marker is not a version', () => {
  for (const artifact_version of ['missing', ' missing ']) {
    assert.equal(usableApprovalTarget({ ...target(), artifact_version }), false);
  }
});

test('Canvas and shortcuts cannot treat unknown recommendations as approval', () => {
  for (const verdict of [undefined, '', 'caution', 'reject', 'request_changes']) {
    assert.equal(requiresInformedApproval({ verdict }), true);
    assert.equal(canConfirmApproval({ requiresReason: requiresInformedApproval({ verdict }), note: ' \n' }), false);
  }
  assert.equal(requiresInformedApproval({ verdict: 'approve' }), false);
});

test('bound confirmations reset for a changed run or evidence, not reordered object keys', () => {
  const value = ['DEMO', target(), { verdict: 'caution', reasons: ['Missing reviewer'] }];
  const same = structuredClone(value);
  same[1] = Object.fromEntries(Object.entries(same[1]).reverse());
  assert.equal(approvalConfirmationKey(value), approvalConfirmationKey(same));
  same[1].run_id = 'run-2';
  assert.notEqual(approvalConfirmationKey(value), approvalConfirmationKey(same));
  same[1].run_id = 'run-1';
  same[2].reasons = ['Reviewer failed'];
  assert.notEqual(approvalConfirmationKey(value), approvalConfirmationKey(same));
});
