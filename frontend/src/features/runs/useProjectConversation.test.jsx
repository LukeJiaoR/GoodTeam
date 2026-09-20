import { act, renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { runApi } from './runApi';
import useProjectConversation from './useProjectConversation';

vi.mock('./runApi', () => ({ runApi: { sendTaskAssistantMessage: vi.fn() } }));

test('transport failure releases the composer before refresh and keeps resend identity', async () => {
  let releaseRefresh;
  const refresh = vi.fn(() => new Promise((resolve) => { releaseRefresh = resolve; }));
  runApi.sendTaskAssistantMessage.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ session: {} });
  const { result } = renderHook(() => useProjectConversation({ projectKey: 'DEMO', contextRunId: 'old-run', refresh }));
  let failed;
  await act(async () => { failed = await result.current.send('Explain the old task.'); });
  expect(failed).toBe(false);
  expect(result.current.busy).toBe('');
  expect(result.current.delivery.status).toBe('unconfirmed');
  const original = runApi.sendTaskAssistantMessage.mock.calls[0][1];
  await act(async () => { releaseRefresh(); });

  let retry;
  await act(async () => { retry = result.current.send('Explain the old task.'); });
  expect(runApi.sendTaskAssistantMessage.mock.calls[1][1]).toEqual(original);
  expect(original.context_run_id).toBe('old-run');
  await act(async () => { releaseRefresh(); await retry; });
  expect(result.current.busy).toBe('');
  expect(result.current.delivery.status).toBe('recorded');
});
