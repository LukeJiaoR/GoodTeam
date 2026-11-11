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
    const onQueue = vi.fn();
    const view = render(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} onQueue={onQueue} />);
    fireEvent.click(screen.getByText('View candidate tasks (16)'));
    expect(screen.getByText('DEMO-16 — Task 16')).toBeInTheDocument();
    expect(screen.getByText(/up to 10 Workers run concurrently/)).toBeInTheDocument();
    expect(screen.getByText(/no workers launched, isolation or merge verified/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add selected tasks to existing queue' }));
    expect(onQueue).toHaveBeenCalledExactlyOnceWith({
      action: 'queue_iteration_choice',
      expected_backlog_version: snapshot.version,
      confirmed: true,
    });
    view.rerender(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} onQueue={onQueue}
      queuedTasks={tasks.map(item => ({ id: `proposal-${item.task_id}`, revision: 1, project_task_id: item.task_id, status: 'queued' }))} />);
    expect(screen.queryByRole('button', { name: 'Confirm and run selected tasks (uses model)' })).not.toBeInTheDocument();
    expect(screen.getAllByText(/Legacy policy not projected/)).toHaveLength(16);
    view.rerender(<ProjectIterationSuggestion snapshot={snapshot} choice={choice} onQueue={onQueue}
      queuedTasks={tasks.map(item => ({ id: `proposal-${item.task_id}`, revision: 1, web_search: null, project_task_id: item.task_id, status: 'queued' }))} />);
    expect(screen.getByText(/Queued tasks run in isolated workspaces/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add selected tasks to existing queue' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and run selected tasks (uses model)' }));
    expect(onQueue).toHaveBeenLastCalledWith({
      action: 'start_queued_task_batch', proposal_ids: tasks.map(item => `proposal-${item.task_id}`),
      expected_revisions: Object.fromEntries(tasks.map(item => [`proposal-${item.task_id}`, 1])), confirmed: true,
    });
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

it('shows a chat assignment scope and uses the existing exact-revision batch confirmation', () => {
  const onQueue = vi.fn();
  render(<ProjectIterationSuggestion snapshot={snapshot} onQueue={onQueue} queuedTasks={[{
    id: 'chat-task', web_search: null, revision: 3, project_task_id: 'DEMO-8', source: 'manager_conversation', status: 'queued',
    exact_request: 'Research the entry point.', assignment: { role: 'research', execution_policy: 'read_only',
      write_domains: [], acceptance_criteria: ['Cite the actual entry point.'],
      delegation: { purpose: 'Trace login failures', context: 'Observed timeout', scope: 'Diagnosis only' } },
  }]} />);
  for (const text of ['Trace login failures', 'Observed timeout', 'Diagnosis only']) expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.getByText('Research the entry point.')).toBeInTheDocument();
  expect(screen.queryByText('Original assignment member')).not.toBeInTheDocument();
  expect(screen.getByText('Cite the actual entry point.')).toBeInTheDocument();
  expect(screen.getByText(/Read only; no project file changes/)).toBeInTheDocument();
  expect(onQueue).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm scope, saved configuration and model charges and run this task' }));
  expect(onQueue).toHaveBeenCalledExactlyOnceWith({ action: 'start_queued_task_batch', proposal_ids: ['chat-task'],
    expected_revisions: { 'chat-task': 3 }, confirmed: true });
});


it('keeps a queued group confirmable with new revisions after configuration invalidates the recommendation', () => {
  const onQueue = vi.fn();
  render(<ProjectIterationSuggestion snapshot={snapshot} onQueue={onQueue}
    choice={{ schema_version: 'project_iteration_choice.v1', status: 'stale' }} queuedTasks={[
      { id: 'a', project_task_id: 'DEMO-1', status: 'queued', source: 'manager_project_iteration', source_event_id: 'choice-1', revision: 2, web_search: null },
      { id: 'b', project_task_id: 'DEMO-2', status: 'queued', source: 'manager_project_iteration', source_event_id: 'choice-1', revision: 4, web_search: null },
    ]} />);
  fireEvent.click(screen.getByRole('button', { name: /run queued group/ }));
  expect(onQueue).toHaveBeenCalledExactlyOnceWith({ action: 'start_queued_task_batch', proposal_ids: ['a', 'b'],
    expected_revisions: { a: 2, b: 4 }, confirmed: true });
});
