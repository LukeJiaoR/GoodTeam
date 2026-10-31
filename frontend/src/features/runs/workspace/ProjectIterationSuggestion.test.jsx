import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ProjectIterationSuggestion from './ProjectIterationSuggestion';

const snapshot = {
  schema_version: 'project_iteration_backlog.v1',
  project_key: 'DEMO',
  status: 'candidate_ready',
  version: 'sha256:abc',
  next_candidate: { task_id: 'DEMO-7', title: 'Fix validation', priority: 'high' },
};

describe('project iteration recommendation', () => {
  it('asks Manager to choose without automatically running Workers', () => {
    const onPlan = vi.fn();
    render(<ProjectIterationSuggestion snapshot={snapshot} onPlan={onPlan} />);
    expect(onPlan).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Ask Manager to choose/ }));
    expect(onPlan).toHaveBeenCalledExactlyOnceWith({
      action: 'plan_next_iteration',
      expected_backlog_version: snapshot.version,
      confirmed: true,
    });
  });

  it('shows a large parallel plan without implying Workers were launched', () => {
    const tasks = Array.from({ length: 16 }, (_, i) => ({
      task_id: `DEMO-${i + 1}`, title: `Task ${i + 1}`,
    }));
    const choice = {
      schema_version: 'project_iteration_choice.v1',
      status: 'recommended',
      backlog_version: snapshot.version,
      execution_mode: 'parallel',
      selected_task_ids: tasks.map(task => task.task_id),
      selected_tasks: tasks,
      selected_task_id: tasks[0].task_id,
      rationale: 'These independent tasks have disjoint declared paths.',
      max_simultaneous_workers_per_dispatch: 10,
    };
    render(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} />);
    fireEvent.click(screen.getByText('View candidate tasks (16)'));
    expect(screen.getByText('DEMO-16 — Task 16')).toBeInTheDocument();
    expect(screen.getByText(/Direct-dispatch cap: 10 concurrent Workers/)).toBeInTheDocument();
    expect(screen.getByText(/no workers launched, isolation or merge verified/)).toBeInTheDocument();
  });

  it('marks an old recommendation stale after the backlog changes', () => {
    const choice = {
      schema_version: 'project_iteration_choice.v1',
      status: 'stale', backlog_version: 'sha256:old', selected_task_id: 'DEMO-1',
    };
    render(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} />);
    expect(screen.getByText(/Previous recommendation is stale/)).toBeInTheDocument();
  });
});
