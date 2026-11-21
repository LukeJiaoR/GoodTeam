import { afterEach, expect, test, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
});


test('requests bounded Goal history explicitly', async () => {
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
