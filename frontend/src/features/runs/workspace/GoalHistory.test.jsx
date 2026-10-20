import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import { runApi } from '../runApi';
import GoalContinuationPanel from './GoalContinuationPanel';

const t = (key, params) => translate('en', key, params);
const goal = (id, project = 'A') => ({ id, project_key: project, revision: 1, objective: `Goal ${id}`,
  status: 'stopped', acceptance_criteria: ['all criteria'], allowed_scope: ['all scope'], work_units: [], max_work_units: 1 });
const panel = (project = 'A') => <GoalContinuationPanel key={project} projectKey={project}
  goals={[goal('latest', project)]} historyCursor="latest" t={t} onAction={vi.fn()} />;

afterEach(() => vi.restoreAllMocks());

it('loads only on request, orders multiple pages newest first, and retains complete evidence', async () => {
  const old = { ...goal('1'), history: [{ evidence: 'complete older evidence' }] };
  const read = vi.spyOn(runApi, 'getGoalHistory').mockResolvedValueOnce({ project_key: 'A', goals: [goal('3'), goal('4')], next_before: '3' })
    .mockResolvedValueOnce({ project_key: 'A', goals: [old, goal('2')], next_before: '' });
  render(panel());
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Load older goals' }));
  await screen.findByText('Goal 4 · Stopped');
  fireEvent.click(screen.getByRole('button', { name: 'Load older goals' }));
  await screen.findByText('Goal 1 · Stopped');
  const summaries = [...document.querySelectorAll('section[aria-label="Goal history"] > details > summary')].map(e => e.textContent);
  expect(summaries).toEqual(['Goal 4 · Stopped', 'Goal 3 · Stopped', 'Goal 2 · Stopped', 'Goal 1 · Stopped']);
  expect(document.querySelector('.goal-history-record').textContent).toContain('all criteria');
  expect(document.body.textContent).toContain('complete older evidence');
  expect(screen.queryByRole('button', { name: 'Load older goals' })).not.toBeInTheDocument();
  expect(read.mock.calls.map(c => c.slice(0, 2))).toEqual([['A', 'latest'], ['A', '3']]);
});

it('rejects a late previous-project response and aborts its request', async () => {
  let resolve;
  const read = vi.spyOn(runApi, 'getGoalHistory').mockImplementation(() => new Promise(r => { resolve = r; }));
  const view = render(panel());
  fireEvent.click(screen.getByRole('button', { name: 'Load older goals' }));
  view.rerender(panel('B'));
  expect(read.mock.calls[0][2].signal.aborted).toBe(true);
  await act(async () => resolve({ project_key: 'A', goals: [goal('private-A')], next_before: '' }));
  expect(document.body.textContent).not.toContain('private-A');
});

it('rejects wrong-project or malformed payloads without consuming the cursor, and retries failures', async () => {
  const read = vi.spyOn(runApi, 'getGoalHistory').mockResolvedValueOnce({ project_key: 'B', goals: [goal('private-B', 'B')], next_before: '' })
    .mockResolvedValueOnce({ project_key: 'A', goals: {}, next_before: '' })
    .mockResolvedValueOnce({ project_key: 'A', goals: [goal('safe')], next_before: '' });
  render(panel());
  for (let attempt = 1; attempt <= 2; attempt++) {
    fireEvent.click(screen.getByRole('button', { name: 'Load older goals' }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(attempt));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Load older goals' })).not.toBeDisabled());
  }
  expect(document.body.textContent).not.toContain('private-B');
  fireEvent.click(screen.getByRole('button', { name: 'Load older goals' }));
  await screen.findByText('Goal safe · Stopped');
  expect(read.mock.calls.every(c => c[1] === 'latest')).toBe(true);
});
