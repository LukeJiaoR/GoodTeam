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

describe('project iteration planning', () => {
  it('explicitly calls the read-only Manager action without dispatching work', () => {
    const onPlan = vi.fn();
    render(<ProjectIterationSuggestion snapshot={snapshot} onPlan={onPlan} language="en" />);
    expect(screen.getByText(/DEMO-7: Fix booking validation/)).toBeInTheDocument();
    expect(onPlan).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Ask Manager to choose (uses model)' }));
    expect(onPlan).toHaveBeenCalledExactlyOnceWith({
      action: 'plan_next_iteration', expected_backlog_version: 'sha256:abc', confirmed: true,
    });
  });

  it('shows a durable Manager decision as advice rather than executable work', () => {
    const onPlan = vi.fn();
    const choice = {
      schema_version: 'project_iteration_choice.v1',
      backlog_version: 'sha256:abc', status: 'recommended',
      selected_task_id: 'DEMO-7', selected_title: 'Fix booking validation',
      rationale: 'Verified current acceptance gap; first handle empty requests.',
      execution_authorized: false,
    };
    render(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} onPlan={onPlan} />);
    expect(screen.getByText(/Manager recommends: DEMO-7/)).toBeInTheDocument();
    expect(screen.getByText(/Advisory only; no execution started/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ask Manager/ })).not.toBeInTheDocument();
    expect(onPlan).not.toHaveBeenCalled();
  });

  it('renders a parallel batch as advice, not launched Workers', () => {
    const onPlan = vi.fn();
    const choice = {
      schema_version: 'project_iteration_choice.v1',
      backlog_version: 'sha256:abc', status: 'recommended',
      execution_mode: 'parallel', selected_task_id: 'DEMO-7',
      selected_task_ids: ['DEMO-7', 'DEMO-8'],
      selected_tasks: [
        { task_id: 'DEMO-7', title: 'Fix booking validation' },
        { task_id: 'DEMO-8', title: 'Improve invoice preview' },
      ],
      rationale: 'The two tasks have declared disjoint paths, but execution isolation is not verified.',
      parallel_execution_authorized: false,
      max_simultaneous_workers_per_dispatch: 10,
    };
    render(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} onPlan={onPlan} language="en" />);
    expect(screen.getByText(/Manager recommends parallel tasks/)).toBeInTheDocument();
    expect(screen.getByText('DEMO-7 — Fix booking validation')).toBeInTheDocument();
    expect(screen.getByText('DEMO-8 — Improve invoice preview')).toBeInTheDocument();
    expect(screen.getByText(/no workers launched, isolation or merge verified/)).toBeInTheDocument();
    expect(screen.getByText(/Direct-dispatch cap: 10 concurrent Workers/)).toBeInTheDocument();
    expect(onPlan).not.toHaveBeenCalled();
  });

  it('keeps a large parallel batch accessible without flooding the conversation', () => {
    const items = Array.from({ length: 16 }, (_, index) => ({
      task_id: `DEMO-${index + 1}`, title: `Task ${index + 1}`,
    }));
    const choice = {
      schema_version: 'project_iteration_choice.v1',
      backlog_version: 'sha256:abc', status: 'recommended',
      execution_mode: 'parallel', selected_task_id: 'DEMO-1',
      selected_task_ids: items.map(item => item.task_id),
      selected_tasks: items,
      rationale: 'The project has multiple independent tasks with declared disjoint scopes.',
    };
    render(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} />);
    const details = screen.getByText('View candidate tasks (16)').closest('details');
    expect(details).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('View candidate tasks (16)'));
    expect(details).toHaveAttribute('open');
    expect(screen.getByText('DEMO-16 — Task 16')).toBeInTheDocument();
  });

  it('invalidates old decisions after the source backlog version changes', () => {
    const oldChoice = {
      schema_version: 'project_iteration_choice.v1', backlog_version: 'sha256:old',
      status: 'stale', selected_task_id: 'DEMO-5',
    };
    render(<ProjectIterationSuggestion snapshot={snapshot} choice={oldChoice} onPlan={vi.fn()} />);
    expect(screen.getByText(/Previous recommendation is stale/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ask Manager to choose (uses model)' })).toBeEnabled();
  });

  it('does not invent work when the backlog is empty', () => {
    const onInspect = vi.fn();
    const empty = { ...snapshot, status: 'empty', next_candidate: null, candidates: [] };
    render(<ProjectIterationSuggestion snapshot={empty} onInspect={onInspect} language="en" />);
    fireEvent.click(screen.getByRole('button', { name: 'Ask Manager to inspect the project' }));
    expect(onInspect).toHaveBeenCalledWith(expect.stringContaining('Identify possible next iterations'));
    expect(iterationPlanningRequest(empty)).not.toContain('DEMO-7');
  });

  it('blocks planning with unreadable data or when another workflow owns the surface', () => {
    const onPlan = vi.fn();
    const view = render(<ProjectIterationSuggestion snapshot={{
      ...snapshot, status: 'unavailable', next_candidate: null,
    }} onPlan={onPlan} />);
    expect(screen.getByRole('button')).toBeDisabled();
    view.rerender(<ProjectIterationSuggestion snapshot={snapshot} disabled onPlan={onPlan} />);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(onPlan).not.toHaveBeenCalled();
  });
});
