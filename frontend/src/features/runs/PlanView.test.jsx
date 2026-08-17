import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import TaskDetailModal from '../../components/TaskDetailModal';
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

describe('TaskDetailModal', () => {
  test('renders all task properties accessibly without reviews', () => {
    const task = sampleBoard.columns.backlog[0];
    let closed = false;
    render(
      <MemoryRouter>
        <TaskDetailModal task={task} onClose={() => { closed = true; }} t={t} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getByText('GT-1')).toBeVisible();
    expect(screen.getByText('Draft and approve PRD')).toBeVisible();
    expect(screen.getByText('Workflow stage task for product_requirement.')).toBeVisible();
    expect(screen.getByText('PRD generated')).toBeVisible();
    expect(screen.getByText('Approved by lead')).toBeVisible();
    expect(screen.getByText('stage:product_requirement')).toBeVisible();

    const closeButtons = screen.getAllByRole('button', { name: /关闭|Close/i });
    expect(closeButtons.length).toBeGreaterThan(0);
    fireEvent.click(closeButtons[0]);
    expect(closed).toBe(true);
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
