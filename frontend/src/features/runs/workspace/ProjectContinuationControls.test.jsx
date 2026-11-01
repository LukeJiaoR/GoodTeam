import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ProjectContinuationControls from './ProjectContinuationControls';

it('binds explicit scoped continuation and reviewed landed delivery to visible evidence', () => {
  const action = vi.fn();
  const snapshot = { version: 'sha256:source', candidates: [{ task_id: 'APP-1', title: 'First task', status: 'todo' }] };
  const view = render(<ProjectContinuationControls snapshot={snapshot} projectKey="APP" onAction={action} canStart />);
  const start = screen.getByRole('button', { name: 'Authorize and start continuation' });
  expect(start).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(start);
  const initial = action.mock.calls[0][0];
  expect(initial).toMatchObject({ action: 'start_project_continuation', task_ids: ['APP-1'], max_batches: 3,
    confirmed: true, billing_acknowledged: true });
  const batch = { run_id: `task-batch-${'a'.repeat(64)}`, status: 'review', task_ids: ['APP-1'],
    integration: { candidate_commit: 'b'.repeat(40), candidate_path: '/isolated/candidate' } };
  view.rerender(<ProjectContinuationControls snapshot={snapshot} projectKey="APP" onAction={action} canStart
    session={{ project_continuation: { id: 'approved-1', status: 'stopped', batches_started: 1, max_batches: 3,
      manager_calls: 1, max_manager_calls: 3 }, project_task_batches: { [batch.run_id]: batch } }} />);
  fireEvent.click(screen.getByRole('button', { name: 'Authorize and start continuation' }));
  expect(action.mock.calls[1][0].idempotency_key).not.toBe(initial.idempotency_key);
  expect(screen.getByRole('link', { name: /Inspect execution/ })).toHaveAttribute('href', `/api/projects/APP/iteration-batches/${batch.run_id}`);
  expect(screen.getByText('b'.repeat(40))).toBeInTheDocument();
  const accept = screen.getByRole('button', { name: 'Verify version and accept delivery' });
  expect(accept).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Full landed Git commit/), { target: { value: 'c'.repeat(40) } });
  fireEvent.click(screen.getByLabelText(/I independently reviewed/));
  fireEvent.click(accept);
  expect(action.mock.calls[2][0]).toEqual({ action: 'accept_project_batch_delivery', run_id: batch.run_id,
    candidate_commit: 'b'.repeat(40), landed_commit: 'c'.repeat(40), reviewed: true, confirmed: true });
});
