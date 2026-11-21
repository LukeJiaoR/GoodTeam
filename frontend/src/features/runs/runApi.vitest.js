import { afterEach, expect, test, vi } from 'vitest';
import { getTaskInputContext } from './runApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

test('reads task input with exact encoded project/run scope, abort signal and no cache', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  await getTaskInputContext('demo/project', 'run #1', { signal });
  expect(fetch).toHaveBeenCalledWith('/api/projects/demo%2Fproject/runs/run%20%231/input-context', { signal, cache: 'no-store' });
});

test('keeps default state requests legacy and opts into bounded history explicitly', async () => {
  const { getProjectState, getGoalHistory } = await import('./runApi');
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  await getProjectState('A B', { signal });
  await getProjectState('A B', { signal, goalLimit: 1 });
  await getGoalHistory('A B', 'cursor+1', { signal });
  expect(fetch.mock.calls.map(call => call[0])).toEqual([
    '/api/state?project=A%20B', '/api/state?project=A%20B&goal_limit=1',
    '/api/goal-history?project=A%20B&before=cursor%2B1&goal_limit=10',
  ]);
  expect(fetch.mock.calls.every(call => call[1].signal === signal)).toBe(true);
});
