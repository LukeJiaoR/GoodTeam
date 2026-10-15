import { useMemo } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import useProjectState from '../runs/useProjectState';
import ProjectAttentionFocus from './ProjectAttentionFocus';
import { attentionHref, readAttentionSelection } from './projectAttention';
import { projectApi } from './projectApi';

const t = (key, params) => translate('en', key, params);
const item = (key = 'A', gate = 'prd') => ({ id: `workflow:run-${key}:approval:${gate}`, kind: 'approval', source: 'workflow',
  run_id: `run-${key}`, target_id: gate, title: `Task ${key}`, reason: 'approval_required' });
const projects = (keys = ['A', 'B']) => ({ projects: keys.map(key => ({ project: { project_key: key, display_name: key, root: `/work/${key}` },
  readiness: { lifecycle: 'ready' }, attention_count: 0, active_run: { run_id: `run-${key}`, next_node: 'await_prd_approval' },
  attention: { schema_version: 'project_attention.v1', items: [item(key)], counts: { blocked: 0, approval: 1, needs_input: 0, total: 1 } } })) });
const state = (key = 'A', gate = 'prd') => ({ active_run: true, state: { project_key: key, run_id: `run-${key}` },
  team_workspace: { schema_version: 'team_workspace.v1', mission: { source: 'workflow', run_id: `run-${key}`, project_key: key },
    attention_request: { id: `approval:${gate}`, kind: 'approval', target_id: gate } } });
const deferred = () => { let resolve; const promise = new Promise(a => { resolve = a; }); return { promise, resolve }; };
let frames;
beforeEach(() => {
  frames = [];
  vi.stubGlobal('requestAnimationFrame', (callback) => { frames.push(callback); return frames.length; });
  vi.spyOn(projectApi, 'listProjects').mockResolvedValue(projects());
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const finishFrames = async () => {
  await act(async () => {});
  act(() => { const queued = frames.splice(0); queued.forEach(fn => fn()); });
};

function Harness({ api }) {
  const { projectKey, runId } = useParams();
  const { search } = useLocation();
  const navigate = useNavigate();
  const selection = useMemo(() => readAttentionSelection(new URLSearchParams(search)), [search]);
  const { data, status, refresh } = useProjectState(projectKey, { api, poll: false });
  return <main className="product-work-page" data-context-scope={`${projectKey}:${runId}`}>
    <ProjectAttentionFocus key={`${projectKey}:${runId}:${search}`} selection={selection}
      projectKey={projectKey} runId={runId} data={data} freshness={status} refresh={refresh} reducedMotion t={t} />
    {data ? <section className="team-attention" data-attention-id={data.team_workspace.attention_request.id} data-testid="target">{data.state.project_key} {data.team_workspace.attention_request.target_id}</section> : null}
    <Link to={attentionHref('B', item('B'))}>Open B</Link>
    <button onClick={() => navigate(-1)}>History back</button><button onClick={() => navigate(1)}>History forward</button>
    <button onClick={() => void refresh()}>Update current state</button>
  </main>;
}
const mount = (api, initial = attentionHref('A', item())) => render(<MemoryRouter initialEntries={[initial]}>
  <Routes><Route path="/projects/:projectKey/work/runs/:runId" element={<Harness api={api} />} /></Routes>
</MemoryRouter>);
const notice = () => screen.getByRole('region', { name: 'Attention item' });

test('real project-state hook initial read is not superseded by the attention entry', async () => {
  const api = { getProjectState: vi.fn().mockResolvedValue(state()) };
  mount(api);
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  expect(api.getProjectState).toHaveBeenCalledTimes(1);
  await finishFrames();
  expect(screen.getByTestId('target')).toHaveFocus();
  expect(projectApi.listProjects).toHaveBeenCalledTimes(1);
});

test('list-first arrival waits for current state before selecting a resource', async () => {
  const pending = deferred();
  const api = { getProjectState: vi.fn(() => pending.promise) };
  mount(api);
  await waitFor(() => expect(projectApi.listProjects).toHaveBeenCalledTimes(1));
  expect(notice()).toHaveAttribute('data-status', 'loading');
  await finishFrames();
  expect(screen.queryByTestId('target')).not.toBeInTheDocument();
  await act(async () => pending.resolve(state()));
  expect(notice()).toHaveAttribute('data-status', 'current');
  await finishFrames();
  expect(screen.getByTestId('target')).toHaveFocus();
});

test('a gate change while delayed focus is queued invalidates the original selection', async () => {
  const user = userEvent.setup();
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(state()).mockResolvedValueOnce(state('A', 'rfc')) };
  mount(api);
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  expect(frames.length).toBeGreaterThan(0);
  await user.click(screen.getByRole('button', { name: 'Update current state' }));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'changed'));
  await finishFrames();
  expect(screen.getByTestId('target')).not.toHaveFocus();
  expect(notice()).toHaveFocus();
});

test('keyboard navigation and browser back/forward restore exact project and run scope', async () => {
  const user = userEvent.setup();
  const api = { getProjectState: vi.fn(key => Promise.resolve(state(key))) };
  mount(api);
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  screen.getByRole('link', { name: 'Open B' }).focus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(screen.getByTestId('target')).toHaveTextContent('B prd'));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  await finishFrames();
  expect(screen.getByTestId('target')).toHaveFocus();
  await user.click(screen.getByRole('button', { name: 'History back' }));
  await waitFor(() => expect(screen.getByTestId('target')).toHaveTextContent('A prd'));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  await user.click(screen.getByRole('button', { name: 'History forward' }));
  await waitFor(() => expect(screen.getByTestId('target')).toHaveTextContent('B prd'));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  await finishFrames();
  expect(screen.getByTestId('target')).toHaveFocus();
});

test('superseded project-list response cannot validate another route', async () => {
  const user = userEvent.setup(); const old = deferred();
  projectApi.listProjects.mockReturnValueOnce(old.promise).mockResolvedValueOnce(projects(['B']));
  const api = { getProjectState: vi.fn(key => Promise.resolve(state(key))) };
  mount(api);
  await waitFor(() => expect(screen.getByTestId('target')).toHaveTextContent('A prd'));
  await user.click(screen.getByRole('link', { name: 'Open B' }));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  await act(async () => old.resolve(projects(['A'])));
  await finishFrames();
  expect(notice()).toHaveAttribute('data-status', 'current');
  expect(screen.getByTestId('target')).toHaveTextContent('B prd');
  expect(screen.getByTestId('target')).toHaveFocus();
});

test('manual refresh disables old selection until both read requests settle', async () => {
  const user = userEvent.setup(); const pending = deferred();
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(state()).mockReturnValueOnce(pending.promise) };
  mount(api);
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  await user.click(screen.getByRole('button', { name: 'Refresh attention' }));
  expect(notice()).toHaveAttribute('data-status', 'loading');
  await finishFrames();
  expect(screen.getByTestId('target')).not.toHaveFocus();
  await act(async () => pending.resolve(state('A', 'rfc')));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'changed'));
  await finishFrames();
  expect(screen.getByTestId('target')).not.toHaveFocus();
});

test('a superseded manual state read cannot validate against the cached ready snapshot', async () => {
  const user = userEvent.setup(); const attentionRead = deferred(); const newWorkRead = deferred();
  const api = { getProjectState: vi.fn().mockResolvedValueOnce(state())
    .mockReturnValueOnce(attentionRead.promise).mockReturnValueOnce(newWorkRead.promise) };
  mount(api);
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'current'));
  await user.click(screen.getByRole('button', { name: 'Refresh attention' }));
  await user.click(screen.getByRole('button', { name: 'Update current state' }));
  await act(async () => attentionRead.resolve(state()));
  await waitFor(() => expect(notice()).toHaveAttribute('data-status', 'unavailable'));
  await finishFrames();
  expect(screen.getByTestId('target')).not.toHaveFocus();
  await act(async () => newWorkRead.resolve(state('A', 'rfc')));
  expect(notice()).toHaveAttribute('data-status', 'unavailable');
  expect(screen.getByTestId('target')).not.toHaveFocus();
});
