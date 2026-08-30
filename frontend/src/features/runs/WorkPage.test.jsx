import { fireEvent, render, screen, within } from '@testing-library/react';
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
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom';
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
  startRun: vi.fn(),
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
    startRun: mocks.startRun,
    terminateRun: mocks.terminateRun,
  },
}));

function TranslationOutlet() {
  const location = useLocation();
  return (
    <>
      <output data-testid="location-path">{location.pathname}</output>
      <output data-testid="location-search">{location.search}</output>
      <Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />
    </>
  );
}

function renderWorkPage(initialEntry = '/projects/DEMO/work/runs/run-active') {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work" element={<WorkPage />} />
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
          <Route path="/projects/:projectKey/review/:approval" element={<div data-testid="full-review-page">Full review workbench</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function stubMediaPreferences({ mobile = false, reducedMotion = false } = {}) {
  vi.stubGlobal('matchMedia', vi.fn((query) => ({
    matches: query === '(max-width: 767px)' ? mobile : reducedMotion,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
}

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
        {
          id: 'job-1', role: 'implementation', state: 'working', responsibility: 'Implement OAuth', task_id: 'DEMO-1',
          source: 'worker_job', status_reason: 'worker_running',
        },
        {
          id: 'role-reviewer', role: 'reviewer', state: 'waiting', responsibility: 'Review OAuth', task_id: 'DEMO-2',
          source: 'task_role', dependency_reason: 'waiting_for_dependencies', status_reason: 'waiting_for_dependencies',
          dependency_task_id: 'DEMO-1', dependency_task_title: 'Implement OAuth',
        },
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

function repeatedArchitectAttemptData() {
  const data = projectedWorkData();
  data.worker_jobs = [
    { id: 'job-architect-1', role: 'architect', status: 'blocked' },
    { id: 'job-architect-2', role: 'architect', status: 'completed' },
    { id: 'job-architect-3', role: 'architect', status: 'blocked' },
    { id: 'job-architect-4', role: 'architect', status: 'completed' },
  ];
  data.team_workspace.execution_actors = [];
  data.team_workspace.outcome_tasks = [
    {
      id: 'DEMO-1',
      objective: 'Design RFC & Security Review',
      owner: 'architect',
      status: 'completed',
      status_reason: 'delivered',
      dependencies: [],
      acceptance_criteria: [],
      attempt_count: 4,
      current_attempt_id: 'job-architect-4',
      supporting_refs: [],
    },
  ];
  return data;
}

function orderedExecutionData() {
  const data = projectedWorkData();
  data.team_workspace.execution_actors = [
    { id: 'blocked', role: 'architect', state: 'blocked', responsibility: 'Resolve CI failure', status_reason: 'execution_blocked' },
    { id: 'reviewing', role: 'reviewer', state: 'reviewing', responsibility: 'Review architecture', status_reason: 'awaiting_review' },
    { id: 'working', role: 'implementation', state: 'working', responsibility: 'Implement approved design', status_reason: 'worker_running' },
    {
      id: 'waiting', role: 'qa', state: 'waiting', responsibility: 'Verify implementation',
      status_reason: 'waiting_for_dependencies', dependency_reason: 'waiting_for_dependencies',
      dependency_task_id: 'DEMO-IMPL', dependency_task_title: 'Implement approved design',
    },
    { id: 'up-next', role: 'release', state: 'up_next', responsibility: 'Prepare release', status_reason: 'not_started' },
  ];
  return data;
}

function managerReviewData() {
  const data = projectedWorkData();
  data.approval_reviews = [
    {
      approval: 'rfc',
      title: 'RFC architecture review',
      decision: 'approved',
      is_actionable: false,
      content: '# RFC v2\n\nPreviously approved architecture.',
    },
    {
      approval: 'security',
      title: 'Architecture security review',
      artifact: 'rfc',
      artifact_path: '/tmp/rfc-v3.md',
      decision: 'pending',
      is_actionable: true,
      content: '# RFC v3\n\nSecurity boundaries and evidence.',
      recommendation: { verdict: 'approve', reasons: ['The threat model is complete.'] },
      evidence_snapshot: {
        ci_status: 'passed', smoke_status: 'passed', qa_status: 'passed', blocker_count: 0, pr_number: 376,
      },
      reviewer_provenance: { source: 'independent_reviewer', review_id: 'security-review-3' },
    },
  ];
  data.team_workspace.attention_request = {
    id: 'approval:security',
    kind: 'approval',
    target_type: 'review',
    target_id: 'security',
    task_id: 'DEMO-ARCH',
    reason: 'approval_required',
    actions: ['approve', 'request_changes', 'review'],
    refs: ['security', 'DEMO-ARCH'],
  };
  data.team_workspace.outcome_tasks = [
    {
      id: 'DEMO-ARCH',
      objective: 'Architecture',
      description: 'Review RFC v3 and its security boundaries.',
      owner: 'architect',
      status: 'reviewing',
      status_reason: 'awaiting_review',
      dependencies: [],
      acceptance_criteria: ['Threat model covers the production boundary.'],
      attempt_count: 2,
      review_state: 'pending',
      review_target_type: 'review',
      review_target_id: 'security',
      primary_outcome: {
        kind: 'document', label: 'RFC v3', href: '', path: '/tmp/rfc-v3.md', status: 'ready',
      },
      supporting_refs: ['/tmp/security-evidence.json'],
    },
    {
      id: 'DEMO-IMPL',
      objective: 'Implementation',
      owner: 'implementation',
      status: 'working',
      status_reason: 'worker_running',
      dependencies: ['DEMO-ARCH'],
      acceptance_criteria: [],
      attempt_count: 1,
      review_target_type: '',
      review_target_id: '',
      supporting_refs: [],
    },
    {
      id: 'DEMO-SEC',
      objective: 'Security review',
      owner: 'architect',
      status: 'waiting',
      status_reason: 'waiting_for_dependencies',
      dependencies: ['DEMO-IMPL'],
      acceptance_criteria: [],
      attempt_count: 0,
      review_target_type: '',
      review_target_id: '',
      supporting_refs: [],
    },
  ];
  return data;
}

test('renders the Manager-centered three-pane workspace from the durable projection', async () => {
  const data = projectedWorkData();
  data.workflow_steps = [
    { number: '1', key: 'product_requirement', title: 'Product / Requirement', status: 'done', status_label: 'Done' },
    { number: '2', key: 'prd_review', title: 'PRD Review', status: 'review-needed', status_label: 'Needs review' },
    { number: '3', key: 'architecture', title: 'Architecture', status: 'not-started', status_label: 'Not started' },
  ];
  mocks.useProjectState.mockReturnValue({
    data, error: null, refresh: vi.fn(), status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });

  renderWorkPage();

  expect(screen.getByRole('region', { name: 'Manager' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Manager' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Execution' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Outcomes' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Team & tasks' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Outcomes & delivery' })).toBeVisible();
  const missionContext = document.querySelector('.work-run-context');
  expect(within(missionContext).getByText('Current mission')).toBeVisible();
  expect(within(missionContext).getByText('PRD Review')).toBeVisible();
  const reviewStatus = within(missionContext).getByText('Needs review');
  expect(reviewStatus).toBeVisible();
  expect(reviewStatus).toHaveAttribute('data-tone', 'active');
  expect(within(missionContext).getByText('1 / 3 stages complete')).toBeVisible();
  expect(screen.queryByRole('combobox', { name: 'Select run' })).not.toBeInTheDocument();
  expect(screen.getByText('I organized this mission into 2 outcome tasks and identified the next eligible work.')).toBeVisible();
  const execution = screen.getByRole('region', { name: 'Execution' });
  expect(execution.querySelector('.team-actor-list .product-status-badge')).not.toBeInTheDocument();
  expect(execution.querySelectorAll('.team-status-led')).toHaveLength(2);
  expect(within(execution).getByRole('img', { name: 'Working. A worker is executing this task now.' })).toBeVisible();
  expect(within(execution).getByRole('img', { name: 'Waiting. Waiting for “Implement OAuth” to complete.' })).toBeVisible();
  expect(within(execution).getAllByText('Current task')).toHaveLength(2);
  expect(within(execution).getByText('1 acceptance criteria')).toBeVisible();
  expect(within(execution).getByText('Outcome: PR #42')).toBeVisible();
  expect(document.querySelector('[data-task-id="DEMO-2"] .product-status-badge'))
    .toHaveAttribute('data-tone', 'neutral');
  expect(screen.getAllByText('Waiting for a dependency to complete.')).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'Open PR #42 ↗' })).toHaveAttribute('href', 'https://github.com/example/demo/pull/42');
  expect(screen.queryByRole('navigation', { name: 'Run views' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run details' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'Mission history' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Open the Manager conversation…' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Timeline' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Inspect workflow' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Inspect workers' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Back to Home' })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /Implement OAuth/ }));
  expect(await screen.findByRole('dialog', { name: 'Implement OAuth' })).toBeVisible();
});

test('keeps terminal Architect attempts out of Execution and in Outcomes/history', async () => {
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  });
  mocks.useProjectState.mockReturnValue({
    data: repeatedArchitectAttemptData(), error: null, refresh: vi.fn(), status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });

  renderWorkPage();

  const executionPane = screen.getByRole('region', { name: 'Execution' });
  expect(within(executionPane).queryAllByRole('listitem')).toHaveLength(0);
  expect(within(executionPane).getByText('No active workers yet.')).toBeVisible();
  const outcomesPane = screen.getByRole('region', { name: 'Outcomes' });
  expect(within(outcomesPane).getByRole('button', {
    name: /Design RFC & Security Review.*Completed/,
  })).toBeVisible();

  const runDetails = screen.getByRole('button', { name: 'Run details' });
  await userEvent.click(runDetails);
  const inspector = document.querySelector('.team-inspector');
  expect(inspector).not.toBeNull();
  expect(runDetails).toHaveAttribute('aria-expanded', 'true');
  expect(inspector).toHaveFocus();
  expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
  const detailTabs = within(inspector).getByRole('navigation', { name: 'Run detail views' });
  expect(within(detailTabs).getAllByRole('button').map((button) => button.textContent)).toEqual([
    'Board', 'Activity', 'Execution', 'Messages', 'Delivery',
  ]);
  await userEvent.click(within(detailTabs).getByRole('button', { name: 'Execution' }));
  expect(screen.getByTestId('location-search')).toHaveTextContent('?view=execution');
  for (const jobId of ['job-architect-1', 'job-architect-2', 'job-architect-3', 'job-architect-4']) {
    expect(within(inspector).getByText(jobId)).toBeVisible();
  }
});

test('localizes up_next as an eligible assignment rather than a generic next step', () => {
  expect(translate('en', 'work.team.status.up_next')).toBe('Up next');
  expect(translate('zh-CN', 'work.team.status.up_next')).toBe('待执行');
  expect(translate('zh-CN', 'work.team.status.up_next')).not.toBe('下一步');
});

test('renders the projection order stably with LED-only Execution states', async () => {
  mocks.useProjectState.mockReturnValue({
    data: orderedExecutionData(), error: null, refresh: vi.fn(), status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });
  const user = userEvent.setup();

  renderWorkPage();

  const execution = screen.getByRole('region', { name: 'Execution' });
  const actorOrder = () => within(execution).getAllByRole('listitem').map((item) => (
    item.querySelector('.team-actor-list__copy p')?.textContent
  ));
  const expected = [
    'Resolve CI failure',
    'Review architecture',
    'Implement approved design',
    'Verify implementation',
    'Prepare release',
  ];
  expect(actorOrder()).toEqual(expected);
  expect(execution.querySelectorAll('.team-status-led')).toHaveLength(5);
  expect(execution.querySelector('.team-actor-list .product-status-badge')).not.toBeInTheDocument();

  await user.hover(within(execution).getByRole('img', { name: /Reviewing/ }));
  expect(actorOrder()).toEqual(expected);
});

test('exposes the same localized LED explanation on hover and keyboard focus', async () => {
  mocks.useProjectState.mockReturnValue({
    data: orderedExecutionData(), error: null, refresh: vi.fn(), status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });
  const user = userEvent.setup();

  renderWorkPage();

  const execution = screen.getByRole('region', { name: 'Execution' });
  const indicator = within(execution).getByRole('img', {
    name: 'Waiting. Waiting for “Implement approved design” to complete.',
  });
  expect(indicator).toHaveAttribute('tabindex', '0');
  expect(within(execution).queryByRole('tooltip')).not.toBeInTheDocument();

  await user.hover(indicator);
  let tooltip = within(execution).getByRole('tooltip');
  expect(tooltip).toHaveTextContent('Waiting');
  expect(tooltip).toHaveTextContent('Waiting for “Implement approved design” to complete.');
  expect(indicator).toHaveAttribute('aria-describedby', tooltip.id);

  await user.unhover(indicator);
  expect(within(execution).queryByRole('tooltip')).not.toBeInTheDocument();
  fireEvent.focus(indicator);
  tooltip = within(execution).getByRole('tooltip');
  expect(tooltip).toHaveTextContent('Waiting for “Implement approved design” to complete.');
  fireEvent.blur(indicator);
  expect(within(execution).queryByRole('tooltip')).not.toBeInTheDocument();
});

test('keeps active Execution LEDs static when reduced motion is preferred', () => {
  stubMediaPreferences({ reducedMotion: true });
  mocks.useProjectState.mockReturnValue({
    data: orderedExecutionData(), error: null, refresh: vi.fn(), status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });

  renderWorkPage();

  const execution = screen.getByRole('region', { name: 'Execution' });
  for (const state of ['working', 'reviewing']) {
    expect(execution.querySelector(`.team-status-led-trigger[data-state="${state}"]`))
      .toHaveAttribute('data-motion', 'static');
  }
});

test('starts empty Work from the Manager inside the three-pane workspace', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: null,
      agile_board: { columns: {}, counts: {} },
      approval_reviews: [],
      start_context: {
        delivery_mode: 'init_project',
        model_profile: 'balanced',
        project_config: '/workspace/projects/demo.local.yaml',
        project_name: 'Demo project',
      },
      state: null,
      team_workspace: {
        schema_version: 'team_workspace.v1',
        mission: null,
        manager_timeline: [],
        attention_request: null,
        execution_actors: [],
        outcome_tasks: [],
        capabilities: [],
      },
      workflow_steps: [],
      worker_jobs: [],
    },
    error: null,
    refresh,
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({ runs: [] });
  mocks.startRun.mockResolvedValue({ run_id: 'run-new' });

  renderWorkPage('/projects/DEMO/work');

  expect(screen.getByRole('region', { name: 'Execution' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Manager' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Outcomes' })).toBeVisible();
  expect(screen.getByText('No active workers yet.')).toBeVisible();
  expect(screen.getByText('No tasks yet.')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'What should the team work on?' })).toBeVisible();
  expect(document.querySelector('.work-start-panel')).not.toBeInTheDocument();

  await userEvent.type(screen.getByLabelText('Mission'), 'Ship the Manager-centered empty state.');
  await userEvent.click(screen.getByRole('button', { name: 'Send to Manager' }));

  expect(mocks.startRun).toHaveBeenCalledWith({
    idea: 'Ship the Manager-centered empty state.',
    project_key: 'DEMO',
    model_profile: 'balanced',
    delivery_mode: 'init_project',
    source_type: 'existing_project',
    project_config: '/workspace/projects/demo.local.yaml',
    dry_run: false,
  });
});

test('keeps an active Operator mission on Work when workflow history has a stale run id', async () => {
  const data = projectedWorkData({
    active_run: null,
    state: { run_id: 'run-archived', idea: 'Old workflow mission' },
    team_workspace: {
      schema_version: 'team_workspace.v1',
      mission: {
        id: 'operator-task-375',
        run_id: '',
        execution_id: 'exec-375',
        source: 'operator_queue',
        title: 'Current Operator mission',
        objective: 'Keep the current Operator mission visible through review.',
        status: 'reviewing',
        phase: 'review_ready',
      },
      manager_timeline: [],
      attention_request: null,
      execution_actors: [
        { id: 'operator-review', role: 'qa', state: 'reviewing', responsibility: 'Current Operator mission' },
      ],
      outcome_tasks: [
        {
          id: 'task-375', objective: 'Current Operator mission', owner: 'implementation',
          status: 'reviewing', status_reason: 'operator_review_ready', supporting_refs: [],
        },
      ],
      capabilities: [],
    },
  });
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-archived', idea: 'Old workflow mission', status: 'archived' }],
  });

  renderWorkPage('/projects/DEMO/work');

  expect(await screen.findByRole('region', { name: 'Manager' })).toBeVisible();
  expect(screen.getByText('Keep the current Operator mission visible through review.')).toBeVisible();
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work');
  const selector = await screen.findByRole('combobox', { name: 'Select run' });
  expect(Array.from(selector.options, (option) => option.textContent)).toEqual([
    'Current · Current Operator mission',
    'Archived · Old workflow mission',
  ]);
  expect(screen.queryByRole('heading', { name: 'Run history' })).not.toBeInTheDocument();
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

test('keeps Manager attention in the conversation after recorded mission history', async () => {
  const data = managerReviewData();
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });

  renderWorkPage();

  const manager = screen.getByRole('region', { name: 'Manager' });
  expect(within(manager).getByText('Needs you', { selector: '.product-status-badge' }))
    .toHaveAttribute('data-tone', 'active');
  const attention = manager.querySelector('.team-attention');
  const history = manager.querySelector('.team-manager-timeline');
  expect(attention).toBeInTheDocument();
  expect(history).toBeInTheDocument();
  expect(history.compareDocumentPosition(attention) & Node.DOCUMENT_POSITION_FOLLOWING)
    .toBeTruthy();
});

test('opens the exact attention Outcome in-place and restores Manager focus on close', async () => {
  const data = managerReviewData();
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  });
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const user = userEvent.setup();

  renderWorkPage('/projects/DEMO/work/runs/run-active?context=manager');

  const manager = screen.getByRole('region', { name: 'Manager' });
  const reviewButton = within(manager).getByRole('button', { name: 'Inspect details →' });
  const architecture = document.querySelector('[data-task-id="DEMO-ARCH"]');
  const implementation = document.querySelector('[data-task-id="DEMO-IMPL"]');
  const security = document.querySelector('[data-task-id="DEMO-SEC"]');
  expect(architecture).toHaveAttribute('data-attention', 'true');
  expect(within(architecture).getByText('Needs review')).toBeVisible();
  expect(implementation).not.toHaveAttribute('data-attention');
  expect(security).not.toHaveAttribute('data-attention');

  await user.click(reviewButton);

  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work/runs/run-active');
  expect(screen.getByTestId('location-search')).toHaveTextContent('?context=manager');
  expect(screen.queryByTestId('full-review-page')).not.toBeInTheDocument();
  expect(architecture).toHaveAttribute('data-active-review', 'true');
  expect(implementation).not.toHaveAttribute('data-active-review');
  expect(security).not.toHaveAttribute('data-active-review');
  expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  const dialog = await screen.findByRole('dialog', { name: 'Architecture' });
  expect(within(dialog).getByText('Architecture security review')).toBeVisible();
  expect(within(dialog).getByText('Pending')).toBeVisible();
  expect(within(dialog).queryByText('work.plan.status.pending')).not.toBeInTheDocument();
  expect(within(dialog).getByText('CI: Passed')).toBeVisible();
  expect(within(dialog).queryByText('Evidence snapshot')).not.toBeInTheDocument();
  expect(within(dialog).queryByText('Reviewer provenance')).not.toBeInTheDocument();
  expect(within(dialog).queryByText('independent_reviewer')).not.toBeInTheDocument();
  expect(within(dialog).queryByText('security-review-3')).not.toBeInTheDocument();

  await user.click(within(dialog).getAllByRole('button', { name: 'Close' })[0]);

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const restoredManager = screen.getByRole('region', { name: 'Manager' });
  expect(restoredManager).toBeVisible();
  expect(within(restoredManager).getByText('Make OAuth production ready')).toBeVisible();
  expect(architecture).toHaveAttribute('data-active-review', 'true');
  expect(reviewButton).toHaveFocus();
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work/runs/run-active');
  expect(screen.getByTestId('location-search')).toHaveTextContent('?context=manager');
});

test('uses only the modal Full Review action for dedicated review navigation', async () => {
  const data = managerReviewData();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  });
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const user = userEvent.setup();

  renderWorkPage();
  await user.click(screen.getByRole('button', { name: 'Inspect details →' }));
  const dialog = await screen.findByRole('dialog', { name: 'Architecture' });
  await user.click(within(dialog).getByRole('link', { name: /Full Review View/ }));

  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/review/security');
  expect(screen.getByTestId('full-review-page')).toBeVisible();
});

test('does not lend an actionable review to another same-role Outcome', async () => {
  const data = managerReviewData();
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const user = userEvent.setup();

  renderWorkPage();
  const outcomes = screen.getByRole('region', { name: 'Outcomes' });
  await user.click(within(outcomes).getByRole('button', { name: /Security review.*DEMO-SEC/ }));

  const dialog = await screen.findByRole('dialog', { name: 'Security review' });
  expect(within(dialog).queryByText('Architecture security review')).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Approval Decision' })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument();
  const waitingBadge = within(dialog).getByText('Waiting', { selector: '.product-status-badge' });
  expect(waitingBadge).toHaveAttribute('data-tone', 'pending');
  expect(within(dialog).queryByText('work.plan.status.waiting')).not.toBeInTheDocument();
});

test('uses a Manager-default mobile tab workspace with keyboard navigation and mounted pane state', async () => {
  const data = managerReviewData();
  stubMediaPreferences({ mobile: true });
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const user = userEvent.setup();

  renderWorkPage('/projects/DEMO/work/runs/run-active?mobile=tabs');

  const tablist = screen.getByRole('tablist', { name: 'Team panes' });
  const tabs = within(tablist).getAllByRole('tab');
  expect(tabs.map((tab) => tab.id)).toEqual([
    'team-tab-execution',
    'team-tab-manager',
    'team-tab-outcomes',
  ]);
  const [executionTab, managerTab, outcomesTab] = tabs;
  const managerPanel = document.getElementById('team-panel-manager');
  const executionPanel = document.getElementById('team-panel-execution');
  const outcomesPanel = document.getElementById('team-panel-outcomes');
  expect(managerTab).toHaveAttribute('aria-selected', 'true');
  expect(managerTab).toHaveAttribute('tabindex', '0');
  expect(managerTab).toHaveAccessibleName('Manager. Needs you');
  expect(managerTab.querySelector('.team-mobile-nav__attention')).toBeInTheDocument();
  expect(executionTab).toHaveAttribute('aria-selected', 'false');
  expect(outcomesTab).toHaveAttribute('aria-selected', 'false');
  expect(managerPanel).toBeVisible();
  expect(managerPanel).toHaveAttribute('role', 'tabpanel');
  expect(executionPanel).not.toBeVisible();
  expect(outcomesPanel).not.toBeVisible();
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);

  await user.click(within(managerPanel).getByRole('button', { name: 'Request changes' }));
  const draft = within(managerPanel).getByLabelText('What should change?');
  await user.type(draft, 'Keep this draft while switching panes.');

  managerTab.focus();
  await user.keyboard('{ArrowLeft}');
  expect(executionTab).toHaveFocus();
  expect(executionTab).toHaveAttribute('aria-selected', 'true');
  expect(executionPanel).toBeVisible();
  expect(managerPanel).not.toBeVisible();

  await user.keyboard('{ArrowRight}');
  expect(managerTab).toHaveFocus();
  expect(managerTab).toHaveAttribute('aria-selected', 'true');
  expect(managerPanel).toBeVisible();
  expect(within(managerPanel).getByLabelText('What should change?'))
    .toHaveValue('Keep this draft while switching panes.');

  await user.keyboard('{ArrowRight}');
  expect(outcomesTab).toHaveFocus();
  expect(outcomesPanel).toBeVisible();
  expect(managerPanel).not.toBeVisible();
});

test('opens exact Manager review from the mobile Manager tab and retains its Outcome target', async () => {
  const data = managerReviewData();
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  });
  stubMediaPreferences({ mobile: true, reducedMotion: true });
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const user = userEvent.setup();

  renderWorkPage('/projects/DEMO/work/runs/run-active?mobile=manager-first');
  const tablist = screen.getByRole('tablist', { name: 'Team panes' });
  const managerTab = within(tablist).getByRole('tab', { name: /Manager/ });
  const outcomesTab = within(tablist).getByRole('tab', { name: 'Outcomes' });
  const managerPanel = document.getElementById('team-panel-manager');
  const outcomesPanel = document.getElementById('team-panel-outcomes');
  const reviewButton = within(managerPanel).getByRole('button', { name: 'Inspect details →' });

  await user.click(reviewButton);

  const dialog = await screen.findByRole('dialog', { name: 'Architecture' });
  expect(dialog).toBeVisible();
  expect(within(dialog).getByText('Architecture security review')).toBeVisible();
  expect(managerTab).toHaveAttribute('aria-selected', 'true');
  expect(managerPanel).toBeVisible();
  expect(outcomesPanel).not.toBeVisible();
  expect(document.querySelector('[data-task-id="DEMO-ARCH"]')).toHaveAttribute('data-active-review', 'true');
  expect(document.querySelector('[data-task-id="DEMO-SEC"]')).not.toHaveAttribute('data-active-review');
  expect(scrollIntoView).not.toHaveBeenCalled();
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work/runs/run-active');

  await user.click(within(dialog).getAllByRole('button', { name: 'Close' })[0]);

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(managerTab).toHaveAttribute('aria-selected', 'true');
  expect(managerPanel).toBeVisible();
  expect(reviewButton).toHaveFocus();
  expect(document.querySelector('[data-task-id="DEMO-ARCH"]')).toHaveAttribute('data-active-review', 'true');

  await user.click(outcomesTab);

  expect(outcomesTab).toHaveAttribute('aria-selected', 'true');
  expect(outcomesPanel).toBeVisible();
  expect(managerPanel).not.toBeVisible();
  expect(document.querySelector('[data-task-id="DEMO-ARCH"]')).toHaveAttribute('data-active-review', 'true');
  expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
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
