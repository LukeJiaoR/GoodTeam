import { act, renderHook, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import useProjectState from './useProjectState';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

test('hides old project state immediately and ignores its late response after switching', async () => {
  const first = deferred();
  const second = deferred();
  const api = { getProjectState: vi.fn((key) => (key === 'FIRST' ? first.promise : second.promise)) };
  const { result, rerender } = renderHook(
    ({ projectKey }) => useProjectState(projectKey, { api, poll: false }),
    { initialProps: { projectKey: 'FIRST' } },
  );
  await waitFor(() => expect(api.getProjectState).toHaveBeenCalledWith('FIRST', expect.any(Object)));

  rerender({ projectKey: 'SECOND' });
  expect(result.current.data).toBeNull();
  expect(result.current.status).toBe('loading');
  await waitFor(() => expect(api.getProjectState).toHaveBeenCalledWith('SECOND', expect.any(Object)));

  await act(async () => {
    first.resolve({ project_key: 'FIRST', state: { run_id: 'first-run' } });
    await first.promise;
  });
  expect(result.current.data).toBeNull();

  await act(async () => {
    second.resolve({ project_key: 'SECOND', state: { run_id: 'second-run' } });
    await second.promise;
  });
  expect(result.current.data.project_key).toBe('SECOND');
  expect(result.current.data.state.run_id).toBe('second-run');
});

test('does not retain the previous project data when the new project load fails', async () => {
  const second = deferred();
  const api = {
    getProjectState: vi.fn((key) => (
      key === 'FIRST'
        ? Promise.resolve({ project_key: 'FIRST', state: { run_id: 'first-run' } })
        : second.promise
    )),
  };
  const { result, rerender } = renderHook(
    ({ projectKey }) => useProjectState(projectKey, { api, poll: false }),
    { initialProps: { projectKey: 'FIRST' } },
  );
  await waitFor(() => expect(result.current.data?.project_key).toBe('FIRST'));

  rerender({ projectKey: 'SECOND' });
  expect(result.current.data).toBeNull();
  await waitFor(() => expect(api.getProjectState).toHaveBeenCalledWith('SECOND', expect.any(Object)));
  await act(async () => {
    second.reject(new Error('Project unavailable'));
    await Promise.resolve();
  });

  await waitFor(() => expect(result.current.status).toBe('error'));
  expect(result.current.data).toBeNull();
  expect(result.current.error.message).toBe('Project unavailable');
});

test('accepts a slow state poll instead of superseding it with overlapping polls', async () => {
  vi.useFakeTimers();
  const pending = deferred();
  const first = { active_run: true, state: { run_id: 'first-run' } };
  const second = { active_run: true, state: { run_id: 'replacement-run' } };
  const api = { getProjectState: vi.fn().mockResolvedValue(first) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  try {
    await act(async () => { await Promise.resolve(); });
    expect(result.current.data).toEqual(first);
    api.getProjectState.mockReturnValueOnce(pending.promise);
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(api.getProjectState).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.getProjectState).toHaveBeenCalledTimes(2);
    await act(async () => { pending.resolve(second); await pending.promise; });
    expect(result.current.data).toEqual(second);
    expect(result.current.status).toBe('ready');
  } finally {
    unmount();
    vi.useRealTimers();
  }
});

test('keeps a manual refresh current when an older poll settles', async () => {
  vi.useFakeTimers();
  const oldPoll = deferred();
  const manual = deferred();
  const first = { active_run: true, state: { run_id: 'first-run' } };
  const replacement = { active_run: true, state: { run_id: 'replacement-run' } };
  const api = { getProjectState: vi.fn().mockResolvedValue(first) };
  const { result, unmount } = renderHook(() => useProjectState('DEMO', { api }));
  try {
    await act(async () => { await Promise.resolve(); });
    api.getProjectState.mockReturnValueOnce(oldPoll.promise);
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    api.getProjectState.mockReturnValueOnce(manual.promise);
    let refreshed;
    await act(async () => { refreshed = result.current.refresh(); });
    await act(async () => { oldPoll.resolve(first); await oldPoll.promise; });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.getProjectState).toHaveBeenCalledTimes(3);
    await act(async () => { manual.resolve(replacement); await refreshed; });
    expect(result.current.data).toEqual(replacement);
    api.getProjectState.mockResolvedValue(replacement);
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(api.getProjectState).toHaveBeenCalledTimes(4);
  } finally {
    unmount();
    vi.useRealTimers();
  }
});
