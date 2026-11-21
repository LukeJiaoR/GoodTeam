import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import ProductLayout from './ProductLayout';
import ProjectsPage from '../features/projects/ProjectsPage';
import { projectApi } from '../features/projects/projectApi';
import { translate } from '../i18n';

const t = (key) => translate('en', key);
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

test('successful import refreshes the mounted sidebar and survives leaving and returning to the project', async () => {
  const row = key => ({ project: { project_key: key, display_name: key, root: `/work/${key}` }, readiness: { lifecycle: 'ready' }, attention_count: 0, has_active_batch: false });
  let projects = [row('OLD')];
  let resolveOldList;
  const oldProjects = projects;
  vi.spyOn(projectApi, 'listProjects')
    .mockImplementationOnce(() => new Promise(resolve => { resolveOldList = resolve; }))
    .mockImplementation(async () => ({ projects }));
  vi.spyOn(projectApi, 'importProject').mockImplementation(async () => {
    projects = [...projects, row('NEW')];
    return { project: projects[1].project };
  });
  const router = createMemoryRouter([{ element: <ProductLayout />, children: [
    { path: '/projects', element: <ProjectsPage language="en" /> },
    { path: '/projects/:key/work', element: <p>Project work</p> },
  ] }], { initialEntries: ['/projects'] });
  render(<RouterProvider router={router} />);
  await screen.findByRole('link', { name: 'Enter workspace: OLD' });
  fireEvent.click(screen.getAllByRole('button', { name: t('projects.launcher.add') })[0]);
  fireEvent.change(screen.getByRole('textbox', { name: t('projects.directory.label') }), { target: { value: '/work/NEW' } });
  fireEvent.click(screen.getByRole('button', { name: t('projects.load.action'), exact: true }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/projects/NEW/work'));
  await act(async () => router.navigate('/projects/OLD/work'));
  const sidebar = screen.getByRole('complementary');
  const newProject = await within(sidebar).findByText('NEW', { selector: 'summary span' });
  // A pre-import response arriving after the refreshed list must not erase NEW.
  await act(async () => resolveOldList({ projects: oldProjects }));
  expect(within(sidebar).getByText('NEW', { selector: 'summary span' })).toBeInTheDocument();
  const group = newProject.closest('details');
  group.open = true;
  fireEvent.click(within(group).getByRole('link'));
  await waitFor(() => expect(router.state.location.pathname).toBe('/projects/NEW/work'));
});
