import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, test } from 'vitest';
import { createProductRoutes } from '../../app/AppRouter';

function Placeholder() { return <main id="main-content">Project content</main>; }
function openSettings(path) {
  const router = createMemoryRouter(createProductRoutes({ ProjectsComponent: Placeholder, WorkComponent: Placeholder, TeamComponent: Placeholder, KnowledgeComponent: Placeholder, ReviewComponent: Placeholder }), { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); localStorage.setItem('gt-language', 'en'); });

test('settings is a real destination and shares appearance and language preferences with the shell', async () => {
  const user = userEvent.setup();
  const router = openSettings('/settings');
  const main = screen.getByRole('main');
  expect(within(main).getByRole('heading', { name: 'Settings' })).toBeVisible();
  await user.click(within(main).getByRole('button', { name: 'Dark', exact: true }));
  expect(document.documentElement).toHaveClass('dark');
  expect(localStorage.getItem('color-scheme')).toBe('dark');
  await user.selectOptions(within(main).getByRole('combobox'), 'zh-CN');
  expect(within(main).getByRole('heading', { name: '设置' })).toBeVisible();
  expect(localStorage.getItem('gt-language')).toBe('zh-CN');
  expect(router.state.location.pathname).toBe('/settings');
});

test('project settings keeps deep-linked work context and exposes named navigation for compact layouts', async () => {
  const user = userEvent.setup();
  const router = openSettings('/projects/DEMO/work/runs/history?view=outcomes');
  await user.click(screen.getByRole('link', { name: 'Settings', exact: true }));
  expect(router.state.location.pathname).toBe('/projects/DEMO/settings');
  expect(screen.getByRole('link', { name: 'Configure team' })).toHaveAttribute('href', '/projects/DEMO/team');
  expect(screen.getByRole('link', { name: 'Workbench' })).toHaveAttribute('aria-label', 'Workbench');
  await user.click(screen.getByRole('link', { name: 'Workbench' }));
  expect(router.state.location.pathname + router.state.location.search).toBe('/projects/DEMO/work/runs/history?view=outcomes');
  await act(() => router.navigate(-1));
  expect(screen.getByRole('heading', { name: 'Settings' })).toBeVisible();
});
