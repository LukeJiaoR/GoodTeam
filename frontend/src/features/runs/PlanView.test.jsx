import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import TaskDetailModal, { findMatchingReview } from './workspace/WorkTaskDetailDialog';
import { translate } from '../../i18n';

const t = (key, params) => translate('en', key, params);
const task = { id: 'GT-1', title: 'Draft and approve PRD', role: 'product_requirement', review_target_id: 'prd' };
const reviews = [{ approval: 'prd', artifact: 'prd', content: '# PRD\nA bounded plan.',
  decision: 'pending', is_actionable: true, recommendation: { verdict: 'caution', reasons: [] } }];

describe('WorkTaskDetailDialog review routing', () => {
  test('prefers the projected review target id over role heuristics', () => {
    const review = findMatchingReview(
      { id: 'GT-ARCH', role: 'architect', review_target_id: 'rfc' },
      [{ approval: 'security', is_actionable: true }, { approval: 'rfc', is_actionable: false }],
    );
    expect(review?.approval).toBe('rfc');
  });

  test('treats an explicit empty projected review target as no review', () => {
    const items = [{ approval: 'security', is_actionable: true }];
    expect(findMatchingReview({ id: 'GT-FUTURE', role: 'architect', review_target_id: '' }, items)).toBeNull();
    expect(findMatchingReview({ id: 'GT-LEGACY', role: 'architect' }, items)?.approval).toBe('security');
  });

  test('opens the exact review instead of submitting an unbound decision from the task projection', () => {
    const onOpenReview = vi.fn();
    const onDecide = vi.fn();
    render(<MemoryRouter><TaskDetailModal task={task} reviews={reviews} projectKey="DEMO"
      onOpenReview={onOpenReview} onDecide={onDecide} onClose={vi.fn()} t={t} /></MemoryRouter>);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: t('work.panel.reviewDetails') }));
    expect(onOpenReview).toHaveBeenCalledWith('prd');
    expect(onDecide).not.toHaveBeenCalled();
  });

  test('the legacy task detail links to the same full review without a mutation callback', () => {
    const onDecide = vi.fn();
    render(<MemoryRouter><TaskDetailModal task={task} reviews={reviews} projectKey="DEMO"
      onDecide={onDecide} onClose={vi.fn()} t={t} /></MemoryRouter>);
    expect(screen.getByRole('link')).toHaveAttribute('href', '/projects/DEMO/review/prd');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(onDecide).not.toHaveBeenCalled();
  });
});
