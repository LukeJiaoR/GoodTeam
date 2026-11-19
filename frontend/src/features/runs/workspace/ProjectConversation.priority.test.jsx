import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import ProjectConversation from './ProjectConversation';

const t = key => key;

test('streamed response leaves input available and stops the selected request', async () => {
  const send = vi.fn().mockResolvedValue({ accepted: true });
  const action = vi.fn().mockResolvedValue({ session: {} });
  render(<ProjectConversation projectKey="DEMO" language="en" t={t} timeline={[]}
    onStart={send} onAssistantAction={action} assistantBusy=""
    assistantState={{ assistant_requests: { current: {
      status: 'running', phase: 'model', attempt: 2, stream_text: 'The Reviewer is checking the patch.',
      activity: 'Inspecting the current task', started_at: new Date().toISOString(),
    } } }} />);
  expect(screen.getByText('The Reviewer is checking the patch.')).toBeVisible();
  const input = screen.getByRole('textbox', { name: 'work.taskAssistant.message' });
  fireEvent.change(input, { target: { value: 'Prioritize the failed test.' } });
  const button = screen.getByRole('button', { name: 'work.taskAssistant.send' });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(send).toHaveBeenCalledWith('Prioritize the failed test.'));
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.stopResponse' }));
  await waitFor(() => expect(action).toHaveBeenCalledWith({
    action: 'stop_manager_request', message_id: 'current', expected_attempt: 2,
  }));
});
