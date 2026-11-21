import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import TaskNavigation from './TaskNavigation';

test('selects the exact queued task revision', async () => {
  const onAction = vi.fn();
  const t = key => key;
  const assistant = { can_select_task: true, queued_tasks: [
    { id: 'queued-1', revision: 2, exact_request: 'Investigate the failing batch' },
  ] };
  render(<TaskNavigation assistant={assistant} onAction={onAction} t={t} />);
  await userEvent.click(screen.getByRole('button', { name: 'assistant.project.selectTask' }));
  expect(onAction).toHaveBeenCalledWith({ action: 'select_queued_task', proposal_id: 'queued-1', expected_revision: 2 });
});
