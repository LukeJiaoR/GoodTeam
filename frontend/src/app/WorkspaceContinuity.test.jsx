import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import ProductLayout from './ProductLayout';
import ProjectsPage from '../features/projects/ProjectsPage';
import { projectApi } from '../features/projects/projectApi';
import ConversationComposer from '../features/runs/workspace/ConversationComposer';
import { translate } from '../i18n';

const t = (key) => translate('en', key);
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

test('restored attachments cannot be consumed by change discussion, but remain sendable as a message', async () => {
  const file = { id: 'a'.repeat(64), name: 'notes.txt', size: 12, media_type: 'text/plain' };
  const key = 'goodteam:conversation-files:DEMO';
  sessionStorage.setItem(key, JSON.stringify([file]));
  const onStart = vi.fn().mockResolvedValue({});
  const onDiscussChangeIntent = vi.fn().mockResolvedValue({});
  const onKeepDiscussingChangeIntent = vi.fn().mockResolvedValue({});
  render(<ConversationComposer t={t} draftKey="DEMO" projectKey="DEMO" onStart={onStart}
    changeIntent={{ id: 'change', status: 'ready_to_confirm', draft_text: 'Keep the schema.' }}
    onDiscussChangeIntent={onDiscussChangeIntent} onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent} />);
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: 'Retain this discussion.' } });
  fireEvent.submit(input.closest('form'));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(t('work.attachments.messageOnly')));
  expect(onDiscussChangeIntent).not.toHaveBeenCalled();
  expect(onKeepDiscussingChangeIntent).not.toHaveBeenCalled();
  expect(input).toHaveValue('Retain this discussion.');
  expect(JSON.parse(sessionStorage.getItem(key))[0].id).toBe(file.id);
  fireEvent.click(screen.getByRole('button', { name: t('work.taskAssistant.message'), exact: true }));
  fireEvent.submit(input.closest('form'));
  await waitFor(() => expect(onStart).toHaveBeenCalledWith(t('work.attachments.message'), [expect.objectContaining(file)]));
  await waitFor(() => expect(sessionStorage.getItem(key)).toBeNull());
});

test('successful import refreshes the mounted sidebar and survives leaving and returning to the project', async () => {
  const row = key => ({ project: { project_key: key, display_name: key, root: `/work/${key}` }, readiness: { lifecycle: 'ready' }, attention_count: 0, active_run: null });
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
