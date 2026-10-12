import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import useProjectState from './useProjectState';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

let hidden;
beforeEach(() => {
  vi.useFakeTimers();
  hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

async function visibility(value) {
  hidden.mockReturnValue(value);
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
}
async function settle() {
  await act(async () => { await Promise.resolve(); });
}
async function tick(ms = 5_000) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

test.each([
  ['running workflow', { active_run: true }],
  ['project conversation', { project_assistant: { timeline: [] } }],
  ['legacy conversation', { task_assistant_session: { timeline: [] } }],
  ['Manager mission', { team_workspace: { mission: { source: 'task_assistant' } } }],
])('a %s loaded in a hidden tab refreshes on return and keeps polling', async (_label, initial) => {
  hidden.mockReturnValue(true);
  const first = { ...initial, revision: 1 };
  const latest = { ...initial, revision: 2 };
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(first).mockResolvedValue(latest) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(1);
  expect(result.current.data).toEqual(first);

  await visibility(false);
  expect(api.getProjectState).toHaveBeenCalledTimes(2);
  expect(result.current.data).toEqual(latest);
  await tick();
  expect(api.getProjectState).toHaveBeenCalledTimes(3);
  unmount();
});

test('a conversation poll settling in the background does not stop future updates', async () => {
  const pending = deferred();
  const first = { project_assistant: { timeline: [] }, revision: 1 };
  const next = { project_assistant: { timeline: [{ id: 'reply' }] }, revision: 2 };
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(first).mockReturnValueOnce(pending.promise).mockResolvedValue(next) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await tick();
  await visibility(true);
  await act(async () => { pending.resolve(next); await pending.promise; });
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(2);

  await visibility(false);
  expect(api.getProjectState).toHaveBeenCalledTimes(3);
  expect(result.current.data).toEqual(next);
  await tick();
  expect(api.getProjectState).toHaveBeenCalledTimes(4);
  unmount();
});

test('returning to an idle project discovers work started while the tab was away', async () => {
  const active = { active_run: true, state: { run_id: 'new-run' } };
  const api = { getProjectState: vi.fn().mockResolvedValueOnce({ active_run: false }).mockResolvedValue(active) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await visibility(true);
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(1);
  await visibility(false);
  expect(result.current.data).toEqual(active);
  await tick();
  expect(api.getProjectState).toHaveBeenCalledTimes(3);
  unmount();
});

test('returning from a hidden initial load cannot leave a running task frozen indefinitely', async () => {
  hidden.mockReturnValue(true);
  const api = { getProjectState: vi.fn().mockResolvedValueOnce({ active_run: true, revision: 1 })
    .mockResolvedValue({ active_run: true, revision: 2 }) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await visibility(false);
  await tick(30_000);
  expect(result.current.data.revision).toBe(2);
  unmount();
});

test('returning during the initial read waits for that read instead of duplicating it', async () => {
  hidden.mockReturnValue(true);
  const pending = deferred();
  const api = { getProjectState: vi.fn().mockReturnValueOnce(pending.promise)
    .mockResolvedValue({ active_run: true, revision: 2 }) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await visibility(false);
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve({ active_run: true, revision: 1 }); await pending.promise; });
  await tick();
  expect(result.current.data.revision).toBe(2);
  unmount();
});

test('visibility events and timer ticks do not supersede a pending refresh', async () => {
  const pending = deferred();
  const initial = { active_run: true, revision: 1 };
  const latest = { active_run: true, revision: 2 };
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(initial).mockReturnValueOnce(pending.promise).mockResolvedValue(latest) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await visibility(true);
  await visibility(false);
  expect(api.getProjectState).toHaveBeenCalledTimes(2);
  await visibility(false);
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(2);
  expect(api.getProjectState.mock.calls[1][1].signal.aborted).toBe(false);
  await act(async () => { pending.resolve(latest); await pending.promise; });
  expect(result.current.data).toEqual(latest);
  await tick();
  expect(api.getProjectState).toHaveBeenCalledTimes(3);
  unmount();
});

test('a failed foreground read preserves stale evidence and polling recovers it', async () => {
  const initial = { active_run: true, revision: 1 };
  const latest = { active_run: true, revision: 3 };
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(initial)
    .mockRejectedValueOnce(new Error('Disconnected')).mockResolvedValue(latest) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  await settle();
  await visibility(true);
  await visibility(false);
  expect(result.current.status).toBe('stale');
  expect(result.current.data).toEqual(initial);
  expect(result.current.error.message).toBe('Disconnected');
  await tick();
  expect(result.current.status).toBe('ready');
  expect(result.current.data).toEqual(latest);
  unmount();
});

test('poll=false disables foreground refresh and periodic reads', async () => {
  const api = { getProjectState: vi.fn().mockResolvedValue({ active_run: true }) };
  const { unmount } = renderHook(() => useProjectState('DEMO', { api, poll: false }));
  await settle();
  await visibility(true);
  await visibility(false);
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(1);
  unmount();
});

test('switching projects aborts a pending foreground read and ignores its late response', async () => {
  const pending = deferred();
  const api = { getProjectState: vi.fn().mockResolvedValueOnce({ active_run: true, project_key: 'FIRST' })
    .mockReturnValueOnce(pending.promise).mockResolvedValue({ active_run: true, project_key: 'SECOND' }) };
  const { result, rerender, unmount } = renderHook(
    ({ projectKey }) => useProjectState(projectKey, { api }), { initialProps: { projectKey: 'FIRST' } },
  );
  await settle();
  await visibility(true);
  await visibility(false);
  const signal = api.getProjectState.mock.calls[1][1].signal;
  rerender({ projectKey: 'SECOND' });
  await settle();
  expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve({ active_run: true, project_key: 'FIRST' }); await pending.promise; });
  expect(result.current.data.project_key).toBe('SECOND');
  unmount();
  await visibility(false);
  await tick(15_000);
  expect(api.getProjectState).toHaveBeenCalledTimes(3);
});
