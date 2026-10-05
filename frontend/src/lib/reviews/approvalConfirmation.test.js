import assert from 'node:assert/strict';
import test from 'node:test';
import { approvalConfirmationKey, canConfirmApproval, requiresInformedApproval } from './approvalConfirmation.js';

for (const verdict of [undefined, null, '', 'caution', 'reject', 'request_changes', 'unknown']) {
  test(`non-positive recommendation ${String(verdict)} needs an informed reason`, () => {
    const requiresReason = requiresInformedApproval({ verdict });
    assert.equal(requiresReason, true);
    for (const note of ['', '   \n', null, 123, {}]) {
      assert.equal(canConfirmApproval({ requiresReason, note }), false);
    }
    assert.equal(canConfirmApproval({ requiresReason, note: 'I accept this bounded gap.' }), true);
  });
}

test('explicit positive recommendation preserves normal approval without requiring CI or a note', () => {
  const requiresReason = requiresInformedApproval({ verdict: 'approve' });
  assert.equal(requiresReason, false);
  assert.equal(canConfirmApproval({ requiresReason }), true);
});

test('an informed reason cannot bypass a disabled approval or active mutation', () => {
  for (const requiresReason of [true, false]) {
    assert.equal(canConfirmApproval({ requiresReason, disabled: true, note: 'Checked.' }), false);
  }
});

test('unknown or absent recommendation never silently permits empty approval', () => {
  assert.equal(requiresInformedApproval(null), true);
  assert.equal(requiresInformedApproval(undefined), true);
  assert.equal(canConfirmApproval(), false);
});

test('confirmation key ignores object property order but retains real evidence changes', () => {
  const value = ['A', 'run-1', { gate: 'prd', version: 'v1', evidence: { status: 'missing', count: 1 } }];
  const reordered = ['A', 'run-1', { evidence: { count: 1, status: 'missing' }, version: 'v1', gate: 'prd' }];
  assert.equal(approvalConfirmationKey(value), approvalConfirmationKey(reordered));
  reordered[2].evidence.status = 'failed';
  assert.notEqual(approvalConfirmationKey(value), approvalConfirmationKey(reordered));
});

test('project, run, gate, artifact version and evidence order isolate confirmation drafts', () => {
  const value = ['A', 'run-1', 'prd', 'v1', ['review', 'qa']];
  const baseline = approvalConfirmationKey(value);
  for (let index = 0; index < 4; index += 1) {
    const next = structuredClone(value); next[index] = 'changed';
    assert.notEqual(approvalConfirmationKey(next), baseline);
  }
  const next = structuredClone(value); next[4].reverse();
  assert.notEqual(approvalConfirmationKey(next), baseline);
});
