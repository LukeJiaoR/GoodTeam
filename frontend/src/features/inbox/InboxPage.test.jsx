import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import InboxPage from './InboxPage';

const mocks = vi.hoisted(() => ({
  getProjectProjections: vi.fn(),
}));

vi.mock('../../lib/api/globalApi', () => ({ getProjectProjections: mocks.getProjectProjections }));

function TranslationLayout() {
  return <Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />;
}

function renderInbox() {
  const router = createMemoryRouter([
    { element: <TranslationLayout />, children: [{ path: '/inbox', element: <InboxPage /> }] },
  ], { initialEntries: ['/inbox'] });
  render(<RouterProvider router={router} />);
}

afterEach(() => {
  vi.clearAllMocks();
});

test('shows the last-updated time and a refresh action', async () => {
  mocks.getProjectProjections.mockResolvedValue([{
    item: { project: { project_key: 'DEMO' } },
    state: { approval_reviews: [{ approval: 'prd', is_actionable: true }] },
    error: null,
  }]);

  renderInbox();

  expect(await screen.findByText('Last updated', { exact: false })).toBeVisible();
  expect(screen.getByText('PRD review is waiting')).toBeVisible();

  mocks.getProjectProjections.mockResolvedValue([]);
  await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(await screen.findByText('Nothing needs your attention')).toBeVisible();
});
