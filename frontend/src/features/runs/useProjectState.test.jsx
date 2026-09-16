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
