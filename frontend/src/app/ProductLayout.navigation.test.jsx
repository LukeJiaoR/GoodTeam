import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router-dom';
import { beforeEach, expect, test } from 'vitest';
import ProductLayout from './ProductLayout';

function LocationPage() {
  const location = useLocation();
  return <main id="main-content">
    <output aria-label="Current location">{location.pathname}{location.search}</output>
  </main>;
}

function openRoute(initial) {
  const router = createMemoryRouter([{ element: <ProductLayout />, children: [{ path: '*', element: <LocationPage /> }] }], { initialEntries: [initial] });
  render(<RouterProvider router={router} />);
  return router;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('gt-language', 'en');
});

test('navigation returns to Workbench and exact memory source after visiting another page', async () => {
  const user = userEvent.setup();
  const memorySearch = `?view=memory&role=builder&q=${encodeURIComponent('任务')}&event=event-1&event_project=two`;
  const router = openRoute('/projects/one/work?view=outcomes');
  await user.click(screen.getByRole('link', { name: 'Knowledge base' }));
  await act(() => router.navigate(`/projects/one/knowledge${memorySearch}`));
  await user.click(screen.getByRole('link', { name: 'Team' }));
  await user.click(screen.getByRole('link', { name: 'Workbench' }));
  expect(router.state.location.pathname + router.state.location.search).toBe('/projects/one/work?view=outcomes');
  await user.click(screen.getByRole('link', { name: 'Knowledge base' }));
  expect(router.state.location.search).toBe(memorySearch);
});

test('workspace breadcrumb identifies the route rather than words in the project key', () => {
  openRoute('/projects/work-settings-project/knowledge');
  expect(document.querySelector('.studio-topbar strong')).toHaveTextContent('Knowledge base');
  expect(screen.getByRole('link', { name: 'Projects', exact: true })).not.toHaveAttribute('aria-current');
  expect(screen.getByRole('link', { name: 'Knowledge base' })).toHaveAttribute('aria-current', 'page');
});

test('keeps Workbench visible in the project nav and preserves icon links when collapsed', async () => {
  const user = userEvent.setup();
  openRoute('/projects/one/work');
  expect(screen.getByRole('link', { name: 'Workbench', exact: true })).toHaveAttribute('aria-current', 'page');
  await user.click(screen.getByRole('button', { name: 'Collapse sidebar', expanded: true }));
  expect(document.querySelector('.product-shell')).toHaveAttribute('data-sidebar', 'collapsed');
  for (const name of ['Projects', 'Workbench', 'Team', 'Knowledge base', 'Settings']) {
    const link = screen.getByRole('link', { name, exact: true });
    expect(link).toBeVisible();
    expect(link).toHaveAttribute('title', name);
  }
});
