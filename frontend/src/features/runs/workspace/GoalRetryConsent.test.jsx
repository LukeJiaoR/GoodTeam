import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import GoalContinuationPanel from './GoalContinuationPanel';
const t = (key, params) => translate('en', key, params);
const original = { id: 'g1', revision: 4, objective: 'Goal', status: 'blocked', max_work_units: 1,
  acceptance_criteria: ['Done'], allowed_scope: ['Repo'],
  work_units: [{ id: 'w1', run_id: 'r1', task: 'Task', status: 'running' }],
  assessment_retry_target: { work_id: 'w1', run_id: 'r1', attempt: 1, job_id: 'old-failed-job', evidence_digest: 'old-digest' } };
const checkbox = () => screen.getByRole('checkbox', { name: /I approve one more/ });
const button = () => screen.getByRole('button', { name: 'Retry this assessment' });
const panel = (goal, onAction, props = {}) => <GoalContinuationPanel goals={[goal]} onAction={onAction} t={t} {...props} />;

it.each([
  ['goal', g => { g.id = 'g2'; }],
  ['revision', g => { g.revision++; }],
  ['work', g => { g.work_units[0].id = g.assessment_retry_target.work_id = 'w2'; }],
  ['run', g => { g.work_units[0].run_id = g.assessment_retry_target.run_id = 'r2'; }],
  ['attempt', g => { g.assessment_retry_target.attempt = 2; }],
  ['job', g => { g.assessment_retry_target.job_id = 'new-failed-job'; }],
  ['evidence', g => { g.assessment_retry_target.evidence_digest = 'new-digest'; }],
])('invalidates consent when the exact %s changes and does not restore it on return', (_field, change) => {
  const onAction = vi.fn();
  const view = render(panel(original, onAction));
  fireEvent.click(checkbox());
  expect(button()).toBeEnabled();
  const replaced = structuredClone(original);
  change(replaced);
  view.rerender(panel(replaced, onAction));
  expect(checkbox()).not.toBeChecked();
  expect(button()).toBeDisabled();
  fireEvent.click(button());
  expect(onAction).not.toHaveBeenCalled();
  view.rerender(panel(original, onAction));
  expect(checkbox()).not.toBeChecked();
});

it('preserves consent across same-identity polling, submits the bound target once, then consumes consent', async () => {
  let finish;
  const onAction = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const view = render(panel(original, onAction));
  fireEvent.click(checkbox());
  const refreshed = structuredClone(original);
  refreshed.stop_reason = 'Updated explanation';
  view.rerender(panel(refreshed, onAction));
  expect(checkbox()).toBeChecked();
  fireEvent.click(button());
  fireEvent.click(button());
  expect(onAction).toHaveBeenCalledExactlyOnceWith({ action: 'retry_goal_assessment', goal_id: 'g1', expected_revision: 4,
    work_id: 'w1', run_id: 'r1', evidence_digest: 'old-digest', failed_job_id: 'old-failed-job', confirmed: true,
    idempotency_key: expect.any(String) });
  expect(checkbox()).not.toBeChecked();
  finish(false);
  await waitFor(() => expect(checkbox()).not.toBeDisabled());
  expect(button()).toBeDisabled();
});

it.each(['readOnly', 'targetRemoved', 'statusChanged'])('does not preserve consent through %s', mode => {
  const onAction = vi.fn();
  const view = render(panel(original, onAction));
  fireEvent.click(checkbox());
  const changed = structuredClone(original);
  if (mode === 'targetRemoved') delete changed.assessment_retry_target;
  if (mode === 'statusChanged') changed.status = 'active';
  view.rerender(panel(changed, onAction, { readOnly: mode === 'readOnly' }));
  expect(screen.queryByRole('button', { name: 'Retry this assessment' })).not.toBeInTheDocument();
  view.rerender(panel(original, onAction));
  expect(checkbox()).not.toBeChecked();
  expect(button()).toBeDisabled();
});
