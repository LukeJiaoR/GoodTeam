const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const digest = value => typeof value === 'string' && /^sha256:[0-9a-f]{64}$/.test(value);

// This validates a task-scoped read contract, never execution authority or proof
// that a runtime received, read, or followed a captured method.
export function validTaskInputContext(payload, projectKey, runId) {
  if (!payload || payload.schema_version !== 'goodteam.task-input-context.v1'
    || payload.project_key !== projectKey || payload.run_id !== runId
    || payload.task_kind !== 'workflow_run' || payload.read_only !== true
    || (payload.error_code !== undefined && typeof payload.error_code !== 'string')
    || typeof payload.captured_at !== 'string'
    || !(payload.team_revision === null || (Number.isSafeInteger(payload.team_revision) && payload.team_revision >= 0))
    || !Array.isArray(payload.subjects) || payload.subjects.length > 64) return false;
  const subjects = new Set();
  for (const subject of payload.subjects) {
    if (!subject || !nonempty(subject.subject) || subjects.has(subject.subject)
      || !['default', 'custom'].includes(subject.mode)
      || !Array.isArray(subject.diagnostics) || !subject.diagnostics.every(item => nonempty(item?.code))
      || !Array.isArray(subject.methods)) return false;
    subjects.add(subject.subject);
    const methods = new Set();
    for (const method of subject.methods) {
      if (!method || !nonempty(method.skill_id) || methods.has(method.skill_id)
        || !nonempty(method.version_id) || !digest(method.package_digest)
        || !['required', 'on_demand'].includes(method.activation)) return false;
      methods.add(method.skill_id);
    }
  }
  if (payload.capture_status === 'unavailable') {
    return payload.input_origin === 'unavailable' && payload.input === null && !payload.subjects.length;
  }
  const input = payload.input;
  if (!input || !['exact_request', 'source_type', 'delivery_mode'].every(key => typeof input[key] === 'string')
    || !Array.isArray(input.delivery_standards) || !input.delivery_standards.every(value => typeof value === 'string')) return false;
  if (payload.capture_status === 'legacy_unverified') {
    return payload.input_origin === 'workflow_state' && payload.task_lock_ref === null
      && payload.team_revision === null && !payload.subjects.length;
  }
  return payload.capture_status === 'locked' && payload.input_origin === 'task_start_lock'
    && Number.isSafeInteger(payload.team_revision) && nonempty(payload.captured_at)
    && nonempty(payload.task_lock_ref?.id) && digest(payload.task_lock_ref?.digest);
}
