import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Link, MemoryRouter, Outlet, Route, Routes, useNavigate } from 'react-router-dom';
import KnowledgePage from './KnowledgePage';
import { knowledgeApi } from './knowledgeApi';

function pending() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function Layout() {
  const navigate = useNavigate();
  return <><Link to="/projects/SECOND/knowledge?view=overview">Switch project</Link>
    <button onClick={() => navigate(-1)}>Browser back</button><button onClick={() => navigate(1)}>Browser forward</button>
    <Outlet context={{ language: 'en', t: key => key }} /></>;
}

function open() {
  return render(<MemoryRouter initialEntries={['/projects/FIRST/knowledge?view=overview']}>
    <Routes><Route element={<Layout />}><Route path="/projects/:projectKey/knowledge" element={<KnowledgePage />} /></Route></Routes>
  </MemoryRouter>);
}

const firstHome = {
  facts: { confirmed_commands: ['FIRST confirmed command'], constraints: ['FIRST confirmed constraint'] },
  readiness: { artifacts: { repository_overview: { constraints: ['FIRST repository constraint'] } } },
};

beforeEach(() => {
  vi.spyOn(knowledgeApi, 'getKnowledgeState').mockImplementation(async project => ({ state: { artifacts: {} }, start_context: { project_name: `${project} project` } }));
  vi.spyOn(knowledgeApi, 'getKnowledgeHome').mockResolvedValue(firstHome);
  vi.spyOn(knowledgeApi, 'getRepositoryUnderstanding').mockResolvedValue({});
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('switching projects hides previous confirmed facts while the next home is pending', async () => {
  const second = pending();
  knowledgeApi.getKnowledgeHome.mockImplementation(project => project === 'FIRST' ? Promise.resolve(firstHome) : second.promise);
  open();
  await screen.findByText('FIRST confirmed command');
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText('SECOND project');
  expect(screen.queryByText('FIRST confirmed command')).not.toBeInTheDocument();
  expect(screen.queryByText('FIRST confirmed constraint')).not.toBeInTheDocument();
  expect(screen.queryByText('FIRST repository constraint')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  expect(screen.queryByText('knowledge.overview.empty')).not.toBeInTheDocument();
  await act(async () => second.resolve({ facts: { confirmed_commands: ['SECOND confirmed command'] }, readiness: {} }));
  await screen.findByText('SECOND confirmed command');
});

test('next project facts cannot be paired with the previous repository understanding', async () => {
  const secondArtifact = pending();
  knowledgeApi.getKnowledgeHome.mockImplementation(async project => project === 'FIRST' ? firstHome : {
    facts: { confirmed_commands: ['SECOND confirmed command'] },
    readiness: { steps: [{ id: 'repository_understanding', status: 'complete' }] },
  });
  knowledgeApi.getRepositoryUnderstanding.mockReturnValue(secondArtifact.promise);
  open();
  await screen.findByText('FIRST repository constraint');
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText('SECOND project');
  await waitFor(() => expect(knowledgeApi.getRepositoryUnderstanding).toHaveBeenCalledWith('SECOND', expect.anything()));
  expect(screen.queryByText('FIRST repository constraint')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  await act(async () => secondArtifact.resolve({ constraints: ['SECOND repository constraint'] }));
  await screen.findByText('SECOND repository constraint');
});

test.each(['home', 'artifact'])('a late previous-project %s response cannot replace the current overview', async kind => {
  const old = pending();
  let oldSignal;
  knowledgeApi.getKnowledgeHome.mockImplementation((project, { signal }) => {
    if (project === 'SECOND') return Promise.resolve({ facts: { confirmed_commands: ['SECOND confirmed command'] }, readiness: { artifacts: { repository_overview: { constraints: ['SECOND repository constraint'] } } } });
    if (kind === 'home') { oldSignal = signal; return old.promise; }
    return Promise.resolve({ facts: firstHome.facts, readiness: { steps: [{ id: 'repository_understanding', status: 'complete' }] } });
  });
  knowledgeApi.getRepositoryUnderstanding.mockImplementation((_project, { signal }) => { oldSignal = signal; return old.promise; });
  open();
  await waitFor(() => expect(oldSignal).toBeDefined());
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText('SECOND repository constraint');
  expect(oldSignal.aborted).toBe(true);
  await act(async () => old.resolve(kind === 'home' ? firstHome : firstHome.readiness.artifacts.repository_overview));
  expect(screen.getByText('SECOND confirmed command')).toBeVisible();
  expect(screen.getByText('SECOND repository constraint')).toBeVisible();
  expect(screen.queryByText('FIRST confirmed command')).not.toBeInTheDocument();
  expect(screen.queryByText('FIRST repository constraint')).not.toBeInTheDocument();
});

test('a previous-project error is hidden while the current project overview loads', async () => {
  const second = pending();
  knowledgeApi.getKnowledgeHome.mockRejectedValueOnce(new Error('FIRST overview failed')).mockReturnValueOnce(second.promise);
  open();
  await screen.findByText('FIRST overview failed');
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText('SECOND project');
  expect(screen.queryByText('FIRST overview failed')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  await act(async () => second.resolve({ facts: { confirmed_commands: ['SECOND confirmed command'] }, readiness: {} }));
  await screen.findByText('SECOND confirmed command');
});

test('retry has a fresh loading state and restores the same project overview', async () => {
  const retry = pending();
  knowledgeApi.getKnowledgeHome.mockRejectedValueOnce(new Error('Temporary overview failure')).mockReturnValueOnce(retry.promise);
  open();
  await screen.findByText('Temporary overview failure');
  fireEvent.click(screen.getByRole('button', { name: 'product.actions.retry' }));
  expect(screen.queryByText('Temporary overview failure')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  expect(screen.queryByText('knowledge.overview.empty')).not.toBeInTheDocument();
  await act(async () => retry.resolve(firstHome));
  await screen.findByText('FIRST confirmed command');
  expect(screen.getByText('FIRST repository constraint')).toBeVisible();
  expect(knowledgeApi.getKnowledgeHome).toHaveBeenCalledTimes(2);
});

test('back and forward cannot revive a previous project snapshot or an interrupted response', async () => {
  const back = pending();
  const secondHome = { facts: { confirmed_commands: ['SECOND confirmed command'] }, readiness: {} };
  knowledgeApi.getKnowledgeHome.mockResolvedValueOnce(firstHome).mockResolvedValueOnce(secondHome).mockReturnValueOnce(back.promise).mockResolvedValueOnce(secondHome);
  open();
  await screen.findByText('FIRST confirmed command');
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText('SECOND confirmed command');
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }));
  await screen.findByText('FIRST project');
  expect(screen.queryByText('SECOND confirmed command')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Browser forward' }));
  await screen.findByText('SECOND confirmed command');
  await act(async () => back.resolve(firstHome));
  expect(screen.getByText('SECOND confirmed command')).toBeVisible();
  expect(screen.queryByText('FIRST confirmed command')).not.toBeInTheDocument();
});

test.each(['success', 'error'])('returning before the intervening project loads cannot reuse an earlier %s', async kind => {
  const second = pending();
  const returning = pending();
  if (kind === 'success') knowledgeApi.getKnowledgeHome.mockResolvedValueOnce(firstHome);
  else knowledgeApi.getKnowledgeHome.mockRejectedValueOnce(new Error('Earlier FIRST failure'));
  knowledgeApi.getKnowledgeHome.mockReturnValueOnce(second.promise).mockReturnValueOnce(returning.promise);
  open();
  await screen.findByText(kind === 'success' ? 'FIRST confirmed command' : 'Earlier FIRST failure');
  const oldSignal = knowledgeApi.getKnowledgeHome.mock.calls[0][1].signal;
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText('SECOND project');
  expect(oldSignal.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }));
  await screen.findByText('FIRST project');
  expect(screen.queryByText('FIRST confirmed command')).not.toBeInTheDocument();
  expect(screen.queryByText('FIRST repository constraint')).not.toBeInTheDocument();
  expect(screen.queryByText('Earlier FIRST failure')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  await act(async () => second.resolve({ facts: { confirmed_commands: ['Late SECOND command'] }, readiness: {} }));
  expect(screen.queryByText('Late SECOND command')).not.toBeInTheDocument();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  await act(async () => returning.resolve({ facts: { confirmed_commands: ['Fresh FIRST command'] }, readiness: {} }));
  await screen.findByText('Fresh FIRST command');
  expect(screen.queryByText('FIRST confirmed command')).not.toBeInTheDocument();
  expect(knowledgeApi.getKnowledgeHome).toHaveBeenCalledTimes(3);
});
