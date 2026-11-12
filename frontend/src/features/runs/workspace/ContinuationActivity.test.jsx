import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ProjectContinuationControls from './ProjectContinuationControls';
import { continuationActivity } from './continuationActivity';

it('shows actual waits and original Manager rationale without claiming execution or starting actions', () => {
  const onAction = vi.fn();
  const grant = { id: 'grant', status: 'active', reason: 'awaiting_project_context', waiting_attempt_id: 'grant:2',
    manager_calls: 2, max_manager_calls: 4, batches_started: 0, max_batches: 3 };
  const session = { project_continuation: grant, timeline: [
    { event: 'project_iteration_choice_recorded', details: { choice: { planning_attempt_id: 'other:2', status: 'waiting', rationale: 'OTHER PRIVATE REASON' } } },
    { event: 'project_iteration_choice_recorded', details: { choice: { planning_attempt_id: 'grant:2', status: 'waiting', rationale: 'Wait for the dependency owner to confirm the schema.' } } },
  ] };
  const ui = (current, language = 'zh-CN') => <ProjectContinuationControls snapshot={{ candidates: [] }} session={current}
    projectKey="APP" onAction={onAction} canStart language={language} />;
  const view = render(ui(session));
  expect(screen.getByText(/项目自动迭代 · 等待项目事实变化/)).toBeInTheDocument();
  expect(screen.getByText(/上次 Manager 判断依据: Wait for the dependency owner/)).toBeInTheDocument();
  expect(screen.queryByText(/OTHER PRIVATE REASON/)).not.toBeInTheDocument();
  expect(screen.queryByText(/自动推进中/)).not.toBeInTheDocument();
  view.rerender(ui({ ...session, project_continuation: { ...grant, waiting_attempt_id: 'other:2' } }));
  expect(screen.queryByText(/上次 Manager 判断依据/)).not.toBeInTheDocument();
  view.rerender(ui({ project_continuation: { ...grant, pending_batch: { task_ids: ['APP-1'], run_id: '' } } }, 'en'));
  expect(screen.getByText(/Reserved, awaiting start/)).toBeInTheDocument();
  expect(screen.getByText(/no Worker start is implied/)).toBeInTheDocument();
  const running = { project_continuation: { ...grant, pending_batch: { run_id: 'batch', task_ids: ['APP-1'] } },
    project_task_batches: { batch: { status: 'running', expected_continuation_id: 'grant' } } };
  view.rerender(ui(running, 'en'));
  expect(screen.getByText(/Awaiting Worker results/)).toBeInTheDocument();
  expect(screen.getByText(/Check its invocation evidence for current process activity/)).toBeInTheDocument();
  view.rerender(ui({ ...running, project_task_batches: { batch: { status: 'running', expected_continuation_id: 'other' } } }, 'en'));
  expect(screen.getByText(/Batch attribution needs checking/)).toBeInTheDocument();
  view.rerender(ui({ project_continuation: { ...grant, status: 'completed', reason: 'authorized_tasks_delivered' } }));
  expect(screen.getByText(/本次授权已结束/)).toBeInTheDocument();
  expect(screen.queryByText(/授权任务已交付/)).not.toBeInTheDocument();
  expect(onAction).not.toHaveBeenCalled();
});

it('distinguishes retained calls, applied decisions, new facts and resource waits without new state', () => {
  const value = { id: 'grant', status: 'active' };
  const activity = extra => continuationActivity({ project_continuation: { ...value, ...extra } });
  expect(activity({ discovery_pending: {} }).label).toBe('Awaiting original judgment result');
  expect(activity({ discovery_pending: { selection: { new_task: null } } }).label).toBe('Decision retained, awaiting application');
  expect(activity({ planning_backlog_version: 'version' }).label).toBe('Awaiting original planning result');
  for (const reason of ['planning_context_changed', 'discovery_context_changed', 'delivery_feedback_pending']) {
    expect(activity({ reason }).label).toBe('Awaiting next judgment');
  }
  expect(activity({ reason: 'manager_resource_busy' }).label).toBe('Waiting for available resources');
  expect(activity({ reason: 'backlog_judgment_waiting', discovery_last: { rationale: 'Original evidence' } }).rationale).toBe('Original evidence');
  expect(continuationActivity({ project_continuation: { ...value, status: 'stopped' } })).toBeNull();
});
