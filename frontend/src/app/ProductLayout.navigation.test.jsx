import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider, useBlocker, useLocation } from 'react-router-dom';
import { beforeEach, expect, test } from 'vitest';
import ProductLayout from './ProductLayout';

function LocationPage() {
  const location = useLocation();
  const blocked = useBlocker(location.pathname.endsWith('/review/dirty'));
  return <main id="main-content">
    <output aria-label="Current location">{location.pathname}{location.search}</output>
    {blocked.state === 'blocked' ? <button onClick={() => blocked.reset()}>Keep editing</button> : null}
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

test('navigation returns to the selected historical task and exact memory source after visiting another page', async () => {
  const user = userEvent.setup();
  const router = openRoute('/projects/one/work/runs/history-1?view=outcomes');
  await user.click(screen.getByRole('link', { name: 'Knowledge base' }));
  await act(() => router.navigate('/projects/one/knowledge?view=memory&role=builder&q=source&event=event-1&event_project=two'));
  await user.click(screen.getByRole('link', { name: 'Team' }));
  await user.click(screen.getByRole('link', { name: 'Workbench' }));
  expect(router.state.location.pathname + router.state.location.search).toBe('/projects/one/work/runs/history-1?view=outcomes');
  await user.click(screen.getByRole('link', { name: 'Knowledge base' }));
  expect(router.state.location.search).toBe('?view=memory&role=builder&q=source&event=event-1&event_project=two');
});

test('review selection survives nav but another project starts at its own unselected review', async () => {
  const user = userEvent.setup();
  const router = openRoute('/projects/one/review/architecture');
  await user.click(screen.getByRole('link', { name: 'Team' }));
  await user.click(screen.getByRole('link', { name: 'Reviews' }));
  expect(router.state.location.pathname).toBe('/projects/one/review/architecture');
  await act(() => router.navigate('/projects/two/team'));
  await user.click(screen.getByRole('link', { name: 'Reviews' }));
  expect(router.state.location.pathname).toBe('/projects/two/review');
  await act(() => router.navigate('/projects/one/team'));
  await user.click(screen.getByRole('link', { name: 'Reviews' }));
  expect(router.state.location.pathname).toBe('/projects/one/review/architecture');
});

test('Back and direct current-task navigation update the remembered destination rather than locking users to old history', async () => {
  const user = userEvent.setup();
  const router = openRoute('/projects/one/work/runs/history-1');
  await user.click(screen.getByRole('link', { name: 'Team' }));
  await act(() => router.navigate(-1));
  expect(router.state.location.pathname).toBe('/projects/one/work/runs/history-1');
  await act(() => router.navigate('/projects/one/work'));
  await user.click(screen.getByRole('link', { name: 'Knowledge base' }));
  await user.click(screen.getByRole('link', { name: 'Workbench' }));
  expect(router.state.location.pathname).toBe('/projects/one/work');
});

test('remembered navigation respects the existing unsaved-review blocker', async () => {
  const user = userEvent.setup();
  const router = openRoute('/projects/one/review/dirty');
  await user.click(screen.getByRole('link', { name: 'Team' }));
  expect(router.state.location.pathname).toBe('/projects/one/review/dirty');
  await user.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(router.state.location.pathname).toBe('/projects/one/review/dirty');
});
