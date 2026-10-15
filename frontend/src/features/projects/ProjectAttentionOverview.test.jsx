import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import ProjectsPage from './ProjectsPage';

const item = { id: 'workflow:run-1:approval:prd', kind: 'approval', source: 'workflow', run_id: 'run-1',
  target_id: 'prd', title: 'Ship search', reason: 'approval_required' };
const payload = (key = 'A', items = [item]) => ({ projects: [{ project: { project_key: key, display_name: key, root: `/work/${key}` },
  readiness: { lifecycle: 'ready' }, attention_count: 0, active_run: { run_id: 'run-1', next_node: 'await_prd_approval' },
  attention: { schema_version: 'project_attention.v1', items, counts: { blocked: 0, approval: items.length, needs_input: 0, total: items.length } } }] });
const deferred = () => { let resolve; let reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const page = (api) => <MemoryRouter><ProjectsPage api={api} language="en" /></MemoryRouter>;
const overview = () => screen.getByRole('region', { name: 'Needs your attention' });

test('refresh revokes old attention links and counts until successful revalidation, without optimistic dismissal', async () => {
  const user = userEvent.setup();
  const pending = deferred();
  const api = { listProjects: vi.fn().mockResolvedValueOnce(payload()).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(payload('A', [])) };
  render(page(api));
  await screen.findByRole('link', { name: /Ship search/ });
  await user.click(within(overview()).getByRole('button', { name: 'Refresh attention' }));
  expect(within(overview()).queryByRole('link')).not.toBeInTheDocument();
  expect(within(overview()).queryByRole('definition')).not.toBeInTheDocument();
  expect(within(overview()).getByRole('status')).toHaveTextContent('previous attention snapshot is no longer current');
  expect(screen.getByRole('button', { name: 'Enter workspace: A' })).toBeVisible();
  await act(async () => pending.reject(new Error('offline')));
  expect(within(overview()).getByRole('status')).toHaveTextContent('Attention is unknown');
  expect(within(overview()).queryByText(/No current workflow/)).not.toBeInTheDocument();
  await user.click(within(overview()).getByRole('button', { name: 'Refresh attention' }));
  expect(await within(overview()).findByText(/No current workflow/)).toBeVisible();
});

test('project search cannot filter away a cross-project attention item', async () => {
  const user = userEvent.setup();
  render(page({ listProjects: vi.fn().mockResolvedValue(payload()) }));
  await screen.findByRole('link', { name: /Ship search/ });
  await user.type(screen.getByRole('searchbox'), 'missing project');
  expect(screen.queryByRole('button', { name: 'Enter workspace: A' })).not.toBeInTheDocument();
  expect(within(overview()).getByRole('link', { name: /Ship search/ })).toBeVisible();
});

test('older servers keep launcher navigation and never become an all-clear', async () => {
  const data = payload(); delete data.projects[0].attention;
  render(page({ listProjects: vi.fn().mockResolvedValue(data) }));
  expect(await screen.findByRole('button', { name: 'Enter workspace: A' })).toBeVisible();
  expect(within(overview()).getByRole('status')).toHaveTextContent('These projects are not confirmed clear');
  expect(within(overview()).queryByText(/No current workflow/)).not.toBeInTheDocument();
});

test('a superseded server response cannot replace the current project attention snapshot', async () => {
  const old = deferred(); const fresh = deferred();
  const oldApi = { listProjects: vi.fn(() => old.promise) };
  const newApi = { listProjects: vi.fn(() => fresh.promise) };
  const { rerender } = render(page(oldApi));
  rerender(page(newApi));
  await act(async () => fresh.resolve(payload('CURRENT')));
  await act(async () => old.resolve(payload('OLD')));
  expect(within(overview()).getByRole('link', { name: /CURRENT/ })).toBeVisible();
  expect(within(overview()).queryByRole('link', { name: /OLD/ })).not.toBeInTheDocument();
  expect(oldApi.listProjects.mock.calls[0][0].signal.aborted).toBe(true);
});
