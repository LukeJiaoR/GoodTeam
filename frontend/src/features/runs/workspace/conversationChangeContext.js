// This is presentation and draft handoff, never authority to change execution.
export function availableChangeTarget({ mission, attention, projectKey }) {
  if (!mission?.run_id || mission.source !== 'workflow' || mission.read_only
    || mission.project_key !== projectKey
    || ['complete', 'completed', 'stopped', 'failed', 'cancelled'].includes(mission.status)
    || attention?.intent_commit || attention?.kind !== 'approval'
    || !attention.actions?.includes('request_changes')) return null;
  const brief = attention.approval_brief;
  const target = brief?.approval_target;
  if (brief?.schema_version !== 'manager_approval_brief.v1'
    || !target || target.run_id !== mission.run_id || target.target_type !== 'review'
    || target.target_id !== attention.target_id || !target.artifact_key || !target.artifact_version) return null;
  return { ...target, approval: attention.target_id, project_key: projectKey };
}

export function changeContextStatus(intent) {
  if (!intent) return 'message';
  return ['discussing', 'ready_to_confirm', 'committing', 'ambiguous', 'stale', 'committed', 'cancelled'].includes(intent.status)
    ? intent.status : 'unknown';
}

export function matchesSavedChange(result, target, text) {
  const intent = result?.intent_commit;
  if (!intent?.id || intent.project_key !== target.project_key
    || intent.status !== 'discussing' || intent.draft_text !== text
    || intent.mission?.run_id !== target.run_id) return false;
  return ['run_id', 'target_type', 'target_id', 'artifact_key', 'artifact_version', 'task_id', 'head_sha']
    .every((key) => (intent.target?.[key] || '') === (target[key] || ''));
}
