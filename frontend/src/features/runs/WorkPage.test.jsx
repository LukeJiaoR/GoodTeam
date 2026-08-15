import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@xyflow/react', async () => {
  const React = await import('react');
  return {
    ReactFlow: ({ children, nodes, onNodeClick }) => (
      <div data-testid="reactflow">
        {nodes.map((node) => (
          <button type="button" key={node.id} data-node={node.id} data-status={node.data.step.status} onClick={() => onNodeClick?.(null, node)}>
            {node.data.title}
          </button>
        ))}
        {children}
      </div>
    ),
    Background: () => null,
    Panel: ({ children }) => <div data-testid="canvas-panel">{children}</div>,
    ReactFlowProvider: ({ children }) => <div>{children}</div>,
    useNodesState: (initial) => {
      const state = React.useState(initial);
      return [...state, () => {}];
    },
    useReactFlow: () => ({ fitView: () => {}, zoomIn: () => {}, zoomOut: () => {} }),
    useOnViewportChange: () => {},
  };
});
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import WorkPage from './WorkPage';

const mocks = vi.hoisted(() => ({
  dispatchTrack: vi.fn(),
  getDevLoop: vi.fn(),
  getMessages: vi.fn(),
  getProjectRuns: vi.fn(),
  getProjectState: vi.fn(),
  startSmoke: vi.fn(),
  useProjectState: vi.fn(),
}));

vi.mock('./useProjectState', () => ({ default: mocks.useProjectState }));
vi.mock('./runApi', () => ({
  getProjectState: mocks.getProjectState,
  runApi: {
    dispatchTrack: mocks.dispatchTrack,
    getDevLoop: mocks.getDevLoop,
    getMessages: mocks.getMessages,
    getProjectRuns: mocks.getProjectRuns,
    getProjectState: mocks.getProjectState,
    startSmoke: mocks.startSmoke,
  },
}));

function TranslationOutlet() {
  return <Outlet context={{ t: (key, params) => translate('en', key, params) }} />;
}

function renderWorkPage() {
  render(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

test('labels the active run and run selector with task titles instead of raw IDs', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [
      { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' },
      { run_id: 'run-archived', idea: 'Review the previous delivery', status: 'archived' },
    ],
  });

  renderWorkPage();

  expect(screen.getByText('Prepare a human-readable takeover brief', { selector: '.work-run-title' }))
    .toBeVisible();
  const selector = await screen.findByRole('combobox', { name: 'Select run' });
  expect(Array.from(selector.options, (option) => option.textContent)).toEqual([
    'Active · Prepare a human-readable takeover brief',
    'Archived · Review the previous delivery',
  ]);
  expect(screen.queryByText('run-active')).not.toBeInTheDocument();
});

test('makes an actionable takeover review the primary next step', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: { blocked: 0 } },
      approval_reviews: [{ approval: 'takeover', artifact: 'takeover', is_actionable: true }],
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [{ status: 'review-needed', title: 'Takeover Context' }],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });

  renderWorkPage();

  expect(screen.getByText('Review the takeover brief', { selector: 'h2' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Review takeover brief' }))
    .toHaveAttribute('href', '/projects/DEMO/review/takeover');
  expect(screen.getByText('Stop or abandon this run')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Terminate this run' })).not.toBeVisible();
});

test('renders the workflow canvas above the views and opens the gate detail card', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [
        { key: 'prd', number: '1', title: 'Product / Requirement', status: 'review-needed', approval: 'prd', status_label: 'Needs review' },
        { key: 'rfc', number: '2', title: 'Architecture', status: 'not-started', approval: 'rfc', status_label: 'Not started' },
      ],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });

  renderWorkPage();

  expect(await screen.findByRole('region', { name: 'Workflow canvas' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: /Product \/ Requirement/ }));
  expect(await screen.findByRole('dialog', { name: 'Stage details' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Open full review' }))
    .toHaveAttribute('href', '/projects/DEMO/review/prd');
});

test('delivery view renders structured lists with raw payload collapsed', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      start_context: { project_name: 'Demo project' },
      resources: [{ key: 'prd', path: '/runs/x/prd.md' }],
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });
  mocks.getDevLoop.mockResolvedValue({
    pull_requests: [{ number: 7, title: 'feat: walkthrough', branch: 'feat/x', state: 'open', html_url: 'https://example.com/pr/7' }],
    ci: [{ name: 'backend', status: 'completed', conclusion: 'success', html_url: 'https://example.com/ci/1' }],
    evidence: [],
  });

  render(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active?view=delivery']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText('feat: walkthrough')).toBeVisible();
  expect(screen.getByText('feat/x', { selector: 'code' })).toBeVisible();
  const openLinks = screen.getAllByRole('link', { name: 'Open PR' });
  expect(openLinks.some((link) => link.getAttribute('href') === 'https://example.com/pr/7')).toBe(true);
  expect(openLinks.some((link) => link.getAttribute('href') === 'https://example.com/ci/1')).toBe(true);
  expect(screen.getByText('backend')).toBeVisible();
  expect(screen.getByText('prd', { selector: 'strong' })).toBeVisible();
  expect(screen.getAllByText('Raw payload', { selector: 'summary' }).length).toBeGreaterThanOrEqual(3);
});

test('subnav exposes the active view to assistive technology and dispatch confirms', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });
  mocks.dispatchTrack.mockResolvedValue({ ok: true });

  renderWorkPage();

  const current = await screen.findByRole('button', { name: 'Current work' });
  expect(current).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Plan' })).toHaveAttribute('aria-pressed', 'false');

  await userEvent.click(screen.getByRole('button', { name: 'Execution' }));
  await userEvent.click(screen.getByRole('button', { name: 'Dispatch track' }));
  await userEvent.type(screen.getByLabelText('Track name'), 'audit scan');
  await userEvent.type(screen.getByLabelText('Task and acceptance boundary'), 'scan the repo read-only');
  await userEvent.click(screen.getByRole('button', { name: 'Confirm dispatch' }));
  expect(await screen.findByText('Dispatched audit scan · implementation')).toBeVisible();
  expect(mocks.dispatchTrack).toHaveBeenCalledWith({
    name: 'audit scan', role: 'implementation', adapter: 'codex', task: 'scan the repo read-only', project_key: 'DEMO',
  });
});

test('messages view renders recorded messages', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });
  mocks.getMessages.mockResolvedValue({ messages: [{ id: 'm1', role: 'architect', content: 'RFC drafted' }] });

  render(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active?view=messages']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText('RFC drafted')).toBeVisible();
  expect(mocks.getMessages).toHaveBeenCalledWith('DEMO', 'run-active', '', expect.anything());
});

test('routes a legacy takeover revision to the brief recovery screen', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: { blocked: 0 } },
      approval_reviews: [{ approval: 'takeover', recovery: { available: true }, is_actionable: false }],
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [{ status: 'blocked', title: 'Takeover Context' }],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });

  renderWorkPage();

  expect(screen.getByText('Regenerate the takeover brief', { selector: 'h2' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Regenerate takeover brief' }))
    .toHaveAttribute('href', '/projects/DEMO/review/takeover');
  expect(screen.queryByRole('button', { name: 'Retry blocked work' })).not.toBeInTheDocument();
});
