import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import TaskDetailModal, { findMatchingReview } from './workspace/WorkTaskDetailDialog';
import { translate } from '../../i18n';

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

});
