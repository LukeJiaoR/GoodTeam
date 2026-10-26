import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectsPage from './ProjectsPage';

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderBrowse(initialEntries = ['/projects?source=bookmark']) {
  const api = {
    listProjects: vi.fn().mockResolvedValue({
      projects: ['Allocation', 'Other'].map((name) => ({
        project: { project_key: name.toUpperCase(), display_name: name, root: `/work/${name.toLowerCase()}` },
        readiness: { lifecycle: 'ready' },
        attention_count: 0,
        active_run: name === 'Allocation' ? { run_id: 'run-1', next_node: 'implement' } : null,
      })),
    }),
  };
  // These browse interactions must stay local; no real server or Provider is used.
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Unexpected network request')));
  const router = createMemoryRouter([
    { path: '/projects', element: <ProjectsPage api={api} language="en" /> },
  ], { initialEntries });
  render(<RouterProvider router={router} />);
  const search = await screen.findByRole('searchbox');
  const params = () => new URLSearchParams(router.state.location.search);
  return { router, search, params, user: userEvent.setup() };
}

test.each(['all', 'allocation'])('preserves every typed character in the search query %s', async (query) => {
  const { search, params, user } = await renderBrowse();

  for (let index = 0; index < query.length; index += 1) {
    await user.type(search, query[index]);
    expect(search).toHaveValue(query.slice(0, index + 1));
    expect(params().get('q')).toBe(query.slice(0, index + 1));
  }

  expect(params().get('source')).toBe('bookmark');
  expect(screen.getByRole('link', { name: 'Enter workspace: Allocation' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Enter workspace: Other' })).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

test.each(['empty input', 'clear button'])('%s removes only the search parameter', async (method) => {
  const { search, params, user } = await renderBrowse(['/projects?source=bookmark&q=all&filter=active']);

  if (method === 'empty input') await user.clear(search);
  else await user.click(screen.getByRole('button', { name: 'Clear search', exact: true }));

  expect(search).toHaveValue('');
  expect(params().has('q')).toBe(false);
  expect(params().get('filter')).toBe('active');
  expect(params().get('source')).toBe('bookmark');
});

test('selecting All removes the filter sentinel without removing literal all search text', async () => {
  const { search, params, user } = await renderBrowse(['/projects?source=bookmark&q=all&filter=active']);

  await user.click(within(screen.getByRole('group', { name: 'Filter results' })).getByRole('button', { name: 'All', exact: true }));

  expect(params().has('filter')).toBe(false);
  expect(params().get('q')).toBe('all');
  expect(params().get('source')).toBe('bookmark');
  expect(search).toHaveValue('all');
  expect(screen.queryByRole('link', { name: 'Enter workspace: Other' })).not.toBeInTheDocument();
});

test('clearing all browse filters preserves unrelated parameters', async () => {
  const { search, params, user } = await renderBrowse(['/projects?source=bookmark&q=missing&filter=active']);

  await user.click(screen.getByRole('button', { name: 'Clear filters', exact: true }));

  expect(search).toHaveValue('');
  expect([...params()]).toEqual([['source', 'bookmark']]);
  expect(screen.getByRole('link', { name: 'Enter workspace: Other' })).toBeVisible();
});

test('Back and Forward restore literal search queries while typing replaces only the current entry', async () => {
  const { router, search, params, user } = await renderBrowse([
    '/projects?source=bookmark&q=earlier',
    '/projects?source=bookmark',
  ]);
  await user.type(search, 'allocation');
  await user.click(within(screen.getByRole('group', { name: 'Filter results' })).getByRole('button', { name: 'Active run', exact: true }));
  await user.clear(search);
  await user.type(search, 'all');
  expect(search).toHaveValue('all');
  expect(params().get('filter')).toBe('active');

  await act(async () => router.navigate(-1));
  expect(search).toHaveValue('allocation');
  expect(params().has('filter')).toBe(false);
  await act(async () => router.navigate(-1));
  expect(search).toHaveValue('earlier');
  await act(async () => router.navigate(1));
  expect(search).toHaveValue('allocation');
  await act(async () => router.navigate(1));
  expect(search).toHaveValue('all');
  expect(params().get('q')).toBe('all');
  expect(params().get('filter')).toBe('active');
  expect(params().get('source')).toBe('bookmark');
});
