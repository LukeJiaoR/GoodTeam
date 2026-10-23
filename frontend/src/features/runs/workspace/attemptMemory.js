const count = value => Number.isSafeInteger(value) && value >= 0;
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const recordedTime = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
const unknown = (reason = 'not_recorded', status = 'unknown') => ({ status, reason, providerReceipt: 'unknown', providerRead: 'unknown' });
const pick = (value, keys) => Object.fromEntries(keys.map(key => [key, value[key]]));
const preparationFields = ['id', 'prepared_at', 'input_digest', 'fragment_digest', 'packet_digest', 'capture_packet_match', 'context_status', 'citation_count', 'rendered_source_count'];
const writebackFields = ['id', 'kind', 'observed_at', 'outcome', 'result_digest', 'counts_complete', 'parsed', 'attempted', 'appended', 'replayed', 'deduplicated', 'unattempted', 'omitted_observations'];
const outcomes = new Set(['completed', 'skipped', 'unknown', 'partial', 'known_not_committed']);

// A role, mutable job citation, or neighboring attempt is never a receipt.
// Keep all provider receipt/read assertions unknown, even if a future payload
// adds an unsupported positive claim to this version of the public contract.
export function resolveAttemptMemory({ inspection, projectKey, runId }) {
  if (inspection?.available !== true) return unknown('scope_mismatch', 'unavailable');
  const { attempt, summary } = inspection;
  const value = attempt?.memory_observations;
  if (!value) return unknown();
  if (summary?.project_key !== projectKey || summary?.run_id !== runId
    || value.schema_version !== 'goodteam.attempt-memory.v1' || value.read_only !== true
    || value.project_key !== projectKey || value.run_id !== runId || value.attempt_id !== attempt.id
    || value.role !== attempt.role || value.adapter !== attempt.adapter) return unknown('scope_mismatch', 'unavailable');
  if (value.status !== 'observed') {
    const reason = ['not_recorded', 'unsupported_attempt', 'scope_mismatch', 'records_unavailable'].includes(value.reason) ? value.reason : 'records_unavailable';
    return unknown(reason, value.status === 'unknown' ? 'unknown' : 'unavailable');
  }
  const preparations = value.preparations, writebacks = value.writebacks;
  if (!Array.isArray(preparations) || !Array.isArray(writebacks) || preparations.length > 8 || writebacks.length > 8
    || !['preparation_count', 'omitted_preparations', 'transport_attempt_count', 'writeback_count', 'omitted_writebacks'].every(key => count(value[key]))
    || value.preparation_count !== preparations.length + value.omitted_preparations
    || value.writeback_count !== writebacks.length + value.omitted_writebacks
    || !recordedTime(value.capture?.captured_at) || !digest(value.capture?.packet_digest)
    || preparations.some(item => !item || typeof item.id !== 'string' || !item.id || !recordedTime(item.prepared_at)
      || !digest(item.input_digest) || !digest(item.fragment_digest) || (item.packet_digest !== null && !digest(item.packet_digest))
      || typeof item.capture_packet_match !== 'boolean' || !['available', 'partial', 'unavailable', 'unknown'].includes(item.context_status)
      || item.capture_packet_match !== (item.packet_digest === value.capture.packet_digest)
      || !count(item.citation_count) || !count(item.rendered_source_count))
    || writebacks.some(item => !item || typeof item.id !== 'string' || !item.id || !recordedTime(item.observed_at)
      || !['result', 'provenance'].includes(item.kind) || !outcomes.has(item.outcome) || !digest(item.result_digest)
      || typeof item.counts_complete !== 'boolean'
      || !['parsed', 'attempted', 'appended', 'replayed', 'deduplicated', 'unattempted', 'omitted_observations'].every(key => count(item[key])))
    || new Set([...preparations, ...writebacks].map(item => item.id)).size !== preparations.length + writebacks.length) {
    return unknown('records_unavailable', 'unavailable');
  }
  return {
    ...unknown('', 'observed'), capture: pick(value.capture, ['captured_at', 'packet_digest']),
    preparationCount: value.preparation_count, preparations: preparations.map(item => pick(item, preparationFields)),
    omittedPreparations: value.omitted_preparations, transportAttemptCount: value.transport_attempt_count,
    writebackCount: value.writeback_count, writebacks: writebacks.map(item => pick(item, writebackFields)),
    omittedWritebacks: value.omitted_writebacks,
  };
}
