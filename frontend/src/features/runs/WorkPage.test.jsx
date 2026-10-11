import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../reviews/ReviewPage', () => ({ default: ({ selectedReviewId, onClose }) => (
  <section role="dialog" aria-label="Review document"><p>{selectedReviewId}</p><button onClick={onClose}>Back to task</button></section>
) }));

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
  confirmChangeIntent: vi.fn(),
  decideReview: vi.fn(),
  discussChangeIntent: vi.fn(),
  dispatchTrack: vi.fn(),
  getDevLoop: vi.fn(),
  getMessages: vi.fn(),
  getProjectRun: vi.fn(),
  getTaskInputContext: vi.fn(),
  getProjectRuns: vi.fn(),
  getProjectState: vi.fn(),
  keepDiscussingChangeIntent: vi.fn(),
  startSmoke: vi.fn(),
  startRun: vi.fn(),
  sendTaskAssistantMessage: vi.fn(),
  taskAssistantAction: vi.fn(),
  startChangeIntent: vi.fn(),
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
    confirmChangeIntent: mocks.confirmChangeIntent,
    discussChangeIntent: mocks.discussChangeIntent,
    dispatchTrack: mocks.dispatchTrack,
    getDevLoop: mocks.getDevLoop,
    getMessages: mocks.getMessages,
    getProjectRun: mocks.getProjectRun,
    getTaskInputContext: mocks.getTaskInputContext,
    getProjectRuns: mocks.getProjectRuns,
    getProjectState: mocks.getProjectState,
    keepDiscussingChangeIntent: mocks.keepDiscussingChangeIntent,
    retryRun: mocks.retryRun,
    startSmoke: mocks.startSmoke,
    startChangeIntent: mocks.startChangeIntent,
    startRun: mocks.startRun,
    sendTaskAssistantMessage: mocks.sendTaskAssistantMessage,
    taskAssistantAction: mocks.taskAssistantAction,
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

function workPageElement(initialEntry = '/projects/DEMO/work/runs/run-active') {
  return (
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work" element={<WorkPage />} />
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
          <Route path="/projects/:projectKey/review/:approval" element={<div data-testid="full-review-page">Full review workbench</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

function renderWorkPage(initialEntry) {
  return render(workPageElement(initialEntry));
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
      artifact_version: 'sha256:1234567890abcdef1234567890abcdef',
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
    approval_brief: {
      schema_version: 'manager_approval_brief.v1',
      approval: 'security',
      approval_type: 'architecture',
      approval_target: {
        run_id: 'run-active',
        target_type: 'review',
        target_id: 'security',
        task_id: 'DEMO-ARCH',
        artifact_key: 'rfc',
        artifact_version: 'sha256:1234567890abcdef1234567890abcdef',
      },
      decision: {
        subject: 'Architecture security review',
        summary: 'Keep a generic provider boundary while shipping Google first.',
        artifact: { key: 'rfc', label: 'rfc-v3.md', version: 'sha256:1234567890abcdef1234567890abcdef' },
      },
      scope: {
        included: [
          {
            source: 'artifact_section',
            heading: 'Scope',
            items: ['Google login in this iteration.', 'Preserve the current session and user model.'],
          },
        ],
        excluded: [
          {
            source: 'artifact_section',
            heading: 'Non-Goals',
            items: ['GitHub login is not implemented yet.'],
          },
        ],
      },
      evidence: [
        { kind: 'artifact', label: 'rfc-v3.md', status: 'versioned', detail: 'sha256:1234567890abcdef1234567890abcdef' },
        { kind: 'reviewer', label: 'Security review', status: 'ok', detail: 'No blocking security issues found.' },
        { kind: 'ci', label: '', status: 'passed', detail: '' },
      ],
      mission_blockers: ['Worker capacity is unavailable for a separate delivery task.'],
      risks: [
        {
          kind: 'artifact', heading: 'Risks and trade-offs',
          text: 'The provider abstraction adds a small amount of initial complexity.',
        },
      ],
      consequence: {
        next_stage: 'planner',
        task_id: 'DEMO-IMPL',
        task_title: 'Implementation',
        recovery: [],
      },
      recommendation: {
        verdict: 'approve', confidence: 'high', blocking_count: 0,
        reasons: [{ kind: 'clean', text: 'No approval-specific blocking signals were found.' }],
      },
    },
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

function managerIntentData(status = 'discussing', draftText = '') {
  const data = managerReviewData();
  const target = data.team_workspace.attention_request.approval_brief.approval_target;
  data.team_workspace.attention_request.intent_commit = {
    schema_version: 'intent_commit.v1',
    id: 'intent-change-1',
    kind: 'request_changes',
    status,
    draft_text: draftText,
    interpretation: draftText ? {
      schema_version: 'intent_interpretation.v1',
      source: 'deterministic_explicit_request',
      summary: draftText,
      items: [{ kind: 'explicit_request', text: draftText }],
    } : null,
    target: { approval: 'security', ...target },
    stale: status === 'stale',
  };
  return data;
}

test('does not infer outcome data from a shared role when task identity is absent', async () => {
  const data = projectedWorkData();
  data.team_workspace.execution_actors = [{
    id: 'architect-live',
    role: 'architect',
    state: 'working',
    responsibility: 'Trace the current worker signal',
    task_id: 'UNKNOWN-TASK',
    status_reason: 'worker_running',
  }];
  data.team_workspace.outcome_tasks = [
    {
      id: 'DEMO-ARCH-A', objective: 'Architecture A', owner: 'architect', status: 'working',
      status_reason: 'worker_running', dependencies: [], acceptance_criteria: ['Criterion A'],
      primary_outcome: { label: 'RFC A' }, supporting_refs: [],
    },
    {
      id: 'DEMO-ARCH-B', objective: 'Architecture B', owner: 'architect', status: 'waiting',
      status_reason: 'waiting_for_dependencies', dependencies: ['DEMO-ARCH-A'],
      acceptance_criteria: ['Criterion B'], primary_outcome: { label: 'RFC B' }, supporting_refs: [],
    },
  ];
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' }],
  });

  renderWorkPage();

  const execution = screen.getByRole('region', { name: 'Execution' });
  await userEvent.click(execution.querySelector('.team-execution-disclosure > summary'));
  expect(within(execution).getByText('Trace the current worker signal')).toBeVisible();
  expect(within(execution).queryByText('Architecture A')).not.toBeInTheDocument();
  expect(within(execution).queryByText('Architecture B')).not.toBeInTheDocument();
  expect(within(execution).queryByText(/Criterion [AB]/)).not.toBeInTheDocument();
  expect(within(execution).queryByText(/RFC [AB]/)).not.toBeInTheDocument();
});

test('requires explicit confirmation of a Task Assistant proposal before starting', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: null,
      agile_board: { columns: {}, counts: {} },
      approval_reviews: [],
      start_context: { project_name: 'Demo project' },
      state: null,
      workflow_steps: [],
      worker_jobs: [],
      team_workspace: {
        schema_version: 'team_workspace.v1',
        mission: { id: 'task-assistant:DEMO', source: 'task_assistant', status: 'waiting_for_human' },
        manager_timeline: [], attention_request: null, execution_actors: [], outcome_tasks: [], capabilities: [],
        suggestions: [],
        proposed_task: { id: 'proposal-1', revision: 2, exact_request: 'Fix login and add a regression test', confirmed: false },
      },
    }, error: null, refresh, status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({ runs: [] });
  mocks.taskAssistantAction.mockResolvedValue({ workflow_started: true, run_id: 'run-1' });

  renderWorkPage('/projects/DEMO/work');
  expect(within(screen.getByRole('region', { name: 'Proposed task' })).getByText('Fix login and add a regression test')).toBeVisible();
  const context = screen.getByRole('region', { name: 'Current task and next step' });
  expect(within(context).getByText('Fix login and add a regression test')).toBeVisible();
  await userEvent.click(within(context).getByRole('button', { name: 'Review task draft' }));
  expect(mocks.taskAssistantAction).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Start this task' }));

  expect(mocks.taskAssistantAction).toHaveBeenCalledWith('DEMO', {
    action: 'confirm_task', proposal_id: 'proposal-1', expected_revision: 2,
  });
});

test('wires durable request retry through Work and refreshes failed attempts', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: null, state: null, agile_board: { columns: {}, counts: {} }, approval_reviews: [],
      start_context: { project_name: 'Demo project' }, workflow_steps: [], worker_jobs: [],
      team_workspace: {
        schema_version: 'team_workspace.v1',
        mission: { id: 'task-assistant:DEMO', source: 'task_assistant', status: 'ready' },
        manager_timeline: [], execution_actors: [], outcome_tasks: [], capabilities: [],
        assistant_requests: { original: { status: 'failed', attempt: 1, text: 'Original request', error: { code: 'model_unavailable' } } },
      },
    }, refresh, status: 'ready', error: null,
  });
  mocks.getProjectRuns.mockResolvedValue({ runs: [] });
  mocks.taskAssistantAction.mockRejectedValue({ code: 'manager_execution_failed', message: 'Failed' });
  renderWorkPage('/projects/DEMO/work');
  await userEvent.click(screen.getByRole('button', { name: 'Retry request' }));
  expect(mocks.taskAssistantAction).toHaveBeenCalledWith('DEMO', {
    action: 'retry_message', message_id: 'original', expected_attempt: 1,
  });
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(screen.getByText('Original request')).toBeVisible();
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

  expect(await screen.findByRole('region', { name: 'Task Assistant' })).toBeVisible();
  await userEvent.click(document.querySelector('.team-execution-disclosure > summary'));
  expect(screen.getByText('Keep the current Operator mission visible through review.')).toBeVisible();
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work');
  if (!document.querySelector('.work-context-disclosure').open) fireEvent.click(screen.getByText('Task & history'));
  const selector = await screen.findByRole('combobox', { name: 'Select run' });
  expect(selector.closest('.work-run-context')).toBeInTheDocument();
  expect(selector.closest('.work-run-context__actions')).toBeNull();
  expect(Array.from(selector.options, (option) => option.textContent)).toEqual([
    'Current · Current Operator mission',
    'Archived · Old workflow mission',
  ]);
  expect(screen.queryByRole('heading', { name: 'Run history' })).not.toBeInTheDocument();
});

test.each(['operator_queue', 'task_assistant'])('keeps history row status scoped when %s is blocked', async (source) => {
  const data = projectedWorkData({ active_run: null, state: { run_id: 'run-history' } });
  data.team_workspace.mission = { id: 'other-mission', run_id: '', source, status: 'blocked' };
  data.team_workspace.attention_request = { kind: 'blocker', reason: 'other_task_blocked', actions: [] };
  data.team_workspace.execution_actors = [];
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-history', title: 'Completed history', status: 'completed' }] });

  renderWorkPage('/projects/DEMO/work');

  const navigation = screen.getByRole('navigation', { name: 'Project tasks' });
  const row = await within(navigation).findByRole('button', { name: /Completed history/ });
  expect(row.querySelector('small')).toHaveTextContent(translate('en', 'work.team.status.completed'));
  expect(row.querySelector('small')).not.toHaveTextContent(translate('en', 'work.blocker.title'));
});

test('binds the blocked history badge to the active workflow mission rather than stale state', async () => {
  const data = projectedWorkData({ state: { run_id: 'run-history' } });
  data.team_workspace.attention_request = { kind: 'blocker', reason: 'current_task_blocked', actions: [] };
  data.team_workspace.execution_actors = [];
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [
    { run_id: 'run-history', title: 'Completed history', status: 'completed' },
    { run_id: 'run-active', title: 'Current workflow', status: 'active' },
  ] });

  renderWorkPage('/projects/DEMO/work');

  const navigation = screen.getByRole('navigation', { name: 'Project tasks' });
  const previous = await within(navigation).findByRole('button', { name: /Completed history/ });
  const current = within(navigation).getByRole('button', { name: /Current workflow/ });
  expect(previous.querySelector('small')).toHaveTextContent(translate('en', 'work.team.status.completed'));
  expect(current.querySelector('small')).toHaveTextContent(translate('en', 'work.blocker.title'));
});

test('opens Request changes as a durable Task Assistant discussion without review mutation', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const data = managerReviewData();
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.startChangeIntent.mockResolvedValue({
    intent_commit: { id: 'intent-change-1', status: 'discussing' },
  });

  renderWorkPage();

  expect(screen.getByText('A decision is ready')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Request changes' }));

  expect(mocks.startChangeIntent).toHaveBeenCalledWith(
    'DEMO',
    'security',
    data.team_workspace.attention_request.approval_brief.approval_target,
  );
  expect(mocks.decideReview).not.toHaveBeenCalled();
  expect(refresh).toHaveBeenCalledTimes(1);
});

test('routes the detail canvas Request changes into Task Assistant Intent Commit', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const data = managerReviewData();
  data.workflow_steps = [
    {
      key: 'security',
      title: 'Architecture security review',
      status: 'review-needed',
      approval: 'security',
      feedback: 'Human review required.',
    },
  ];
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.startChangeIntent.mockResolvedValue({
    intent_commit: { id: 'intent-canvas-1', status: 'discussing' },
  });

  renderWorkPage('/projects/DEMO/work/runs/run-active?view=plan');

  const inspector = document.querySelector('.team-inspector');
  await userEvent.click(within(inspector).getByRole('button', { name: /Security/ }));
  const stageDialog = within(inspector).getByRole('dialog', { name: 'Stage details' });
  await userEvent.type(within(stageDialog).getByRole('textbox'), 'Preserve the API boundary.');
  await userEvent.click(within(stageDialog).getByRole('button', { name: 'Request changes' }));

  expect(mocks.startChangeIntent).toHaveBeenCalledWith('DEMO', 'security', {
    run_id: 'run-active',
    target_type: 'review',
    target_id: 'security',
    artifact_key: 'rfc',
    artifact_version: 'sha256:1234567890abcdef1234567890abcdef',
  }, { initialText: 'Preserve the API boundary.' });
  expect(mocks.decideReview).not.toHaveBeenCalled();
  await waitFor(() => {
    expect(screen.getByTestId('location-search')).toHaveTextContent('');
  });
});

test('routes a detail plan task to its exact full Review without a direct decision', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const data = managerReviewData();
  data.agile_board = {
    counts: { backlog: 1, doing: 0, blocked: 0, done: 0 },
    columns: {
      backlog: [{
        id: 'DEMO-ARCH',
        title: 'Review RFC security boundaries',
        role: 'architect',
        status: 'reviewing',
        review_target_id: 'security',
      }],
      doing: [],
      blocked: [],
      done: [],
    },
  };
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });

  renderWorkPage('/projects/DEMO/work/runs/run-active?view=plan');

  const inspector = document.querySelector('.team-inspector');
  await userEvent.click(within(inspector).getByRole('button', { name: /Review RFC security boundaries/ }));
  const taskDialog = within(inspector).getByRole('dialog', {
    name: 'Review RFC security boundaries',
  });
  expect(within(taskDialog).queryByRole('textbox')).not.toBeInTheDocument();
  expect(within(taskDialog).queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument();
  const workbench = within(taskDialog).getByRole('link', {
    name: `${translate('en', 'work.plan.approval.openWorkbench')} →`,
  });
  expect(workbench).toHaveAttribute('href', '/projects/DEMO/review/security');
  await userEvent.click(workbench);

  expect(await screen.findByTestId('full-review-page')).toBeVisible();
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/review/security');
  expect(mocks.startChangeIntent).not.toHaveBeenCalled();
  expect(mocks.decideReview).not.toHaveBeenCalled();
});

test('durably summarizes change text, supports Keep discussing, and commits only on confirm', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const draft = 'Implement Google only while preserving a generic provider interface.';
  const data = managerIntentData('discussing');
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.discussChangeIntent.mockResolvedValue({
    intent_commit: { id: 'intent-change-1', status: 'ready_to_confirm' },
  });

  const view = renderWorkPage();
  expect(screen.getByText('What would you like changed?')).toBeVisible();
  expect(screen.getByText('Discussing · not committed')).toBeVisible();
  await userEvent.type(screen.getByLabelText('What should change?'), draft);
  await userEvent.click(screen.getByRole('button', { name: 'Review Task Assistant interpretation' }));

  expect(mocks.discussChangeIntent).toHaveBeenCalledWith('DEMO', 'intent-change-1', draft);
  expect(mocks.decideReview).not.toHaveBeenCalled();
  expect(mocks.confirmChangeIntent).not.toHaveBeenCalled();

  const confirmData = managerIntentData('ready_to_confirm', draft);
  mocks.useProjectState.mockReturnValue({ data: confirmData, error: null, refresh, status: 'ready' });
  view.rerender(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

  expect(screen.getByText('My understanding is:')).toBeVisible();
  expect(screen.getByText('Draft · not committed')).toBeVisible();
  expect(screen.getByText(draft, { selector: 'li' })).toBeVisible();
  mocks.confirmChangeIntent.mockResolvedValue({ committed: true, idempotent: false });
  await userEvent.click(screen.getByRole('button', { name: 'Confirm changes' }));
  expect(mocks.confirmChangeIntent).toHaveBeenCalledWith('DEMO', 'intent-change-1');
  expect(mocks.decideReview).not.toHaveBeenCalled();
});

test('Keep discussing returns the confirmed interpretation to discussion without commit', async () => {
  const draft = 'Preserve a generic provider boundary.';
  const data = managerIntentData('ready_to_confirm', draft);
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.keepDiscussingChangeIntent.mockResolvedValue({
    intent_commit: { id: 'intent-change-1', status: 'discussing' },
  });

  renderWorkPage();
  await userEvent.click(screen.getByRole('button', { name: 'Keep discussing' }));

  expect(mocks.keepDiscussingChangeIntent).toHaveBeenCalledWith('DEMO', 'intent-change-1');
  expect(mocks.confirmChangeIntent).not.toHaveBeenCalled();
  expect(mocks.decideReview).not.toHaveBeenCalled();
});

test('restores accepted review feedback and submits it without trimming or re-entry', async () => {
  const draft = '  Preserve formatting.  \n\n    keep_indentation()\n';
  const data = managerIntentData('discussing', draft);
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.discussChangeIntent.mockResolvedValue({ intent_commit: { id: 'intent-change-1' } });
  renderWorkPage();

  expect(screen.getByLabelText('What should change?')).toHaveValue(draft);
  expect(screen.queryByText('What would you like changed?')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Review Task Assistant interpretation' }));
  expect(mocks.discussChangeIntent).toHaveBeenCalledWith('DEMO', 'intent-change-1', draft);
  expect(mocks.confirmChangeIntent).not.toHaveBeenCalled();
});

test('offers restart recovery for a durable committing intent', async () => {
  const draft = 'Preserve the provider boundary.';
  const data = managerIntentData('committing', draft);
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.confirmChangeIntent.mockResolvedValue({
    committed: true,
    idempotent: true,
    reconciled: true,
  });

  renderWorkPage();
  expect(screen.getByText('Committing the confirmed change request…')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Resume confirmation' }));

  expect(mocks.confirmChangeIntent).toHaveBeenCalledWith('DEMO', 'intent-change-1');
  expect(mocks.decideReview).not.toHaveBeenCalled();
});

test('shows ambiguous confirmation as unreplayed without another commit action', () => {
  const data = managerIntentData('ambiguous', 'Preserve the provider boundary.');
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });

  renderWorkPage();

  expect(screen.getByText('This confirmation needs recovery.')).toBeVisible();
  expect(screen.getByText('Unresolved · not replayed')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Confirm changes' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Resume confirmation' })).not.toBeInTheDocument();
});

test('restores a persisted discussion on re-entry and fails closed when it is stale', () => {
  const data = managerIntentData(
    'discussing',
    'Keep this persisted draft after navigation.',
  );
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });

  const view = renderWorkPage();
  expect(screen.getByLabelText('What should change?'))
    .toHaveValue('Keep this persisted draft after navigation.');

  const updated = managerIntentData('discussing', 'Use the newer durable draft.');
  mocks.useProjectState.mockReturnValue({ data: updated, error: null, refresh: vi.fn(), status: 'ready' });
  view.rerender(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByLabelText('What should change?')).toHaveValue('Use the newer durable draft.');

  const stale = managerIntentData('stale', 'Old request.');
  mocks.useProjectState.mockReturnValue({ data: stale, error: null, refresh: vi.fn(), status: 'ready' });
  view.rerender(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText('This discussion belongs to an older approval target.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Confirm changes' })).not.toBeInTheDocument();
});

test('submits the exact version-bound approval from the Task Assistant', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const data = managerReviewData();
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.decideReview.mockResolvedValue({ decision: 'approved' });
  const user = userEvent.setup();

  renderWorkPage();

  const manager = document.querySelector('.team-pane--outcomes');
  const attention = manager.querySelector('.team-attention');
  await user.click(within(attention).getByRole('button', { name: 'Approve and continue' }));

  expect(mocks.decideReview).toHaveBeenCalledWith(
    'DEMO',
    'security',
    'approved',
    '',
    {
      expectedApproval: {
        run_id: 'run-active',
        target_type: 'review',
        target_id: 'security',
        task_id: 'DEMO-ARCH',
        artifact_key: 'rfc',
        artifact_version: 'sha256:1234567890abcdef1234567890abcdef',
      },
    },
  );
});

test('refreshes the Task Assistant brief when exact approval binding is stale', async () => {
  const refresh = vi.fn().mockResolvedValue({});
  const stale = Object.assign(
    new Error('This approval changed after the brief was loaded.'),
    { code: 'stale_approval', status: 409 },
  );
  mocks.useProjectState.mockReturnValue({
    data: managerReviewData(), error: null, refresh, status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.decideReview.mockRejectedValue(stale);
  const user = userEvent.setup();

  renderWorkPage();

  const manager = document.querySelector('.team-pane--outcomes');
  await user.click(within(manager).getByRole('button', { name: 'Approve and continue' }));

  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(await screen.findByText('This approval changed after the brief was loaded.')).toBeVisible();
});

test('opens exact Outcome full Review without mutating the decision', async () => {
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
  expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument();
  await user.click(within(dialog).getByRole('button', { name: 'Review document', exact: true }));

  const review = await screen.findByRole('dialog', { name: 'Review document', exact: true });
  expect(within(review).getByText('security', { exact: true })).toBeVisible();
  expect(mocks.startChangeIntent).not.toHaveBeenCalled();
  expect(mocks.decideReview).not.toHaveBeenCalled();
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work/runs/run-active');
});

test('does not lend an actionable review to another same-role Outcome', async () => {
  const data = managerReviewData();
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const user = userEvent.setup();

  renderWorkPage();
  const outcomes = screen.getByRole('region', { name: 'Outcomes' });
  const securityToggle = within(outcomes).getByRole('button', { name: 'Security review' });
  const securityCard = securityToggle.closest('li');
  await user.click(securityToggle);
  await user.click(within(securityCard).getByRole('button', { name: 'Task details' }));

  const dialog = await screen.findByRole('dialog', { name: 'Security review' });
  expect(within(dialog).queryByText('Architecture security review')).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Approval Decision' })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument();
  const waitingBadge = within(dialog).getByText('Waiting', { selector: '.product-status-badge' });
  expect(waitingBadge).toHaveAttribute('data-tone', 'pending');
  expect(within(dialog).queryByText('work.plan.status.waiting')).not.toBeInTheDocument();
});

test('opens exact review from the mobile Outcomes tab and retains its Outcome target', async () => {
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
  const managerTab = within(tablist).getByRole('tab', { name: /Task Assistant/ });
  const outcomesTab = within(tablist).getByRole('tab', { name: /Outcomes/ });
  const managerPanel = document.getElementById('team-panel-manager');
  const outcomesPanel = document.getElementById('team-panel-outcomes');
  await user.click(outcomesTab);
  const reviewButton = within(outcomesPanel).getByRole('button', { name: 'Inspect details →' });

  await user.click(reviewButton);

  const dialog = await screen.findByRole('dialog', { name: 'Architecture' });
  expect(dialog).toBeVisible();
  expect(within(dialog).getByText('Architecture security review')).toBeVisible();
  expect(outcomesTab).toHaveAttribute('aria-selected', 'true');
  expect(outcomesPanel).toBeVisible();
  expect(managerPanel).not.toBeVisible();
  expect(document.querySelector('[data-task-id="DEMO-ARCH"]')).toHaveAttribute('data-active-review', 'true');
  expect(document.querySelector('[data-task-id="DEMO-SEC"]')).not.toHaveAttribute('data-active-review');
  expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
  expect(screen.getByTestId('location-path')).toHaveTextContent('/projects/DEMO/work/runs/run-active');

  await user.click(within(dialog).getAllByRole('button', { name: 'Close' })[0]);

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(outcomesTab).toHaveAttribute('aria-selected', 'true');
  expect(outcomesPanel).toBeVisible();
  expect(reviewButton).toHaveFocus();
  expect(document.querySelector('[data-task-id="DEMO-ARCH"]')).toHaveAttribute('data-active-review', 'true');

  await user.click(managerTab);
  await user.click(outcomesTab);

  expect(outcomesTab).toHaveAttribute('aria-selected', 'true');
  expect(outcomesPanel).toBeVisible();
  expect(managerPanel).not.toBeVisible();
  expect(document.querySelector('[data-task-id="DEMO-ARCH"]')).toHaveAttribute('data-active-review', 'true');
  expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
});

test('requires confirmation before the Task Assistant cancels a mission', async () => {
  const data = projectedWorkData();
  data.team_workspace.attention_request = {
    id: 'workflow-blocked', kind: 'blocker', target_id: 'run-active', reason: 'execution_blocked',
    actions: ['retry', 'cancel_mission'], refs: [],
  };
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.terminateRun.mockResolvedValue({ terminated: true });

  renderWorkPage();
  await userEvent.click(screen.getByRole('button', { name: 'Archive current task' }));
  expect(screen.getByText(/moves the current task to history/)).toBeVisible();
  expect(mocks.terminateRun).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Archive current task' }));
  expect(mocks.terminateRun).toHaveBeenCalledWith('DEMO', 'run-active');
});

test('pauses stale task actions while keeping Refresh and the last task visible', async () => {
  const data = projectedWorkData();
  const refresh = vi.fn();
  mocks.useProjectState.mockReturnValue({ data, error: new Error('Task update failed'), refresh, status: 'stale' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const view = renderWorkPage('/projects/DEMO/work');

  expect(screen.getByText('Task updates are unavailable. Actions are paused until you refresh successfully.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Archive current task' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'Message', exact: true })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Archive current task' }));
  expect(mocks.terminateRun).not.toHaveBeenCalled();
  const currentContext = screen.getByRole('region', { name: 'Current task and next step' });
  await userEvent.click(within(currentContext).getByRole('button', { name: 'Refresh current state', exact: true }));
  expect(refresh).toHaveBeenCalled();

  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  view.rerender(<MemoryRouter initialEntries={['/projects/DEMO/work']}><Routes>
    <Route element={<TranslationOutlet />}><Route path="/projects/:projectKey/work" element={<WorkPage />} /></Route>
  </Routes></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Archive current task' })).toBeEnabled();
  expect(screen.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled();
});

test('requires a new cancellation confirmation when a different task replaces the displayed run', async () => {
  const data = projectedWorkData();
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  const view = renderWorkPage('/projects/DEMO/work');
  await userEvent.click(screen.getByRole('button', { name: 'Archive current task' }));
  expect(screen.getByText(/moves the current task to history/)).toBeVisible();

  const replacement = structuredClone(data);
  replacement.state.run_id = 'replacement-run';
  replacement.team_workspace.mission.run_id = 'replacement-run';
  replacement.team_workspace.mission.id = 'replacement-run';
  mocks.useProjectState.mockReturnValue({ data: replacement, error: null, refresh: vi.fn(), status: 'ready' });
  view.rerender(<MemoryRouter initialEntries={['/projects/DEMO/work']}><Routes>
    <Route element={<TranslationOutlet />}><Route path="/projects/:projectKey/work" element={<WorkPage />} /></Route>
  </Routes></MemoryRouter>);
  expect(screen.queryByText(/moves the current task to history/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Archive current task' }));
  expect(mocks.terminateRun).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Archive current task' }));
  expect(mocks.terminateRun).toHaveBeenCalledWith('DEMO', 'replacement-run');
});

test.each([false, true])('keeps project chat available while historical task activity and actions stay read-only (mobile=%s)', async (mobile) => {
  stubMediaPreferences({ mobile });
  mocks.useProjectState.mockReturnValue({
    data: projectedWorkData(),
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [
      { run_id: 'run-active', idea: 'Make OAuth production ready', status: 'active' },
      { run_id: 'run-history', idea: 'Ship the earlier OAuth revision', status: 'archived' },
    ],
  });
  mocks.getProjectRun.mockResolvedValue({
    state: { run_id: 'run-history', idea: 'Ship the earlier OAuth revision', current_node: 'end', next_node: '' },
    team_workspace: {
      schema_version: 'team_workspace.v1',
      mission: {
        id: 'run-history', run_id: 'run-history', source: 'workflow',
        objective: 'Ship the earlier OAuth revision', status: 'completed', read_only: true,
      },
      manager_timeline: [
        { id: 'history-request', actor: 'user', event: 'mission_requested', timestamp: '2026-08-30T10:00:00Z', content: 'Ship the earlier OAuth revision', details: {} },
        { id: 'history-retry', actor: 'manager', event: 'work_retry_converged', timestamp: '2026-08-30T10:01:00Z', content: '', details: { attempt_count: 2 } },
        { id: 'history-complete', actor: 'manager', event: 'mission_completed', timestamp: '2026-08-30T10:02:00Z', content: '', details: {} },
      ],
      attention_request: null,
      execution_actors: [{
        id: 'history-implementation', role: 'implementation', state: 'completed',
        responsibility: 'Implement the earlier OAuth revision', task_id: 'DEMO-HISTORY-1',
        status_reason: 'task_completed',
      }],
      outcome_tasks: [{
        id: 'DEMO-HISTORY-1', objective: 'Implement the earlier OAuth revision',
        owner: 'implementation', status: 'completed', status_reason: 'task_completed',
        dependencies: [], acceptance_criteria: ['Historical release passed'],
        primary_outcome: { label: 'PR #40' }, supporting_refs: [],
      }],
      capabilities: [],
    },
  });

  renderWorkPage('/projects/DEMO/work/runs/run-history');

  expect(await screen.findByText('Historical runs are read-only and visually separate from the active run.')).toBeVisible();
  const historicalWorkspace = document.querySelector('.team-workspace[data-read-only="true"]');
  expect(historicalWorkspace).toBeInTheDocument();
  const manager = historicalWorkspace.querySelector('.team-pane--manager');
  expect(historicalWorkspace.querySelector('.team-pane--execution')).toBeInTheDocument();
  expect(historicalWorkspace.querySelector('.team-pane--outcomes')).toBeInTheDocument();
  expect((await within(historicalWorkspace).findAllByText('Implement the earlier OAuth revision')).length)
    .toBeGreaterThan(0);
  expect(within(historicalWorkspace).getByRole('button', { name: 'Implement the earlier OAuth revision', hidden: true }))
    .toHaveAttribute('aria-expanded', 'false');
  expect(within(manager).getByRole('textbox', { name: 'Message' })).toBeVisible();
  expect(within(manager).queryByRole('button', { name: 'Approve', exact: true })).not.toBeInTheDocument();
  expect(within(manager).queryByRole('button', { name: 'Cancel entire mission' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('Historical runs are read-only and visually separate from the active run.'));
  const activity = screen.getByRole('list', { name: 'Historical task activity' });
  const request = within(activity).getByText('Ship the earlier OAuth revision');
  const retry = within(activity).getByText('The work needed 2 attempts and then completed.');
  const complete = within(activity).getByText('The mission reached its recorded completion state.');
  expect(request.compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(retry.compareDocumentPosition(complete) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

});

test('dispatches a track only with the confirmed task and project', async () => {
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

  await screen.findByRole('button', { name: 'Board' });

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


test('opens exact current task inputs on demand without replacing an unsent project message', async () => {
  mocks.useProjectState.mockReturnValue({ data: projectedWorkData(), status: 'ready', refresh: vi.fn() });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.getTaskInputContext.mockResolvedValue({
    schema_version: 'goodteam.task-input-context.v1', project_key: 'DEMO', run_id: 'run-active',
    task_kind: 'workflow_run', read_only: true, capture_status: 'locked', input_origin: 'task_start_lock',
    input: { exact_request: '  Exact task input\nKeep this line.  ', source_type: 'user', delivery_mode: 'pull_request', delivery_standards: ['Add a regression test'] },
    captured_at: '2026-10-11T10:00:00Z', team_revision: 3,
    task_lock_ref: { id: 'task-lock-1', digest: `sha256:${'a'.repeat(64)}` }, subjects: [],
  });
  renderWorkPage();
  const draft = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(draft, { target: { value: 'Still drafting this message' } });
  expect(mocks.getTaskInputContext).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Task inputs and methods' }));
  await waitFor(() => expect(mocks.getTaskInputContext).toHaveBeenCalledWith('DEMO', 'run-active', expect.objectContaining({ signal: expect.any(AbortSignal) })));
  expect(screen.getByText(/Exact task input/).textContent).toBe('  Exact task input\nKeep this line.  ');
  await userEvent.click(screen.getByRole('button', { name: 'Close task inputs and methods' }));
  expect(screen.getByRole('textbox', { name: 'Message' })).toBe(draft);
  expect(draft).toHaveValue('Still drafting this message');
  expect(mocks.sendTaskAssistantMessage).not.toHaveBeenCalled();
  expect(mocks.taskAssistantAction).not.toHaveBeenCalled();
});


test.each(['operator_queue', 'task_assistant'])('never substitutes a stale workflow input record for %s', async source => {
  const data = projectedWorkData();
  data.team_workspace.mission = { ...data.team_workspace.mission, source, run_id: 'run-active' };
  mocks.useProjectState.mockReturnValue({ data, status: 'ready', refresh: vi.fn() });
  mocks.getProjectRuns.mockResolvedValue({ runs: [] });
  renderWorkPage('/projects/DEMO/work');
  await waitFor(() => expect(mocks.getProjectRuns).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: 'Task inputs and methods' })).not.toBeInTheDocument();
  expect(mocks.getTaskInputContext).not.toHaveBeenCalled();
});

test('reads the selected historical input without borrowing the current run', async () => {
  mocks.useProjectState.mockReturnValue({ data: projectedWorkData(), status: 'ready', refresh: vi.fn() });
  mocks.getProjectRuns.mockResolvedValue({ runs: [
    { run_id: 'run-active', status: 'active' }, { run_id: 'run-history', status: 'archived' },
  ] });
  const history = projectedWorkData();
  history.state.run_id = 'run-history';
  history.team_workspace.mission = { ...history.team_workspace.mission, run_id: 'run-history', read_only: true };
  mocks.getProjectRun.mockResolvedValue(history);
  mocks.getTaskInputContext.mockResolvedValue({
    schema_version: 'goodteam.task-input-context.v1', project_key: 'DEMO', run_id: 'run-history',
    task_kind: 'workflow_run', read_only: true, capture_status: 'legacy_unverified', input_origin: 'workflow_state',
    input: { exact_request: 'Archived workflow input', source_type: '', delivery_mode: '', delivery_standards: [] },
    captured_at: '', team_revision: null, task_lock_ref: null, subjects: [],
  });
  renderWorkPage('/projects/DEMO/work/runs/run-history');
  await userEvent.click(await screen.findByRole('button', { name: 'Task inputs and methods' }));
  await screen.findByText('Archived workflow input');
  expect(mocks.getTaskInputContext).toHaveBeenCalledWith('DEMO', 'run-history', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(mocks.getTaskInputContext).toHaveBeenCalledTimes(1);
});


test('does not fetch a workflow mission declared for another project', async () => {
  const data = projectedWorkData();
  data.team_workspace.mission.project_key = 'OTHER';
  mocks.useProjectState.mockReturnValue({ data, status: 'ready', refresh: vi.fn() });
  mocks.getProjectRuns.mockResolvedValue({ runs: [] });
  renderWorkPage('/projects/DEMO/work');
  await waitFor(() => expect(mocks.getProjectRuns).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: 'Task inputs and methods' })).not.toBeInTheDocument();
  expect(mocks.getTaskInputContext).not.toHaveBeenCalled();
});

function retryWorkData({ runId = 'run-active', revision = 'a', attemptId = 'attempt-original', mode = 'retry_implementation' } = {}) {
  const data = projectedWorkData();
  data.state.run_id = runId;
  data.team_workspace.mission = { ...data.team_workspace.mission, id: runId, run_id: runId, status: 'blocked' };
  data.team_workspace.execution_actors = [];
  data.team_workspace.attention_request = {
    id: 'workflow-blocked', kind: 'blocker', target_id: runId,
    reason: `Failure evidence for ${attemptId}`, actions: ['retry'], refs: [attemptId],
  };
  data.worker_jobs = [{ id: attemptId, status: 'failed', updated_at: '2026-10-11T12:00:00Z' }];
  data.retry_target = {
    schema_version: 'workflow-retry-target.v1', project_key: 'DEMO', run_id: runId,
    version: `sha256:${revision.repeat(64)}`, available: true, mode, reason: '',
    attempts: [{ id: attemptId, role: 'implementation', status: 'failed', attempt: 1, retry_attempt: 0 }],
    active_attempt_ids: [],
  };
  return data;
}

test.each([
  ['missing', data => { delete data.retry_target; }],
  ['wrong project', data => { data.retry_target.project_key = 'OTHER'; }],
  ['wrong run', data => { data.retry_target.run_id = 'run-other'; }],
  ['invalid version', data => { data.retry_target.version = ''; }],
  ['unavailable', data => { data.retry_target.available = false; }],
  ['active attempt', data => { data.retry_target.available = false; data.retry_target.reason = 'active_attempt'; data.retry_target.active_attempt_ids = ['still-running']; }],
  ['unknown attempt', data => { data.retry_target.available = false; data.retry_target.reason = 'attempt_unknown'; }],
  ['assistant mission', data => { data.team_workspace.mission.source = 'task_assistant'; }],
])('does not send workflow retry with %s binding', async (_case, invalidate) => {
  const data = retryWorkData();
  invalidate(data);
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  renderWorkPage();
  const retryButton = screen.getByRole('button', { name: 'Retry failed stage' });
  expect(retryButton).toBeDisabled();
  await userEvent.click(retryButton);
  expect(mocks.retryRun).not.toHaveBeenCalled();
});

test.each(['retry_target_changed', 'run_binding_mismatch', 'retry_attempt_active', 'retry_attempt_unknown'])('pins submitted retry evidence and draft through %s until explicit successful refresh', async (code) => {
  const original = retryWorkData();
  const replacement = retryWorkData({
    runId: code === 'run_binding_mismatch' ? 'run-replacement' : 'run-active', revision: 'b', attemptId: 'attempt-new',
  });
  const refresh = vi.fn().mockResolvedValue(null);
  mocks.useProjectState.mockReturnValue({ data: original, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  let rejectRetry;
  mocks.retryRun.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRetry = reject; }));
  const view = renderWorkPage('/projects/DEMO/work');
  const draft = 'Keep this unsent investigation note.\n  Preserve whitespace.';
  fireEvent.change(screen.getByRole('textbox', { name: 'Message', exact: true }), { target: { value: draft } });
  fireEvent.click(screen.getByRole('button', { name: 'Retry failed stage' }));
  fireEvent.click(screen.getByRole('button', { name: 'Retry failed stage' }));
  expect(mocks.retryRun).toHaveBeenCalledTimes(1);
  expect(mocks.retryRun).toHaveBeenCalledWith('DEMO', original.retry_target);

  // A poll can replace the entire current run while the displayed request is in flight.
  mocks.useProjectState.mockReturnValue({ data: replacement, error: null, refresh, status: 'ready' });
  view.rerender(workPageElement('/projects/DEMO/work'));
  expect(screen.getByText('attempt-original', { selector: 'code' })).toBeInTheDocument();
  expect(screen.queryByText('attempt-new', { selector: 'code' })).not.toBeInTheDocument();
  await act(async () => { rejectRetry({ code, message: 'Server target changed' }); });
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Retry failed stage' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(draft);
  expect(screen.getByText('Failure evidence for attempt-original', { selector: 'pre' })).toBeInTheDocument();
  expect(screen.queryByText('Failure evidence for attempt-new', { selector: 'pre' })).not.toBeInTheDocument();
  expect(screen.getByText(translate('en', `work.retry.conflict.${code}`))).toBeInTheDocument();

  // Even another ready poll cannot dismiss a conflict or unlock another submission.
  view.rerender(workPageElement('/projects/DEMO/work'));
  await userEvent.click(screen.getByRole('button', { name: 'Retry failed stage' }));
  expect(mocks.retryRun).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole('button', { name: 'Refresh recovery evidence' }));
  expect(screen.getByRole('button', { name: 'Retry failed stage' })).toBeDisabled();
  expect(screen.getByText('attempt-original', { selector: 'code' })).toBeInTheDocument();

  refresh.mockResolvedValue(replacement);
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: replacement.state.run_id, status: 'active' }] });
  await userEvent.click(screen.getByRole('button', { name: 'Refresh recovery evidence' }));
  expect(screen.getByRole('button', { name: 'Retry failed stage' })).toBeEnabled();
  expect(screen.getByText('attempt-new', { selector: 'code' })).toBeInTheDocument();
  expect(screen.queryByText('attempt-original', { selector: 'code' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(draft);
  expect(mocks.retryRun).toHaveBeenCalledTimes(1);
  mocks.retryRun.mockResolvedValueOnce({ retried: true });
  await userEvent.click(screen.getByRole('button', { name: 'Retry failed stage' }));
  expect(mocks.retryRun).toHaveBeenLastCalledWith('DEMO', replacement.retry_target);
});

test('keeps an accepted retry locked when refreshed execution evidence is unavailable', async () => {
  const data = retryWorkData({ mode: 'resume_transition' });
  const refresh = vi.fn().mockResolvedValue(null);
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh, status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [{ run_id: 'run-active', status: 'active' }] });
  mocks.retryRun.mockResolvedValueOnce({ retried: true });
  renderWorkPage();
  await userEvent.click(screen.getByRole('button', { name: 'Resume interrupted stage' }));
  expect(mocks.retryRun).toHaveBeenCalledWith('DEMO', data.retry_target);
  expect(screen.getByRole('button', { name: 'Resume interrupted stage' })).toBeDisabled();
  expect(screen.getByText(translate('en', 'work.retry.conflict.retry_refresh_required'))).toBeInTheDocument();
});

test('does not retarget an old run route to the replacement current run', async () => {
  const replacement = retryWorkData({ runId: 'run-replacement', revision: 'b', attemptId: 'attempt-new' });
  mocks.useProjectState.mockReturnValue({ data: replacement, error: null, refresh: vi.fn(), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [
    { run_id: 'run-active', status: 'active' }, { run_id: 'run-replacement', status: 'active' },
  ] });
  renderWorkPage('/projects/DEMO/work/runs/run-active');
  await waitFor(() => expect(mocks.getProjectRuns).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: 'Retry failed stage' })).not.toBeInTheDocument();
  expect(mocks.retryRun).not.toHaveBeenCalled();
});

test('ignores a late retry conflict after navigating to another run and returning', async () => {
  const data = retryWorkData();
  const historical = retryWorkData({ runId: 'run-history', attemptId: 'history-attempt' });
  historical.team_workspace.mission.read_only = true;
  mocks.useProjectState.mockReturnValue({ data, error: null, refresh: vi.fn().mockResolvedValue(data), status: 'ready' });
  mocks.getProjectRuns.mockResolvedValue({ runs: [
    { run_id: 'run-active', status: 'active' }, { run_id: 'run-history', status: 'archived' },
  ] });
  mocks.getProjectRun.mockResolvedValue(historical);
  let rejectRetry;
  mocks.retryRun.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRetry = reject; }));
  renderWorkPage();
  await screen.findByRole('combobox', { name: 'Select run' });
  fireEvent.click(screen.getByRole('button', { name: 'Retry failed stage' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Select run' }), { target: { value: 'run-history' } });
  await waitFor(() => expect(screen.getByTestId('location-path')).toHaveTextContent('/runs/run-history'));
  await act(async () => { rejectRetry({ code: 'retry_target_changed' }); });
  expect(screen.queryByRole('button', { name: 'Refresh recovery evidence' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Select run' }), { target: { value: 'run-active' } });
  expect(await screen.findByRole('button', { name: 'Retry failed stage' })).toBeEnabled();
  expect(mocks.retryRun).toHaveBeenCalledTimes(1);
});
