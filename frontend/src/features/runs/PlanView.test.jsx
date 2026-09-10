import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import TaskDetailModal, { findMatchingReview } from './workspace/WorkTaskDetailDialog';
import { translate } from '../../i18n';
import { PlanView } from './WorkPage';

const sampleBoard = {
  counts: { backlog: 2, doing: 1, blocked: 0, done: 1 },
  columns: {
    backlog: [
      {
        id: 'GT-1',
        title: 'Draft and approve PRD',
        description: 'Workflow stage task for product_requirement.',
        role: 'product_requirement',
        status: 'todo',
        priority: 'high',
        dependencies: [],
        acceptance_criteria: ['PRD generated', 'Approved by lead'],
        external_refs: ['stage:product_requirement'],
        created_at: '2026-07-19T00:05:35Z',
        updated_at: '2026-07-30T12:03:31Z',
      },
      {
        id: 'GT-2',
        title: 'Design and approve RFC',
        description: 'Workflow stage task for architect.',
        role: 'architect',
        status: 'todo',
        priority: 'medium',
        dependencies: ['GT-1'],
        acceptance_criteria: [],
        external_refs: ['stage:architect'],
        created_at: '2026-07-19T00:05:35Z',
        updated_at: '2026-07-30T12:03:31Z',
      },
    ],
    doing: [
      {
        id: 'GT-3',
        title: 'Implement approved design',
        description: 'Workflow stage task for implementation.',
        role: 'implementation',
        status: 'in_progress',
        priority: 'critical',
        dependencies: ['GT-2'],
        acceptance_criteria: ['Code passed tests'],
        external_refs: ['stage:implementation'],
        created_at: '2026-07-19T00:05:35Z',
        updated_at: '2026-07-30T12:03:31Z',
      },
    ],
    blocked: [],
    done: [],
  },
};

const sampleReviews = [
  {
    approval: 'prd',
    artifact: 'prd',
    artifact_path: '/tmp/artifacts/prd/prd.md',
    content: '# Product Requirement Document\n\nBuild github actions deploy workflow.',
    decision: 'pending',
    is_actionable: true,
    recommendation: {
      verdict: 'approve',
      reasons: ['Artifact is well structured and ready for implementation.'],
    },
  },
  {
    approval: 'rfc',
    artifact: 'rfc',
    artifact_path: '',
    content: '',
    decision: 'pending',
    is_actionable: false,
  },
];

const t = (key, params) => translate('zh-CN', key, params);

describe('WorkTaskDetailDialog', () => {
  test('prefers the projected review target id over role heuristics', () => {
    const review = findMatchingReview(
      { id: 'GT-ARCH', role: 'architect', review_target_id: 'rfc' },
      [
        { approval: 'security', is_actionable: true },
        { approval: 'rfc', is_actionable: false },
      ],
    );

    expect(review?.approval).toBe('rfc');
  });

  test('treats an explicit empty projected review target as no review', () => {
    const reviews = [{ approval: 'security', is_actionable: true }];

    expect(findMatchingReview(
      { id: 'GT-FUTURE', role: 'architect', review_target_id: '' },
      reviews,
    )).toBeNull();
    expect(findMatchingReview(
      { id: 'GT-LEGACY', role: 'architect' },
      reviews,
    )?.approval).toBe('security');
  });

  test.each([
    ['todo', '待办', 'pending'],
    ['up_next', '待执行', 'pending'],
    ['in_progress', '进行中', 'active'],
    ['working', '进行中', 'active'],
    ['review', '审查中', 'active'],
    ['reviewing', '审查中', 'active'],
    ['changes_requested', '要求修改', 'active'],
    ['fixing', '修改中', 'active'],
    ['pending', '待审批', 'pending'],
    ['waiting', '等待中', 'pending'],
    ['done', '已完成', 'success'],
    ['completed', '已完成', 'success'],
    ['blocked', '受阻', 'danger'],
    ['canceled', '已停止', 'danger'],
    ['stopped', '已停止', 'danger'],
  ])('renders task status %s with a translated %s tone', (status, label, tone) => {
    render(
      <MemoryRouter>
        <TaskDetailModal
          task={{
            id: `GT-${status}`,
            title: `Task ${status}`,
            role: 'architect',
            status,
            review_target_id: '',
          }}
          reviews={[{ approval: 'security', is_actionable: true }]}
          onClose={() => {}}
          t={t}
        />
      </MemoryRouter>,
    );

    const badge = screen.getByText(label, { selector: '.product-status-badge' });
    expect(badge).toHaveAttribute('data-tone', tone);
    expect(screen.queryByText(`work.plan.status.${status}`)).not.toBeInTheDocument();
  });

  test('renders only meaningful project-facing task context without reviews', () => {
    const task = sampleBoard.columns.backlog[1];
    const tasks = Object.values(sampleBoard.columns).flat();
    let closed = false;
    render(
      <MemoryRouter>
        <TaskDetailModal
          language="zh-CN"
          task={task}
          tasks={tasks}
          onClose={() => { closed = true; }}
          t={t}
        />
      </MemoryRouter>,
    );

    const dialog = screen.getByRole('dialog');
    const created = new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(new Date(task.created_at));
    const updated = new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(new Date(task.updated_at));

    expect(dialog).toBeVisible();
    expect(within(dialog).getByText('Design and approve RFC')).toBeVisible();
    expect(within(dialog).getByText('Draft and approve PRD')).toBeVisible();
    expect(within(dialog).getByText('架构师')).toBeVisible();
    expect(within(dialog).getByText(created)).toBeVisible();
    expect(within(dialog).getByText(updated)).toBeVisible();
    expect(within(dialog).queryByText('GT-2')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('GT-1')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('Workflow stage task for architect.')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('stage:architect')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('架构师 (Architect)')).not.toBeInTheDocument();
    expect(within(dialog).queryByText(task.created_at)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(task.updated_at)).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('heading', { name: '任务描述' })).not.toBeInTheDocument();

    const closeButtons = screen.getAllByRole('button', { name: /关闭|Close/i });
    expect(closeButtons.length).toBeGreaterThan(0);
    fireEvent.click(closeButtons[0]);
    expect(closed).toBe(true);
  });

  test('keeps meaningful descriptions and acceptance criteria', () => {
    const task = {
      id: 'GT-CONTEXT',
      title: 'Prepare the production rollout',
      description: 'Confirm the rollout is safe for the current OAuth mission.',
      acceptance_criteria: ['Rollback path is documented'],
      review_target_id: '',
    };
    render(
      <MemoryRouter>
        <TaskDetailModal task={task} tasks={[task]} onClose={() => {}} t={t} />
      </MemoryRouter>,
    );

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(task.description)).toBeVisible();
    expect(within(dialog).getByText('Rollback path is documented')).toBeVisible();
  });

  test('does not expose or guess unresolved and non-informative metadata', () => {
    const task = {
      id: 'GT-UNRESOLVED',
      title: 'GT-UNRESOLVED',
      description: 'None',
      role: '—',
      status: 'None',
      priority: '—',
      dependencies: ['GT-MISSING'],
      external_refs: ['stage:architect'],
      artifact_refs: ['artifact:missing'],
      created_at: 'not-a-timestamp',
      review_target_id: '',
    };
    const sameRoleTask = {
      id: 'GT-SAME-ROLE',
      title: 'Do not infer this task',
      role: 'architect',
    };
    render(
      <MemoryRouter>
        <TaskDetailModal
          task={task}
          tasks={[task, sameRoleTask]}
          onClose={() => {}}
          t={t}
        />
      </MemoryRouter>,
    );

    const dialog = screen.getByRole('dialog', { name: '任务详情' });
    expect(within(dialog).queryByText('GT-UNRESOLVED')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('GT-MISSING')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('Do not infer this task')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('stage:architect')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('artifact:missing')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('not-a-timestamp')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('None')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('—')).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('heading', { name: '任务属性' })).not.toBeInTheDocument();
  });

  test('passes the board task catalog to dependency resolution', () => {
    render(
      <MemoryRouter>
        <PlanView
          board={sampleBoard}
          language="zh-CN"
          reviews={sampleReviews}
          projectKey="CAT-MBTI-APP"
          onDecide={vi.fn()}
          t={t}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: /Design and approve RFC/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Draft and approve PRD')).toBeVisible();
    expect(within(dialog).queryByText('GT-1')).not.toBeInTheDocument();
  });

  test('previews artifact with rendered markdown and raw source toggle', () => {
    const task = sampleBoard.columns.backlog[0];
    render(
      <MemoryRouter>
        <TaskDetailModal
          task={task}
          reviews={sampleReviews}
          projectKey="CAT-MBTI-APP"
          onClose={() => {}}
          t={t}
        />
      </MemoryRouter>,
    );

    // Switch to artifact preview tab
    const previewTab = screen.getByRole('button', { name: /产物预览|Artifact Preview/i });
    fireEvent.click(previewTab);

    // Check rendered markdown header and text
    expect(screen.getByRole('heading', { level: 1, name: /Product Requirement Document/ })).toBeVisible();
    expect(screen.getByText(/Build github actions deploy workflow\./)).toBeVisible();
    expect(screen.getByText('/tmp/artifacts/prd/prd.md')).not.toBeVisible();
    fireEvent.click(screen.getByText(t('files.location')));
    expect(screen.getByText('/tmp/artifacts/prd/prd.md')).toBeVisible();

    // Toggle to raw markdown source
    const rawBtn = screen.getByRole('button', { name: /源码|Raw/i });
    fireEvent.click(rawBtn);
    expect(screen.getByText(/# Product Requirement Document/)).toBeVisible();
  });

  test('performs approval decision directly from modal', async () => {
    const task = sampleBoard.columns.backlog[0];
    const onDecide = vi.fn().mockResolvedValue({ status: 'approved' });
    let closed = false;

    render(
      <MemoryRouter>
        <TaskDetailModal
          task={task}
          reviews={sampleReviews}
          projectKey="CAT-MBTI-APP"
          onDecide={onDecide}
          onClose={() => { closed = true; }}
          t={t}
        />
      </MemoryRouter>,
    );

    // Since it's actionable, it defaults or can switch to approval tab
    const approvalTab = screen.getByRole('button', { name: /审批操作|审批决策|Approval/i });
    fireEvent.click(approvalTab);

    expect(screen.getByText(/评审委员会建议|Review Committee Recommendation/)).toBeVisible();

    // Click approve button
    const approveButton = screen.getByRole('button', { name: /批准通过|Approve/i });
    fireEvent.click(approveButton);

    expect(onDecide).toHaveBeenCalledWith('prd', 'approved', '');
    await vi.waitFor(() => {
      expect(closed).toBe(true);
    });
  });

  test('keeps modal open and displays error when onDecide fails', async () => {
    const task = sampleBoard.columns.backlog[0];
    const onDecide = vi.fn().mockRejectedValue(new Error('Server failed to approve'));
    let closed = false;

    render(
      <MemoryRouter>
        <TaskDetailModal
          task={task}
          reviews={sampleReviews}
          projectKey="CAT-MBTI-APP"
          onDecide={onDecide}
          onClose={() => { closed = true; }}
          t={t}
        />
      </MemoryRouter>,
    );

    const approveButton = screen.getByRole('button', { name: /批准通过|Approve/i });
    fireEvent.click(approveButton);

    expect(onDecide).toHaveBeenCalledWith('prd', 'approved', '');
    await vi.waitFor(() => {
      expect(screen.getByText('Server failed to approve')).toBeVisible();
    });
    expect(closed).toBe(false);
  });

  test('closes on Escape key', () => {
    const task = sampleBoard.columns.backlog[1];
    let closed = false;
    render(
      <MemoryRouter>
        <TaskDetailModal task={task} onClose={() => { closed = true; }} t={t} />
      </MemoryRouter>,
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(closed).toBe(true);
  });

  test('automatically opens task modal when ?approval= query param matches a gate', () => {
    render(
      <MemoryRouter initialEntries={['/projects/CAT-MBTI-APP/work?view=plan&approval=prd']}>
        <PlanView
          board={sampleBoard}
          reviews={sampleReviews}
          projectKey="CAT-MBTI-APP"
          onDecide={vi.fn()}
          t={t}
        />
      </MemoryRouter>,
    );

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeVisible();
    expect(screen.getAllByText('GT-1').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Draft and approve PRD').length).toBeGreaterThanOrEqual(1);
  });
});

test('Escape closes an expanded artifact location before closing the task dialog', () => {
  const onClose = vi.fn();
  render(<MemoryRouter><TaskDetailModal task={sampleBoard.columns.backlog[0]} reviews={sampleReviews} onClose={onClose} t={t} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: /产物预览|Artifact Preview/i }));
  const summary = screen.getByText(t('files.location')).closest('summary');
  fireEvent.click(summary);
  expect(screen.getByText('/tmp/artifacts/prd/prd.md')).toBeVisible();
  fireEvent.keyDown(summary, { key: 'Escape' });
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText('/tmp/artifacts/prd/prd.md')).not.toBeVisible();
  fireEvent.keyDown(summary, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledOnce();
});
