import { act, renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { runApi } from './runApi';
import useProjectConversation from './useProjectConversation';

vi.mock('./runApi', () => ({ runApi: { sendTaskAssistantMessage: vi.fn(), taskAssistantAction: vi.fn() } }));

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

  // An unresolved answer request must not prevent revocation. Other mutations
  // stay serialized, including after Stop finishes while the answer is pending.
  let rejectAnswer, confirmStop;
  runApi.sendTaskAssistantMessage.mockImplementationOnce(() => new Promise((_, reject) => { rejectAnswer = reject; }));
  runApi.taskAssistantAction.mockImplementationOnce(() => new Promise(resolve => { confirmStop = resolve; }));
  let pendingAnswer, pendingStop;
  await act(async () => { pendingAnswer = result.current.answer(`human-${'c'.repeat(32)}`, 'No new access.'); });
  expect(result.current.busy).toBe('answer_question');
  await act(async () => { expect(await result.current.act({ action: 'start_project_continuation' })).toBe(false); });
  const stop = { action: 'stop_project_continuation', continuation_id: 'current-grant' };
  await act(async () => { pendingStop = result.current.act(stop); });
  expect(runApi.taskAssistantAction).toHaveBeenCalledExactlyOnceWith('DEMO', stop);
  await act(async () => { expect(await result.current.act(stop)).toBe(false); });
  await act(async () => { confirmStop({ project_continuation: { status: 'stopped' } }); await pendingStop; });
  expect(result.current.busy).toBe('answer_question');
  await act(async () => { expect(await result.current.act({ action: 'start_project_continuation' })).toBe(false); });
  expect(runApi.taskAssistantAction).toHaveBeenCalledTimes(1);
  await act(async () => { rejectAnswer(new Error('The original continuation was stopped.')); await pendingAnswer; });
  expect(result.current.busy).toBe('');
});
