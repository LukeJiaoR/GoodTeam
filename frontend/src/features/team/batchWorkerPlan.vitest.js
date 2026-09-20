import { expect, test } from 'vitest';
import { batchWorkerPlan, groupTeamRoles, initialWorkerRuntime } from './teamModel';

test('empty groups use the Codex Sol default while explicit project backends keep their own model defaults', () => {
  expect(initialWorkerRuntime([], 'codex')).toEqual({ provider: 'codex', model: 'gpt-5.6-sol', reasoningDepth: 'default' });
  expect(initialWorkerRuntime([], 'fake')).toEqual({ provider: 'fake', model: '', reasoningDepth: 'default' });
  expect(initialWorkerRuntime([], 'claude')).toEqual({ provider: 'claude', model: '', reasoningDepth: 'default' });
});

test('groups every role once and keeps new server roles visible', () => {
  const roles = ['research', 'planner', 'ui', 'implementation', 'qa', 'retro_memory', 'future_role'].map((role) => ({ role }));
  const groups = groupTeamRoles(roles);
  expect(groups.map((group) => group.key)).toEqual(['planning', 'building', 'delivery', 'learning', 'other']);
  expect(groups.flatMap((group) => group.roles)).toEqual(roles);
  expect(groupTeamRoles([])).toEqual([]);
});

test('ignores disabled and unselected roles even when they are empty', () => {
  const roles = [
    { role: 'qa', state: 'disabled', workers: [] },
    { role: 'research', state: 'enabled', workers: [] },
  ];
  expect(batchWorkerPlan(roles, ['qa'], { provider: 'fake', model: '', reasoningDepth: 'default' })).toEqual({ commands: [], updated: 0, created: 0 });
});

test('preserves custom prompts, names and paused states and omits unchanged workers', () => {
  const worker = { id: 'qa-a', role: 'qa', name: 'QA A', lifecycle: 'inactive', provider: 'codex', model: '', reasoningDepth: 'default', additionalPrompt: 'Keep my instructions.' };
  const roles = [{ role: 'qa', state: 'enabled', workers: [worker] }];
  const runtime = { provider: 'codex', model: '', reasoningDepth: 'default' };
  expect(batchWorkerPlan(roles, ['qa'], runtime)).toEqual({ commands: [], updated: 0, created: 0 });
  expect(batchWorkerPlan(roles, ['qa'], { ...runtime, provider: 'fake' })).toEqual({
    commands: [{ kind: 'change_worker_runtime', worker_id: 'qa-a', provider: 'fake' }], updated: 1, created: 0,
  });
  expect(worker.provider).toBe('codex');
});
