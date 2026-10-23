import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveAttemptMemory } from './attemptMemory.js';

const sha = 'a'.repeat(64), time = '2026-10-17T10:00:00+00:00';
function fixture() {
  return { projectKey: 'DEMO', runId: 'run-one', inspection: { available: true,
    summary: { project_key: 'DEMO', run_id: 'run-one' },
    attempt: { id: 'job-one', role: 'architect', adapter: 'noop', memory_observations: {
      schema_version: 'goodteam.attempt-memory.v1', project_key: 'DEMO', run_id: 'run-one',
      attempt_id: 'job-one', role: 'architect', adapter: 'noop', status: 'observed', read_only: true,
      capture: { captured_at: time, packet_digest: sha },
      preparation_count: 1, omitted_preparations: 0, transport_attempt_count: 1,
      preparations: [{ id: 'prepared-one', prepared_at: time, input_digest: sha, fragment_digest: sha,
        packet_digest: sha, capture_packet_match: true, context_status: 'partial', citation_count: 2, rendered_source_count: 1 }],
      writeback_count: 1, omitted_writebacks: 0, writebacks: [{ id: 'write-one', kind: 'result', observed_at: time,
        result_digest: sha, outcome: 'unknown', counts_complete: false, parsed: 3, attempted: 1,
        appended: 1, replayed: 1, deduplicated: 0, unattempted: 1, omitted_observations: 0 }],
    } },
  } };
}
const payload = input => input.inspection.attempt.memory_observations;

test('keeps exact host observations and partial write counts without asserting provider reads', () => {
  const result = resolveAttemptMemory(fixture());
  assert.equal(result.status, 'observed');
  assert.equal(result.preparations[0].context_status, 'partial');
  assert.equal(result.writebacks[0].counts_complete, false);
  assert.equal(result.writebacks[0].appended, 1);
  assert.equal(result.writebacks[0].replayed, 1);
  assert.equal(result.providerRead, 'unknown');
  assert.equal(result.providerReceipt, 'unknown');
});

test('legacy and unsupported attempts stay unknown without invented zero counts', () => {
  const input = fixture();
  delete input.inspection.attempt.memory_observations;
  assert.deepEqual(resolveAttemptMemory(input), { status: 'unknown', reason: 'not_recorded', providerRead: 'unknown', providerReceipt: 'unknown' });
  input.inspection.attempt.memory_observations = { ...payload(fixture()), status: 'unknown', reason: 'unsupported_attempt' };
  const result = resolveAttemptMemory(input);
  assert.equal(result.reason, 'unsupported_attempt');
  assert.equal(result.status, 'unknown');
  assert.equal(Object.hasOwn(result, 'writebackCount'), false);
});

for (const field of ['project_key', 'run_id', 'attempt_id', 'role', 'adapter', 'schema_version']) {
  test(`rejects a foreign or stale ${field} receipt`, () => {
    const input = fixture(); payload(input)[field] = 'other';
    assert.equal(resolveAttemptMemory(input).status, 'unavailable');
    assert.equal(Object.hasOwn(resolveAttemptMemory(input), 'preparations'), false);
  });
}

test('rejects stale inspection, cross-run summary, replaced attempt, and route return reuse', () => {
  for (const change of [
    input => { input.inspection.available = false; },
    input => { input.inspection.summary.run_id = 'newer'; },
    input => { input.inspection.attempt.id = 'new-job'; },
    input => { input.projectKey = 'OTHER'; },
  ]) {
    const input = fixture(); change(input);
    assert.equal(resolveAttemptMemory(input).status, 'unavailable');
  }
});

test('rejects duplicate and malformed observations instead of retaining older facts', () => {
  for (const change of [
    value => { value.preparation_count += 1; },
    value => { value.writebacks[0].appended = true; },
    value => { value.writebacks[0].id = value.preparations[0].id; },
    value => { value.preparations[0].input_digest = 'private-path'; },
    value => { value.capture = null; },
  ]) {
    const input = fixture(); change(payload(input));
    assert.equal(resolveAttemptMemory(input).status, 'unavailable');
  }
});

test('does not disclose raw fields or accept unsupported provider claims', () => {
  const input = fixture();
  Object.assign(payload(input), { provider_receipt: 'received', provider_read: 'confirmed', private: 'SECRET' });
  payload(input).writebacks[0].private = 'SECRET';
  const result = resolveAttemptMemory(input);
  assert.equal(result.providerRead, 'unknown');
  assert.equal(result.providerReceipt, 'unknown');
  assert.equal(JSON.stringify(result).includes('SECRET'), false);
});

test('capture without preparation or writeback remains distinct from zero memory use', () => {
  const input = fixture();
  Object.assign(payload(input), { preparation_count: 0, preparations: [], transport_attempt_count: 0, writeback_count: 0, writebacks: [] });
  const result = resolveAttemptMemory(input);
  assert.equal(result.status, 'observed');
  assert.equal(result.preparationCount, 0);
  assert.equal(result.capture.captured_at, time);
  assert.equal(result.providerRead, 'unknown');
});

test('capture match must agree with the actual preparation digest, including absent packets', () => {
  for (const [packet, match, expected] of [
    ['b'.repeat(64), true, 'unavailable'], [sha, false, 'unavailable'], [null, true, 'unavailable'],
    ['b'.repeat(64), false, 'observed'], [null, false, 'observed'],
  ]) {
    const input = fixture();
    Object.assign(payload(input).preparations[0], { packet_digest: packet, capture_packet_match: match });
    assert.equal(resolveAttemptMemory(input).status, expected);
  }
});
