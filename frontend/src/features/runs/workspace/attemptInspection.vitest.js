import { describe, expect, it } from 'vitest';
import { resolveAttemptInspection } from './attemptInspection';

const input = () => ({
  projectKey: 'A', runId: 'run-a', actorId: 'assignment-1', attemptId: 'job-1',
  data: {
    state: { project_key: 'A', run_id: 'run-a' },
    team_workspace: { schema_version: 'team_workspace.v1',
      mission: { source: 'workflow', project_key: 'A', run_id: 'run-a' },
      execution_actors: [{ id: 'assignment-1', current_attempt_id: 'job-1', role: 'implementation', state: 'completed' }],
    },
    delivery_summary: { project_key: 'A', run_id: 'run-a', attempts: [
      { id: 'job-1', role: 'implementation', status: 'failed', updated_at: '2026-10-12T00:00:00Z' },
    ], artifacts: [] },
    worker_jobs: [{ id: 'job-1', role: 'implementation', summary: 'Saved attempt summary' }],
  },
});

describe('current attempt inspection binding', () => {
  it('uses the server assignment and keeps attempt and assignment states separate', () => {
    const value = input();
    const result = resolveAttemptInspection(value);
    expect(result).toMatchObject({ available: true, actor: { state: 'completed' }, attempt: { status: 'failed' }, job: { summary: 'Saved attempt summary' } });
    expect(result.summary).toBe(value.data.delivery_summary);
  });

  it('supports read-only history without optional worker details', () => {
    const value = input();
    value.data.team_workspace.mission.read_only = true;
    delete value.data.worker_jobs;
    expect(resolveAttemptInspection(value)).toMatchObject({ available: true, job: null });
  });

  it.each(['missionProject', 'missionRun', 'stateRun', 'summaryProject', 'summaryRun', 'jobProject', 'jobRun', 'jobRole', 'attemptRole', 'operator', 'missingProject'])('rejects contradictory %s scope', key => {
    const value = input();
    const mission = value.data.team_workspace.mission;
    if (key === 'missionProject') mission.project_key = 'B';
    if (key === 'missionRun') mission.run_id = 'run-b';
    if (key === 'stateRun') value.data.state.run_id = 'run-b';
    if (key === 'summaryProject') value.data.delivery_summary.project_key = 'B';
    if (key === 'summaryRun') value.data.delivery_summary.run_id = 'run-b';
    if (key === 'jobProject') value.data.worker_jobs[0].project_key = 'B';
    if (key === 'jobRun') value.data.worker_jobs[0].run_id = 'run-b';
    if (key === 'jobRole') value.data.worker_jobs[0].role = 'reviewer';
    if (key === 'attemptRole') value.data.delivery_summary.attempts[0].role = 'reviewer';
    if (key === 'operator') mission.source = 'operator_queue';
    if (key === 'missingProject') delete mission.project_key;
    expect(resolveAttemptInspection(value)).toEqual({ available: false, reason: 'scope' });
  });

  it.each(['actor', 'attempt', 'job', 'sharedAttempt'])('rejects duplicate %s identities', key => {
    const value = input();
    if (key === 'actor') value.data.team_workspace.execution_actors.push({ ...value.data.team_workspace.execution_actors[0] });
    if (key === 'sharedAttempt') value.data.team_workspace.execution_actors.push({ ...value.data.team_workspace.execution_actors[0], id: 'assignment-2' });
    if (key === 'attempt') value.data.delivery_summary.attempts.push({ ...value.data.delivery_summary.attempts[0] });
    if (key === 'job') value.data.worker_jobs.push({ ...value.data.worker_jobs[0] });
    expect(resolveAttemptInspection(value)).toEqual({ available: false, reason: 'ambiguous' });
  });

  it('does not replace the selected attempt when a retry becomes current', () => {
    const value = input();
    value.data.team_workspace.execution_actors[0].current_attempt_id = 'job-retry';
    value.data.delivery_summary.attempts.push({ id: 'job-retry', role: 'implementation', status: 'running' });
    expect(resolveAttemptInspection(value)).toEqual({ available: false, reason: 'changed' });
  });

  it('does not substitute a newer same-role attempt for missing membership', () => {
    const value = input();
    value.data.delivery_summary.attempts = [{ id: 'job-other', role: 'implementation', status: 'completed', updated_at: '2099-01-01' }];
    expect(resolveAttemptInspection(value)).toEqual({ available: false, reason: 'missing' });
  });

  it.each(['stale', 'loading', 'error'])('withholds facts when the snapshot is %s', freshness => {
    expect(resolveAttemptInspection({ ...input(), freshness })).toEqual({ available: false, reason: 'stale' });
  });

  it('requires exact workflow Skill task scope when present', () => {
    const value = input();
    value.data.worker_jobs[0].task_skill_context = { task: { kind: 'workflow_run', project_key: 'A', logical_id: 'run-a' } };
    expect(resolveAttemptInspection(value).available).toBe(true);
    for (const task of [{ kind: 'issue_execution', project_key: 'A', logical_id: 'run-a' }, { kind: 'workflow_run', project_key: 'B', logical_id: 'run-a' }, { kind: 'workflow_run', project_key: 'A', logical_id: 'other' }, null]) {
      value.data.worker_jobs[0].task_skill_context = { task };
      expect(resolveAttemptInspection(value)).toEqual({ available: false, reason: 'scope' });
    }
  });
});
