import assert from 'node:assert/strict';
import test from 'node:test';
import { requiresInformedWorkApproval, workApprovalContext } from './workApprovalContext.js';

function input() {
  return {
    projectKey: 'A', gate: 'rfc',
    brief: {
      schema_version: 'manager_approval_brief.v1', approval: 'rfc',
      approval_target: {
        run_id: 'run-1', target_type: 'review', target_id: 'rfc',
        artifact_key: 'rfc', artifact_version: 'sha256:abc', task_id: 'task-1',
      },
      recommendation: { verdict: 'caution', reasons: [{ kind: 'missing_evidence', text: 'Missing review.' }] },
      evidence: [{ kind: 'reviewer', status: 'missing' }],
    },
  };
}

test('only an explicit positive recommendation permits the existing quick path', () => {
  for (const verdict of [undefined, null, '', 'caution', 'request_changes', 'reject', 'unknown']) {
    assert.equal(requiresInformedWorkApproval({ recommendation: { verdict } }), true);
  }
  assert.equal(requiresInformedWorkApproval(null), true);
  assert.equal(requiresInformedWorkApproval({ recommendation: { verdict: 'approve' } }), false);
});

test('confirmation preserves the exact server target without supplying defaults', () => {
  const value = input();
  const context = workApprovalContext(value);
  assert.deepEqual(context.target, value.brief.approval_target);
  assert.notEqual(context.target, value.brief.approval_target);
  assert.equal(context.requiresReason, true);
});

test('missing or mismatched binding cannot become an unbound quick approval', () => {
  for (const field of ['run_id', 'artifact_key', 'artifact_version', 'target_id', 'target_type']) {
    for (const replacement of [undefined, null, '', '   ']) {
      const value = input();
      value.brief.approval_target[field] = replacement;
      assert.equal(workApprovalContext(value), null, `${field}: ${replacement}`);
    }
  }
  for (const field of ['gate', 'projectKey']) {
    const value = input(); value[field] = '';
    assert.equal(workApprovalContext(value), null);
  }
  const value = input(); value.brief.approval_target.target_id = 'security';
  assert.equal(workApprovalContext(value), null);
  assert.equal(workApprovalContext({ ...input(), brief: null }), null);
});

test('project, run, artifact, and new evidence each invalidate an old reason', () => {
  const baseline = workApprovalContext(input()).key;
  const changes = [
    value => { value.projectKey = 'B'; },
    value => { value.brief.approval_target.run_id = 'run-2'; },
    value => { value.brief.approval_target.artifact_version = 'sha256:def'; },
    value => { value.brief.recommendation.reasons[0].text = 'A different gap.'; },
    value => { value.brief.evidence[0].status = 'failed'; },
  ];
  for (const change of changes) {
    const value = input(); change(value);
    assert.notEqual(workApprovalContext(value).key, baseline);
  }
  assert.equal(workApprovalContext(input()).key, baseline);
});


test('rejects the producer missing-version marker and a contradictory brief gate', () => {
  const missing = input(); missing.brief.approval_target.artifact_version = 'missing';
  assert.equal(workApprovalContext(missing), null);
  const mismatch = input(); mismatch.brief.approval = 'security';
  assert.equal(workApprovalContext(mismatch), null);
  const absent = input(); delete absent.brief.approval;
  assert.equal(workApprovalContext(absent), null);
});

test('key order alone does not discard a reason but evidence order remains meaningful', () => {
  const value = input();
  value.brief.evidence.push({ kind: 'ci', status: 'not_run' });
  const reordered = input();
  reordered.brief = Object.fromEntries(Object.entries(value.brief).reverse());
  reordered.brief.approval_target = Object.fromEntries(Object.entries(value.brief.approval_target).reverse());
  assert.equal(workApprovalContext(reordered).key, workApprovalContext(value).key);
  reordered.brief.evidence = [...value.brief.evidence].reverse();
  assert.notEqual(workApprovalContext(reordered).key, workApprovalContext(value).key);
});
