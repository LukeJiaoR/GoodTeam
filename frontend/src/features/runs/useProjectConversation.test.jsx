import { act, renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { runApi } from './runApi';
import useProjectConversation from './useProjectConversation';

vi.mock('./runApi', () => ({ runApi: { sendTaskAssistantMessage: vi.fn() } }));

test('transport failure releases the composer before refresh and keeps resend identity', async () => {
  let releaseRefresh;
  const refresh = vi.fn(() => new Promise((resolve) => { releaseRefresh = resolve; }));
  runApi.sendTaskAssistantMessage.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ session: {} });
  const { result, rerender } = renderHook(({ contextRunId }) => useProjectConversation({ projectKey: 'DEMO', contextRunId, refresh }),
    { initialProps: { contextRunId: 'old-run' } });
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

  // A question answer belongs to the project, even while inspecting history.
  // Explicit resend keeps its identity across other questions, ordinary chat,
  // and a change to the selected historical run.
  refresh.mockResolvedValue(undefined);
  runApi.sendTaskAssistantMessage.mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ session: {} }).mockResolvedValueOnce({ session: {} }).mockResolvedValueOnce({ session: {} });
  const questionId = `human-${'a'.repeat(32)}`;
  await act(async () => { failed = await result.current.answer(questionId, 'Internal staff only.'); });
  expect(failed).toBe(false);
  const answer = runApi.sendTaskAssistantMessage.mock.lastCall[1];
  expect(answer).toEqual({ message_id: expect.any(String), text: `/answer ${questionId} Internal staff only.`, context_run_id: '' });
  const otherQuestionId = `human-${'b'.repeat(32)}`;
  await act(async () => { await result.current.answer(otherQuestionId, 'Support owns it.'); });
  expect(runApi.sendTaskAssistantMessage.mock.lastCall[1]).toMatchObject({
    text: `/answer ${otherQuestionId} Support owns it.`, context_run_id: '',
  });
  expect(runApi.sendTaskAssistantMessage.mock.lastCall[1].message_id).not.toBe(answer.message_id);
  await act(async () => { await result.current.send('Show the latest progress.'); });
  rerender({ contextRunId: 'another-historical-run' });
  await act(async () => { await result.current.answer(questionId, 'Internal staff only.'); });
  expect(runApi.sendTaskAssistantMessage.mock.lastCall[1]).toEqual(answer);
  expect(result.current.delivery.status).toBe('recorded');
});
