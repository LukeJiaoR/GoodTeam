import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { WorkFocusBanner } from './WorkPage';
import { translate } from '../../i18n';

const t = (key, params) => translate('en', key, params);
for (const verdict of [undefined, 'caution', 'request_changes', 'reject']) {
  test(`legacy banner routes ${verdict} to the review instead of submitting an empty approval`, () => {
    const onDecide = vi.fn();
    render(<MemoryRouter><WorkFocusBanner projectKey="DEMO" t={t} onDecide={onDecide}
      approvalReviews={[{ approval: 'prd', is_actionable: true, recommendation: { verdict } }]} /></MemoryRouter>);
    expect(screen.queryByRole('button', { name: t('work.review.quickApprove') })).not.toBeInTheDocument();
    expect(screen.getByRole('link')).toHaveAttribute('href', '/projects/DEMO/review/prd');
    expect(onDecide).not.toHaveBeenCalled();
  });
}

test('explicit positive recommendation retains the quick action through the parent bound handler', () => {
  const onDecide = vi.fn();
  render(<MemoryRouter><WorkFocusBanner projectKey="DEMO" t={t} onDecide={onDecide}
    approvalReviews={[{ approval: 'prd', is_actionable: true, recommendation: { verdict: 'approve' } }]} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: t('work.review.quickApprove') }));
  expect(onDecide).toHaveBeenCalledWith('prd', 'approved', '');
});
