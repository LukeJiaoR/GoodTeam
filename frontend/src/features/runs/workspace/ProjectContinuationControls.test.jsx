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

it('keeps quality failures visible and delivers only the checked and reviewed correction candidate', () => {
  const action = vi.fn();
  const batch = { run_id: `task-batch-${'a'.repeat(64)}`, status: 'blocked', task_ids: ['APP-1', 'APP-2'],
    integration: { candidate_commit: 'b'.repeat(40), candidate_path: '/isolated/original-candidate' } };
  const quality = { required: true, input_digest: 'sha256:original-input', execution_id: 'quality-run-1',
    status: 'awaiting_worker', check_status: 'failed', review_status: 'machine_recorded', acceptance_status: 'unknown',
    reason: 'The candidate check failed; a correction is prepared.', next_action: 'Continue the prepared correction when its configured runtime is available.',
    candidate_commit: 'b'.repeat(40), candidate_path: '/isolated/original-candidate', correction_job_id: 'correction-job-1' };
  const props = { snapshot: { candidates: [] }, projectKey: 'APP', onAction: action };
  const session = (current, status = 'review') => ({ project_task_batches: { [batch.run_id]: { ...batch, status, quality: current } } });
  const view = render(<ProjectContinuationControls {...props} session={session(quality, 'blocked')} />);
  expect(screen.getByText('correction-job-1')).toBeInTheDocument();
  expect(screen.getByText('quality-run-1')).toBeInTheDocument();
  expect(screen.getByText(quality.reason)).toBeInTheDocument();
  expect(screen.getByText(/Continue the prepared correction/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Inspect check, review and fix evidence' }))
    .toHaveAttribute('href', `/api/projects/APP/iteration-batches/${batch.run_id}`);
  expect(screen.queryByRole('button', { name: 'Verify version and accept delivery' })).not.toBeInTheDocument();
  const advance = screen.getByRole('button', { name: 'Continue checks and review' });
  expect(advance).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/I confirm continuing these checks/));
  fireEvent.click(advance);
  expect(action).toHaveBeenLastCalledWith({ action: 'advance_project_batch_quality', run_id: batch.run_id,
    expected_input_digest: quality.input_digest, confirmed: true, billing_acknowledged: true });

  const corrected = { ...quality, status: 'reviewed',
    candidate_commit: 'd'.repeat(40), candidate_path: '/isolated/corrected-candidate',
    acceptance_status: 'accepted_by_review_policy', reason: '', next_action: '' };
  view.rerender(<ProjectContinuationControls {...props} session={session(corrected)} />);
  // A review label alone cannot bypass a failed check or carry old cost consent.
  expect(screen.queryByRole('button', { name: 'Verify version and accept delivery' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Continue checks and review' })).toBeDisabled();
  const accepted = { ...corrected, check_status: 'passed' };
  view.rerender(<ProjectContinuationControls {...props} session={session(accepted)} />);
  expect(screen.queryByRole('button', { name: 'Continue checks and review' })).not.toBeInTheDocument();
  expect(screen.queryByText('/isolated/original-candidate')).not.toBeInTheDocument();
  expect(screen.getAllByText('/isolated/corrected-candidate')).toHaveLength(2);
  fireEvent.change(screen.getByLabelText(/Full landed Git commit/), { target: { value: 'e'.repeat(40) } });
  fireEvent.click(screen.getByLabelText(/I independently reviewed/));
  fireEvent.click(screen.getByRole('button', { name: 'Verify version and accept delivery' }));
  expect(action).toHaveBeenLastCalledWith({ action: 'accept_project_batch_delivery', run_id: batch.run_id,
    candidate_commit: 'd'.repeat(40), landed_commit: 'e'.repeat(40), reviewed: true, confirmed: true });

  view.rerender(<ProjectContinuationControls {...props}
    session={session({ ...accepted, candidate_commit: 'f'.repeat(40) })} />);
  expect(screen.getByLabelText(/Full landed Git commit/)).toHaveValue('');
  expect(screen.getByLabelText(/I independently reviewed/)).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Verify version and accept delivery' })).toBeDisabled();
});

it('binds recovery to failed task evidence, preserves stop, and makes abandonment terminal', () => {
  const action = vi.fn();
  const props = { snapshot: { candidates: [] }, projectKey: 'APP', onAction: action };
  const batch = { run_id: `task-batch-${'a'.repeat(64)}`, status: 'blocked', task_ids: ['APP-1', 'APP-2'],
    quality: { required: true, input_digest: 'quality-input', status: 'blocked', check_status: 'unknown' } };
  const continuation = { id: 'approved-1', status: 'blocked', batches_started: 1, max_batches: 3,
    manager_calls: 1, max_manager_calls: 3 };
  const recovery = { version: 'evidence-1', reason: 'Worker stopped with a failed result.', retryable_task_ids: ['APP-2'],
    preserved_task_ids: ['APP-1'], unknown_task_ids: [], can_abandon: true, can_resume_continuation: true,
    continuation_id: continuation.id };
  const session = (evidence = recovery, owner = continuation, status = 'blocked') => ({
    project_continuation: owner, project_task_batches: { [batch.run_id]: { ...batch, status, recovery: evidence } },
  });
  const view = render(<ProjectContinuationControls {...props} session={session()} />);
  expect(screen.getByText('Preserved successful results: APP-1')).toBeInTheDocument();
  expect(screen.getByText('Confirmed failed tasks: APP-2')).toBeInTheDocument();
  const retry = screen.getByRole('button', { name: 'Retry confirmed failed tasks' });
  expect(retry).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/I confirm retrying these failed tasks/));
  fireEvent.click(screen.getByLabelText(/Resume the remaining iterations/));
  fireEvent.click(retry);
  const first = action.mock.calls[0][0];
  expect(first).toEqual({ action: 'retry_project_batch', run_id: batch.run_id,
    expected_recovery_version: recovery.version, confirmed: true, billing_acknowledged: true,
    retry_task_ids: ['APP-2'], resume_continuation: true, continuation_id: continuation.id,
    idempotency_key: `batch-retry:${batch.run_id}:${recovery.version}:${continuation.id}` });
  view.rerender(<ProjectContinuationControls {...props} session={session()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Retry confirmed failed tasks' }));
  expect(action).toHaveBeenLastCalledWith(first);

  // Even an obsolete can_resume projection cannot revive an owner-stopped authorization.
  view.rerender(<ProjectContinuationControls {...props} session={session(recovery, { ...continuation, status: 'stopped' })} />);
  expect(screen.queryByLabelText(/Resume the remaining iterations/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry confirmed failed tasks' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/I confirm retrying these failed tasks/));
  fireEvent.click(screen.getByRole('button', { name: 'Retry confirmed failed tasks' }));
  expect(action).toHaveBeenLastCalledWith({ ...first, resume_continuation: false, continuation_id: undefined,
    idempotency_key: `batch-retry:${batch.run_id}:${recovery.version}:manual` });
  expect(action.mock.lastCall[0]).not.toHaveProperty('continuation_id');

  view.rerender(<ProjectContinuationControls {...props}
    session={session({ ...recovery, version: 'evidence-2', unknown_task_ids: ['APP-2'], can_abandon: false })} />);
  expect(screen.getByText(/Outcome unconfirmed; inspect execution evidence first/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Retry confirmed failed tasks' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Abandon this batch' })).not.toBeInTheDocument();

  const settled = { ...recovery, version: 'evidence-3' };
  // Quality failure can retain a review-status batch; safe abandonment stays reachable.
  view.rerender(<ProjectContinuationControls {...props} session={session(settled, continuation, 'review')} />);
  expect(screen.queryByRole('button', { name: 'Retry confirmed failed tasks' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Abandon this batch' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/I confirm abandoning this batch/));
  fireEvent.click(screen.getByRole('button', { name: 'Abandon this batch' }));
  expect(action).toHaveBeenLastCalledWith({ action: 'abandon_project_batch', run_id: batch.run_id,
    expected_recovery_version: settled.version, confirmed: true,
    idempotency_key: `batch-abandon:${batch.run_id}:${settled.version}` });
  view.rerender(<ProjectContinuationControls {...props} session={session(settled, continuation, 'abandoned')} />);
  expect(screen.getByText(/Batch abandoned; evidence retained/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Inspect retained execution evidence' }))
    .toHaveAttribute('href', `/api/projects/APP/iteration-batches/${batch.run_id}`);
  expect(screen.queryByRole('button', { name: /Retry confirmed|Abandon this batch|Continue checks|Verify version/ })).not.toBeInTheDocument();
});
