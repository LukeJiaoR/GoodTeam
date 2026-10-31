import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ProjectIterationSuggestion, { iterationPlanningRequest } from './ProjectIterationSuggestion';

const candidate = {
  task_id: 'DEMO-7', title: 'Fix booking validation', description: 'Handle empty input',
  priority: 'high', acceptance_criteria: ['reject empty input'], eligible_to_plan: true,
};
const snapshot = {
  schema_version: 'project_iteration_backlog.v1', status: 'candidate_ready',
  project_key: 'DEMO', version: 'sha256:abc', next_candidate: candidate,
  candidates: [candidate], execution_authorized: false,
};

describe('project iteration foundation', () => {
  it('shows an evidence-bound backlog candidate without dispatching work', () => {
    const onPlan = vi.fn();
    render(<ProjectIterationSuggestion snapshot={snapshot} onPlan={onPlan} language="en" />);
    expect(screen.getByText(/DEMO-7: Fix booking validation/)).toBeInTheDocument();
    expect(onPlan).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Ask Manager to evaluate and plan' }));
    expect(onPlan).toHaveBeenCalledTimes(1);
    const request = onPlan.mock.calls[0][0];
    expect(request).toContain('DEMO-7');
    expect(request).toContain('sha256:abc');
    expect(request).toContain('reject empty input');
    expect(request).toContain('do not execute');
  });

  it('does not invent work when the backlog is empty', () => {
    const onPlan = vi.fn();
    const empty = { ...snapshot, status: 'empty', next_candidate: null, candidates: [] };
    render(<ProjectIterationSuggestion snapshot={empty} onPlan={onPlan} language="en" />);
    expect(screen.getByText(/No eligible backlog entry/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ask Manager to inspect the project' }));
    expect(onPlan).toHaveBeenCalledWith(expect.stringContaining('Discover and compare'));
    expect(iterationPlanningRequest(empty)).not.toContain('DEMO-7');
  });

  it('blocks actions on unreadable ledger data or when another task owns the work area', () => {
    const onPlan = vi.fn();
    const view = render(<ProjectIterationSuggestion snapshot={{ ...snapshot, status: 'unavailable', next_candidate: null }}
      onPlan={onPlan} />);
    expect(screen.getByRole('button')).toBeDisabled();
    view.rerender(<ProjectIterationSuggestion snapshot={snapshot} disabled onPlan={onPlan} />);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(onPlan).not.toHaveBeenCalled();
  });
});
