import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import GoalContinuationPanel from './GoalContinuationPanel';

const t = (key, params) => translate('en', key, params);
const goal = {
  id: 'goal-1', revision: 4, objective: 'Make search usable', status: 'awaiting_approval', max_work_units: 1,
  acceptance_criteria: ['Search works with keyboard'], allowed_scope: ['Search screen only'],
  work_units: [{ run_id: 'run-bound', task: 'Implement keyboard search', status: 'completed',
    rationale: 'Covers keyboard acceptance', criterion_indices: [0], evidence_refs: ['tests/search.spec.js'] }],
  gate: { id: 'gate-1', kind: 'budget', status: 'pending', revision: 4, proposed_max_work_units: 2, rationale: 'Need one more unit to verify keyboard search' },
};

function fillGoal() {
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: ' Make search usable ' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria (one per line)' }), { target: { value: ' Search works with keyboard\n\nResults announce updates ' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Allowed scope and constraints (one per line)' }), { target: { value: ' Search screen only\nNo new dependencies ' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum work units (1–10)' }), { target: { value: '2' } });
}

describe('bounded Goal controls', () => {
  it('never creates a Goal on render, StrictMode rerender, open, or dismissal', () => {
    const onAction = vi.fn();
    const view = render(<StrictMode><GoalContinuationPanel onAction={onAction} t={t} /></StrictMode>);
    view.rerender(<StrictMode><GoalContinuationPanel onAction={onAction} t={t} /></StrictMode>);
    fireEvent.click(screen.getByRole('button', { name: 'Set a Goal' }));
    fillGoal();
    fireEvent.click(screen.getByRole('button', { name: 'Discard Goal draft' }));
    fireEvent.click(screen.getByRole('button', { name: 'Set a Goal' }));
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveValue('');
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(onAction).not.toHaveBeenCalled();
  });

  it('requires an explicit cost acknowledgement and sends exact scoped confirmation only on submit', async () => {
    const onAction = vi.fn().mockResolvedValue({ goal });
    render(<GoalContinuationPanel onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Set a Goal' }));
    fillGoal();
    expect(screen.getByText(/not a money or token cap/)).toBeInTheDocument();
    expect(screen.getByText(/Existing workflow approval Gates still apply/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm and start Goal' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and start Goal' }));
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and start Goal' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledExactlyOnceWith({
      action: 'create_goal', objective: 'Make search usable',
      acceptance_criteria: ['Search works with keyboard', 'Results announce updates'],
      allowed_scope: ['Search screen only', 'No new dependencies'], max_work_units: 2, max_assessment_attempts: 3,
      confirmed: true, billing_acknowledged: true, idempotency_key: expect.any(String),
    }));
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Objective' })).not.toBeInTheDocument());
  });

  it('requires criteria, scope, and an integer limit from one to ten', () => {
    const onAction = vi.fn();
    render(<GoalContinuationPanel onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Set a Goal' }));
    fillGoal();
    fireEvent.click(screen.getByRole('checkbox'));
    for (const value of ['0', '11', '1.5', '']) {
      fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum work units (1–10)' }), { target: { value } });
      expect(screen.getByRole('button', { name: 'Confirm and start Goal' })).toBeDisabled();
    }
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum work units (1–10)' }), { target: { value: '1' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Allowed scope and constraints (one per line)' }), { target: { value: ' \n ' } });
    expect(screen.getByRole('button', { name: 'Confirm and start Goal' })).toBeDisabled();
    expect(onAction).not.toHaveBeenCalled();
  });

  it('preserves the same creation idempotency key after failure and suppresses double submit', async () => {
    let complete;
    const onAction = vi.fn().mockImplementationOnce(() => new Promise(resolve => { complete = resolve; })).mockResolvedValue(false);
    render(<GoalContinuationPanel onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Set a Goal' }));
    fillGoal();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and start Goal' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and start Goal' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    await act(async () => complete(false));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and start Goal' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(2));
    expect(onAction.mock.calls[1][0]).toEqual(onAction.mock.calls[0][0]);
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveValue(' Make search usable ');
  });

  it('renders recorded work and binds one-unit approval to the displayed exact Goal revision and gate', async () => {
    const onAction = vi.fn().mockResolvedValue(false);
    const view = render(<GoalContinuationPanel goals={[goal]} onAction={onAction} t={t} />);
    expect(onAction).not.toHaveBeenCalled();
    expect(screen.getByText('Implement keyboard search')).toBeInTheDocument();
    expect(screen.getByText('tests/search.spec.js')).toBeInTheDocument();
    expect(screen.getByText('Run: run-bound')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve one more unit' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith({ action: 'decide_goal_gate',
      goal_id: 'goal-1', gate_id: 'gate-1', expected_revision: 4, decision: 'approve', idempotency_key: expect.any(String) }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve one more unit' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: 'Approve one more unit' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(2));
    expect(onAction.mock.calls[1][0]).toEqual(onAction.mock.calls[0][0]);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve one more unit' })).not.toBeDisabled());
    view.rerender(<GoalContinuationPanel goals={[{ ...goal, revision: 9, gate: { ...goal.gate, id: 'gate-2', revision: 9 } }]} onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reject continuation' }));
    await waitFor(() => expect(onAction).toHaveBeenLastCalledWith({ action: 'decide_goal_gate',
      goal_id: 'goal-1', gate_id: 'gate-2', expected_revision: 9, decision: 'reject', idempotency_key: expect.any(String) }));
  });

  it('never treats non-budget gates as one-unit approvals and stops the exact revision', async () => {
    const onAction = vi.fn().mockResolvedValue({ goal });
    render(<GoalContinuationPanel goals={[{ ...goal, gate: { ...goal.gate, kind: 'scope' } }]} onAction={onAction} t={t} />);
    expect(screen.queryByRole('button', { name: 'Approve one more unit' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stop continuation' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledExactlyOnceWith({ action: 'stop_goal',
      goal_id: 'goal-1', expected_revision: 4, status: 'stopped', idempotency_key: expect.any(String) }));
  });

  it('binds cancel and disables stale or read-only snapshots', async () => {
    const onAction = vi.fn().mockResolvedValue(false);
    const view = render(<GoalContinuationPanel goals={[goal]} onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel Goal' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ action: 'stop_goal',
      goal_id: 'goal-1', expected_revision: 4, status: 'canceled' })));
    view.rerender(<GoalContinuationPanel goals={[goal]} readOnly onAction={onAction} t={t} />);
    expect(screen.getByRole('button', { name: 'Approve one more unit' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Cancel Goal' })).not.toBeInTheDocument();
    view.rerender(<GoalContinuationPanel goals={[{ ...goal, revision: undefined }]} onAction={onAction} t={t} />);
    expect(screen.queryByRole('button', { name: 'Approve one more unit' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop continuation' })).toBeDisabled();
  });

  it('prevents overlapping creation while a workflow or active Goal exists', () => {
    const view = render(<GoalContinuationPanel activeRun onAction={vi.fn()} t={t} />);
    expect(screen.getByRole('button', { name: 'Set a Goal' })).toBeDisabled();
    view.rerender(<GoalContinuationPanel goals={[goal]} onAction={vi.fn()} t={t} />);
    expect(screen.queryByRole('button', { name: 'Set a Goal' })).not.toBeInTheDocument();
    view.rerender(<GoalContinuationPanel goals={[{ ...goal, status: 'stopped', gate: null }]} onAction={vi.fn()} t={t} />);
    expect(screen.getByRole('button', { name: 'Set a Goal' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Stop continuation' })).not.toBeInTheDocument();
  });

  it('keeps per-criterion Manager quotes collapsed and labels them as not independently verified', () => {
    const assessed = { ...goal, status: 'complete', gate: null, work_units: [{ ...goal.work_units[0],
      status: 'running', observed_execution_status: 'complete', assessment_checks: [
        { criterion: 0, status: 'met', evidence_ref: 'tests/search.spec.js', quote: 'Keyboard navigation works with Enter and Escape.' },
      ] }] };
    render(<GoalContinuationPanel goals={[assessed]} onAction={vi.fn()} t={t} />);
    expect(screen.getByText('Execution status: Completed')).toBeInTheDocument();
    const assessment = screen.getByRole('region', { name: 'Manager assessment', hidden: true });
    expect(assessment.closest('details')).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('Evidence references'));
    expect(screen.getByText(/have not been independently verified/)).toBeInTheDocument();
    expect(screen.getByText('Manager assessed: met')).toBeInTheDocument();
    expect(screen.getByText('Keyboard navigation works with Enter and Escape.')).toBeInTheDocument();
    expect(screen.queryByText('Running')).not.toBeInTheDocument();
  });

});


it('requires specific consent and binds a completed assessment retry', async () => {
  const onAction = vi.fn().mockResolvedValue({});
  const retry = { work_id: 'w1', run_id: 'run-bound', attempt: 1, job_id: 'job-failed', evidence_digest: 'abc' };
  const blocked = { ...goal, status: 'blocked', gate: null, assessment_retry_target: retry,
    work_units: [{ ...goal.work_units[0], id: 'w1', status: 'running' }] };
  render(<GoalContinuationPanel goals={[blocked]} onAction={onAction} t={t} />);
  const button = screen.getByRole('button', { name: 'Retry this assessment' });
  expect(button).toBeDisabled();
  expect(onAction).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', { name: /I approve one more/ }));
  fireEvent.click(button);
  await waitFor(() => expect(onAction).toHaveBeenCalledExactlyOnceWith({
    action: 'retry_goal_assessment', goal_id: goal.id, expected_revision: goal.revision,
    work_id: 'w1', run_id: 'run-bound', evidence_digest: 'abc', failed_job_id: 'job-failed',
    confirmed: true, idempotency_key: expect.any(String),
  }));
});

it('does not show retry for arbitrary blocked Goals or exhausted attempts', () => {
  const view = render(<GoalContinuationPanel goals={[{ ...goal, status: 'blocked', gate: null }]} onAction={vi.fn()} t={t} />);
  expect(screen.queryByRole('button', { name: 'Retry this assessment' })).not.toBeInTheDocument();
  view.rerender(<GoalContinuationPanel goals={[{ ...goal, status: 'blocked', gate: null,
    assessment_retry_target: { work_id: 'w1', run_id: 'run-bound', attempt: 3, job_id: 'job' },
    work_units: [{ ...goal.work_units[0], id: 'w1' }] }]} onAction={vi.fn()} t={t} />);
  expect(screen.queryByRole('button', { name: 'Retry this assessment' })).not.toBeInTheDocument();
});

const waitingGoal = {
  ...goal, status: 'active', gate: null,
  work_units: [{ ...goal.work_units[0], id: 'waiting-work', status: 'running',
    observed_execution_status: 'awaiting_approval', observed_approval: 'prd' }],
};
const currentApproval = { currentRunId: 'run-bound', approvalReviews: [{ approval: 'prd', is_actionable: true }] };

it('opens the existing approval for the exact current work without dispatching or deciding anything', () => {
  const onAction = vi.fn();
  const onOpenReview = vi.fn();
  render(<GoalContinuationPanel goals={[waitingGoal]} {...currentApproval} onAction={onAction} onOpenReview={onOpenReview} t={t} />);
  expect(onAction).not.toHaveBeenCalled();
  expect(onOpenReview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Review workflow approval' }));
  expect(onOpenReview).toHaveBeenCalledExactlyOnceWith('prd', 'run-bound');
  expect(onAction).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Approve one more unit' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Retry this assessment' })).not.toBeInTheDocument();
});

it.each([
  ['historical snapshot', { readOnly: true }],
  ['different current run', { currentRunId: 'run-new' }],
  ['no active workflow binding', { currentRunId: '' }],
  ['different pending Gate', { approvalReviews: [{ approval: 'rfc', is_actionable: true }] }],
  ['non-actionable Gate', { approvalReviews: [{ approval: 'prd', is_actionable: false }] }],
  ['stopped Goal', { goals: [{ ...waitingGoal, status: 'stopped' }] }],
  ['canceled Goal', { goals: [{ ...waitingGoal, status: 'canceled' }] }],
  ['blocked Goal', { goals: [{ ...waitingGoal, status: 'blocked' }] }],
  ['blocked execution', { goals: [{ ...waitingGoal, work_units: [{ ...waitingGoal.work_units[0], observed_execution_status: 'blocked' }] }] }],
  ['unknown execution', { goals: [{ ...waitingGoal, work_units: [{ ...waitingGoal.work_units[0], observed_execution_status: 'unknown' }] }] }],
  ['unobserved approval', { goals: [{ ...waitingGoal, work_units: [{ ...waitingGoal.work_units[0], observed_approval: '' }] }] }],
  ['stopped work', { goals: [{ ...waitingGoal, work_units: [{ ...waitingGoal.work_units[0], status: 'stopped' }] }] }],
])('does not borrow current approval authority for %s', (_, overrides) => {
  const onAction = vi.fn();
  const onOpenReview = vi.fn();
  render(<GoalContinuationPanel goals={[waitingGoal]} {...currentApproval} {...overrides} onAction={onAction} onOpenReview={onOpenReview} t={t} />);
  expect(screen.queryByRole('button', { name: 'Review workflow approval' })).not.toBeInTheDocument();
  expect(onAction).not.toHaveBeenCalled();
  expect(onOpenReview).not.toHaveBeenCalled();
});

it('uses existing actionable profile Gates without a hard-coded approval list and withdraws stale navigation', () => {
  const onOpenReview = vi.fn();
  const profileGoal = { ...waitingGoal, work_units: [{ ...waitingGoal.work_units[0], observed_approval: 'fix_plan' }] };
  const props = { goals: [profileGoal], currentRunId: 'run-bound', approvalReviews: [{ approval: 'fix_plan', is_actionable: true }], onOpenReview, t };
  const view = render(<GoalContinuationPanel {...props} busy />);
  fireEvent.click(screen.getByRole('button', { name: 'Review workflow approval' }));
  expect(onOpenReview).not.toHaveBeenCalled();
  view.rerender(<GoalContinuationPanel {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Review workflow approval' }));
  expect(onOpenReview).toHaveBeenCalledExactlyOnceWith('fix_plan', 'run-bound');
  view.rerender(<GoalContinuationPanel {...props} approvalReviews={[{ approval: 'fix_plan', is_actionable: false }]} />);
  expect(screen.queryByRole('button', { name: 'Review workflow approval' })).not.toBeInTheDocument();
});

it.each(['stopped', 'canceled'])('preserves the exact %s command and blocks duplicate submission while waiting for workflow approval', async status => {
  let finish;
  const onAction = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const onOpenReview = vi.fn();
  render(<GoalContinuationPanel goals={[waitingGoal]} {...currentApproval} onAction={onAction} onOpenReview={onOpenReview} t={t} />);
  const button = screen.getByRole('button', { name: status === 'stopped' ? 'Stop continuation' : 'Cancel Goal' });
  fireEvent.click(button);
  fireEvent.click(button);
  fireEvent.click(screen.getByRole('button', { name: 'Review workflow approval' }));
  expect(onAction).toHaveBeenCalledExactlyOnceWith({ action: 'stop_goal', goal_id: goal.id,
    expected_revision: goal.revision, status, idempotency_key: expect.any(String) });
  expect(onOpenReview).not.toHaveBeenCalled();
  await act(async () => finish(false));
});
