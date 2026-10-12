const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const unavailable = reason => ({ available: false, reason });

// Read-only presentation join. The server selects the current attempt; a role,
// timestamp, result filename or array position must never substitute for its ID.
export function resolveAttemptInspection({ data, projectKey, runId, actorId, attemptId, freshness = 'ready' }) {
  if (freshness !== 'ready') return unavailable('stale');
  const workspace = data?.team_workspace;
  const mission = workspace?.mission;
  const summary = data?.delivery_summary;
  if (![projectKey, runId, actorId, attemptId].every(nonempty)
    || workspace?.schema_version !== 'team_workspace.v1'
    || mission?.source !== 'workflow' || mission.project_key !== projectKey || mission.run_id !== runId
    || data?.state?.run_id !== runId || (data.state.project_key && data.state.project_key !== projectKey)
    || summary?.project_key !== projectKey || summary.run_id !== runId) return unavailable('scope');

  const actors = Array.isArray(workspace.execution_actors) ? workspace.execution_actors : [];
  const matches = actors.filter(actor => actor?.id === actorId && actor.role !== 'orchestrator');
  if (matches.length !== 1) return unavailable(matches.length ? 'ambiguous' : 'missing');
  const actor = matches[0];
  if (actor.current_attempt_id !== attemptId) return unavailable('changed');
  if (actors.filter(item => item?.role !== 'orchestrator' && item?.current_attempt_id === attemptId).length !== 1) {
    return unavailable('ambiguous');
  }
  const attempts = Array.isArray(summary.attempts) ? summary.attempts : [];
  const records = attempts.filter(attempt => attempt?.id === attemptId);
  if (records.length !== 1) return unavailable(records.length ? 'ambiguous' : 'missing');
  const attempt = records[0];
  if ((attempt.run_id && attempt.run_id !== runId) || (attempt.project_key && attempt.project_key !== projectKey)
    || (actor.role && attempt.role && actor.role !== attempt.role)) return unavailable('scope');

  // Legacy public job rows lack run/project fields. Membership in the exact
  // server-filtered run summary above anchors their optional display facts.
  // Historical run detail has no worker_jobs and remains useful without them.
  const jobs = (Array.isArray(data.worker_jobs) ? data.worker_jobs : []).filter(job => job?.id === attemptId);
  if (jobs.length > 1) return unavailable('ambiguous');
  const job = jobs[0] || null;
  if (job) {
    const task = job.task_skill_context?.task;
    if ((job.run_id && job.run_id !== runId) || (job.project_key && job.project_key !== projectKey)
      || (job.role && attempt.role && job.role !== attempt.role)
      || (job.task_skill_context && (task?.kind !== 'workflow_run'
        || task.project_key !== projectKey || task.logical_id !== runId))) return unavailable('scope');
  }
  return { available: true, actor, attempt, job, summary };
}
