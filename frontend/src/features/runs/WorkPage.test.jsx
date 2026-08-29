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
  decideReview: vi.fn(),
  dispatchTrack: vi.fn(),
  getDevLoop: vi.fn(),
  getMessages: vi.fn(),
  getProjectRuns: vi.fn(),
  getProjectState: vi.fn(),
  startSmoke: vi.fn(),
  retryRun: vi.fn(),
  terminateRun: vi.fn(),
  useProjectState: vi.fn(),
}));

vi.mock('./useProjectState', () => ({ default: mocks.useProjectState }));
vi.mock('../reviews/reviewApi', () => ({
  reviewApi: { decideReview: mocks.decideReview },
}));
vi.mock('./runApi', () => ({
  getProjectState: mocks.getProjectState,
  runApi: {
    dispatchTrack: mocks.dispatchTrack,
    getDevLoop: mocks.getDevLoop,
    getMessages: mocks.getMessages,
    getProjectRuns: mocks.getProjectRuns,
    getProjectState: mocks.getProjectState,
    retryRun: mocks.retryRun,
    startSmoke: mocks.startSmoke,
    terminateRun: mocks.terminateRun,
  },
}));

function TranslationOutlet() {
  return <Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />;
}

function renderWorkPage(initialEntry = '/projects/DEMO/work/runs/run-active') {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
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

function projectedWorkData(overrides = {}) {
  return {
    active_run: { run_id: 'run-active' },
    agile_board: { columns: {}, counts: {} },
    approval_reviews: [],
    start_context: { project_name: 'Demo project', worker_backend: { mode: 'codex' } },
    state: { run_id: 'run-active', idea: 'Make OAuth production ready' },
    workflow_steps: [],
    worker_jobs: [],
    team_workspace: {
      schema_version: 'team_workspace.v1',
      mission: {
        id: 'run-active', run_id: 'run-active', source: 'workflow', title: 'Make OAuth production ready',
        objective: 'Make OAuth production ready', status: 'working',
      },
      manager_timeline: [
        { id: 'intent', actor: 'user', kind: 'intent', event: 'mission_requested', timestamp: '2026-08-30T10:00:00Z', content: 'Make OAuth production ready', details: {} },
        { id: 'manager', actor: 'manager', kind: 'progress', event: 'mission_organized', timestamp: '2026-08-30T10:00:01Z', content: '', details: { task_count: 2 } },
      ],
      attention_request: null,
      execution_actors: [
        { id: 'job-1', role: 'implementation', state: 'working', responsibility: 'Implement OAuth', task_id: 'DEMO-1', source: 'worker_job' },
        { id: 'role-reviewer', role: 'reviewer', state: 'waiting', responsibility: 'Review OAuth', task_id: 'DEMO-2', source: 'task_role', dependency_reason: 'waiting_for_dependencies' },
      ],
      outcome_tasks: [
        { id: 'DEMO-1', objective: 'Implement OAuth', owner: 'implementation', status: 'working', status_reason: 'worker_running', dependencies: [], acceptance_criteria: ['CI passes'], attempt_count: 1, primary_outcome: { kind: 'pull_request', label: 'PR #42', href: 'https://github.com/example/demo/pull/42', path: '', status: 'ready' }, supporting_refs: [] },
        { id: 'DEMO-2', objective: 'Review OAuth', owner: 'reviewer', status: 'waiting', status_reason: 'waiting_for_dependencies', dependencies: ['DEMO-1'], acceptance_criteria: [], attempt_count: 0, supporting_refs: [] },
      ],
      capabilities: [{ action: 'cancel_mission', target_id: 'run-active' }],
    },
    ...overrides,
  };
}

test('renders the Manager-centered three-pane workspace from the durable projection', async () => {
  mocks.useProjectState.mockReturnValue({
    data: projectedWorkData(), error: null, refresh: vi.fn(), status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });

  renderWorkPage();

  expect(screen.getByRole('region', { name: 'Manager' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Manager' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Execution' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Outcomes' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Execution' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Outcomes' })).toBeVisible();
  expect(screen.getByText('I organized this mission into 2 outcome tasks and identified the next eligible work.')).toBeVisible();
  expect(screen.getAllByText('Working', { selector: '.product-status-badge' }).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText('Waiting for a dependency to complete.').length).toBe(2);
  expect(screen.getByRole('link', { name: 'Open PR #42 ↗' })).toHaveAttribute('href', 'https://github.com/example/demo/pull/42');
  expect(screen.queryByRole('navigation', { name: 'Run views' })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /Implement OAuth/ }));
  expect(await screen.findByRole('dialog', { name: 'Implement OAuth' })).toBeVisible();
});

test('falls back to the legacy run views for an unsupported workspace contract version', () => {
  const data = projectedWorkData();
  data.team_workspace.schema_version = 'team_workspace.v2';
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });

  renderWorkPage();

  expect(screen.getByRole('navigation', { name: 'Run views' })).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Manager' })).not.toBeInTheDocument();
});

test('keeps approval and change-request decisions inside the Manager pane', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const data = projectedWorkData();
  data.approval_reviews = [{ approval: 'takeover', is_actionable: true }];
  data.team_workspace.attention_request = {
    id: 'approval:takeover', kind: 'approval', target_id: 'takeover', reason: 'approval_required',
    actions: ['approve', 'request_changes', 'review'], refs: ['takeover'],
  };
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.decideReview.mockResolvedValue({ decision: 'changes_requested' });

  renderWorkPage();

  expect(screen.getByText('A decision is ready')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Inspect details →' })).toHaveAttribute('href', '/projects/DEMO/review/takeover');
  await userEvent.click(screen.getByRole('button', { name: 'Request changes' }));
  await userEvent.type(screen.getByLabelText('What should change?'), 'Keep the provider boundary extensible.');
  await userEvent.click(screen.getByRole('button', { name: 'Send changes' }));

  expect(mocks.decideReview).toHaveBeenCalledWith(
    'DEMO', 'takeover', 'changes_requested', 'Keep the provider boundary extensible.',
  );
});

test('requires confirmation before the Manager cancels a mission', async () => {
  const data = projectedWorkData();
  data.team_workspace.attention_request = {
    id: 'workflow-blocked', kind: 'blocker', target_id: 'run-active', reason: 'execution_blocked',
    actions: ['retry', 'cancel_mission'], refs: [],
  };
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.terminateRun.mockResolvedValue({ terminated: true });

  renderWorkPage();

  await userEvent.click(screen.getByRole('button', { name: 'Cancel mission' }));
  expect(screen.getByText(/Cancel the whole mission/)).toBeVisible();
  expect(mocks.terminateRun).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Terminate this run' }));
  expect(mocks.terminateRun).toHaveBeenCalledWith('DEMO');
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
  expect(screen.getByRole('button', { name: 'Approve' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Inspect details →' }))
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

  const plan = await screen.findByRole('button', { name: 'Board' });
  expect(plan).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Activity' })).toHaveAttribute('aria-pressed', 'false');

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

test('activity view renders action history timeline and transitions', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      action_items: [
        { id: 'act-1', title: 'Start Workflow', action: 'start_run', node_transition: 'takeover -> product_requirement', timestamp: '2026-08-18T10:00:00Z', note: 'Run initialized by user' },
        { id: 'act-2', title: 'PRD Approved', action: 'approve', approval: 'prd', node_transition: 'product_requirement -> architect', timestamp: '2026-08-18T11:00:00Z' },
      ],
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

  render(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active?view=activity']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText('Start Workflow')).toBeVisible();
  expect(screen.getByText('takeover -> product_requirement')).toBeVisible();
  expect(screen.getByText('Run initialized by user')).toBeVisible();
  expect(screen.getByText('PRD Approved')).toBeVisible();
  expect(screen.getByRole('link', { name: /View review \(prd\)/ })).toHaveAttribute('href', '/projects/DEMO/review/prd');
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

test('handles dispatch failure by displaying error without confirming dispatch', async () => {
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
  mocks.dispatchTrack.mockRejectedValue(new Error('Dispatch failed: worker capacity exceeded'));

  renderWorkPage();

  await userEvent.click(await screen.findByRole('button', { name: 'Execution' }));
  await userEvent.click(screen.getByRole('button', { name: 'Dispatch track' }));
  await userEvent.type(screen.getByLabelText('Track name'), 'audit scan');
  await userEvent.type(screen.getByLabelText('Task and acceptance boundary'), 'scan the repo read-only');
  await userEvent.click(screen.getByRole('button', { name: 'Confirm dispatch' }));

  expect((await screen.findAllByText('Dispatch failed: worker capacity exceeded')).length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(/Dispatched audit scan/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Confirm dispatch' })).toBeVisible();
});
