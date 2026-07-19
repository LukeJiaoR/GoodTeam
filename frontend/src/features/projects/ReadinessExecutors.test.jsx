import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import ProjectHomePage from './ProjectHomePage';
import {
  cancelReadiness,
  getReadinessStepArtifact,
  retryReadinessStep,
  startProjectWorkflow,
  startReadiness,
  submitReadinessInput,
} from './projectApi';

function homeWith({
  capabilities = { start_workflow: false, start_workflow_reason: 'mode_specific_readiness_pending' },
  execution,
  lifecycle = 'preparing',
  mode = 'existing_project',
  nextAction = { id: 'repository_understanding', status: 'pending' },
  steps = [],
  artifacts = {},
} = {}) {
  return {
    project: { project_key: 'DEMO', display_name: 'Demo', root: '/tmp/demo' },
    facts: { root: '/tmp/demo', detected_mode: mode, active_run: null },
    readiness: {
      schema_version: 2,
      project_key: 'DEMO',
      project_mode: mode,
      lifecycle,
      execution,
      steps,
      artifacts,
      inputs: {},
      updated_at: '2026-07-17T12:00:00Z',
    },
    capabilities,
    next_action: nextAction,
  };
}

function apiFor(home, overrides = {}) {
  return {
    getProjectHome: vi.fn().mockResolvedValue(home),
    startReadiness: vi.fn(),
    submitReadinessInput: vi.fn(),
    retryReadinessStep: vi.fn(),
    cancelReadiness: vi.fn(),
    getReadinessStepArtifact: vi.fn(),
    startProjectWorkflow: vi.fn(),
    confirmProjectMode: vi.fn(),
    ...overrides,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test('existing project primary CTA starts one idempotent read-only readiness execution', async () => {
  const user = userEvent.setup();
  const initial = homeWith({ steps: [{ id: 'repository_understanding', status: 'pending', required: true }] });
  const running = homeWith({
    execution: { id: 'exec-1', status: 'running', current_step_id: 'repository_understanding' },
    steps: [{ id: 'repository_understanding', status: 'running', required: true, attempt: 1 }],
  });
  const api = apiFor(initial, { startReadiness: vi.fn().mockResolvedValue(running) });

  render(<ProjectHomePage api={api} language="zh-CN" projectKey="DEMO" />);
  const start = await screen.findByRole('button', { name: '开始理解代码库' });
  await user.click(start);

  expect(api.startReadiness).toHaveBeenCalledTimes(1);
  expect(api.startReadiness).toHaveBeenCalledWith('DEMO', {
    inputs: {},
    idempotencyKey: expect.any(String),
  });
  expect(await screen.findByText('GoodTeam 正在准备这个项目')).toBeVisible();
  expect(screen.getByText('理解代码库').closest('li')).toHaveAttribute('aria-current', 'step');
});

test('new project initialization submits all five product inputs', async () => {
  const user = userEvent.setup();
  const initial = homeWith({ mode: 'new_project', nextAction: { id: 'new_project_init', status: 'pending' } });
  const running = homeWith({
    mode: 'new_project',
    execution: { id: 'exec-new', status: 'running', current_step_id: 'new_project_init' },
    steps: [{ id: 'new_project_init', status: 'running', required: true }],
  });
  const api = apiFor(initial, { startReadiness: vi.fn().mockResolvedValue(running) });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  await user.type(await screen.findByLabelText('Project goal'), 'Ship a local studio');
  await user.type(screen.getByLabelText('Project brief'), 'A small publishing workflow');
  await user.type(screen.getByLabelText('Technology preferences'), 'React and Python');
  await user.type(screen.getByLabelText('Necessary constraints'), 'Offline first');
  await user.type(screen.getByLabelText('Prohibited or approval-required operations'), 'No installs without approval');
  await user.click(screen.getByRole('button', { name: 'Start project initialization' }));

  expect(api.startReadiness).toHaveBeenCalledWith('DEMO', {
    idempotencyKey: expect.any(String),
    inputs: {
      goal: 'Ship a local studio',
      brief: 'A small publishing workflow',
      tech_preferences: 'React and Python',
      constraints: 'Offline first',
      prohibited_operations: 'No installs without approval',
    },
  });
});

test('needs-input command confirmation posts only the backend step values contract', async () => {
  const user = userEvent.setup();
  const needsInput = homeWith({
    execution: { id: 'exec-2', status: 'needs_input', current_step_id: 'command_verification' },
    lifecycle: 'attention_required',
    nextAction: { id: 'command_verification', status: 'needs_input' },
    steps: [{
      id: 'command_verification',
      status: 'needs_input',
      required: true,
      input_requirements: {
        fields: [{ id: 'confirmed_command_ids', type: 'multi_select', required: true, options: [
          { id: 'test', label: 'npm test' },
          { id: 'lint', label: 'npm run lint' },
        ] }],
      },
    }],
  });
  const api = apiFor(needsInput, { submitReadinessInput: vi.fn().mockResolvedValue(needsInput) });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  await user.click(await screen.findByRole('checkbox', { name: 'npm test' }));
  await user.click(screen.getByRole('button', { name: 'Confirm and continue' }));

  expect(api.submitReadinessInput).toHaveBeenCalledWith('DEMO', {
    step_id: 'command_verification',
    values: { confirmed_command_ids: ['test'] },
  });
});

test('duplicate discovered commands render once and confirm every source ID together', async () => {
  const user = userEvent.setup();
  const needsInput = homeWith({
    execution: { id: 'exec-grouped', status: 'needs_input', current_step_id: 'command_verification' },
    lifecycle: 'attention_required',
    nextAction: { id: 'command_verification', status: 'needs_input' },
    steps: [{
      id: 'command_verification',
      status: 'needs_input',
      required: true,
      input_requirements: {
        kind: 'command_confirmation',
        commands: [
          { id: 'root-test', command: 'npm run test', source: 'package.json' },
          { id: 'web-test', command: 'npm run test', source: 'apps/web/package.json' },
          { id: 'lint', command: 'npm run lint', source: 'package.json' },
        ],
      },
    }],
  });
  const api = apiFor(needsInput, { submitReadinessInput: vi.fn().mockResolvedValue(needsInput) });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);

  expect(await screen.findAllByRole('checkbox')).toHaveLength(2);
  await user.click(screen.getByRole('checkbox', { name: /npm run test/ }));
  await user.click(screen.getByRole('button', { name: 'Confirm and continue' }));

  expect(api.submitReadinessInput).toHaveBeenCalledWith('DEMO', {
    step_id: 'command_verification',
    values: { confirmed_command_ids: ['root-test', 'web-test'] },
  });
});

test('command confirmation keeps dev and platform scripts behind advanced disclosure', async () => {
  const user = userEvent.setup();
  const needsInput = homeWith({
    execution: { id: 'exec-tiered', status: 'needs_input', current_step_id: 'command_verification' },
    lifecycle: 'attention_required',
    nextAction: { id: 'command_verification', status: 'needs_input' },
    steps: [{
      id: 'command_verification',
      status: 'needs_input',
      required: true,
      input_requirements: {
        kind: 'command_confirmation',
        commands: [
          { id: 'test', kind: 'test', command: 'npm run test', source: 'package.json' },
          { id: 'dev', kind: 'run', command: 'npm run dev', source: 'package.json' },
          { id: 'h5', kind: 'build', command: 'npm run build:h5', source: 'apps/mini/package.json' },
        ],
      },
    }],
  });

  render(<ProjectHomePage api={apiFor(needsInput)} language="en" projectKey="DEMO" />);

  expect(await screen.findByRole('checkbox', { name: /npm run test/ })).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /npm run dev/ })).not.toBeVisible();
  expect(screen.getByRole('checkbox', { name: /npm run build:h5/ })).not.toBeVisible();
  await user.click(screen.getByText('Other advanced commands (2)'));
  expect(screen.getByRole('checkbox', { name: /npm run dev/ })).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /npm run build:h5/ })).toBeVisible();
});

test('command confirmation prevents an empty submission and works from the keyboard', async () => {
  const user = userEvent.setup();
  const needsInput = homeWith({
    execution: { id: 'exec-keyboard', status: 'needs_input', current_step_id: 'command_verification' },
    lifecycle: 'attention_required',
    nextAction: { id: 'command_verification', status: 'needs_input' },
    steps: [{
      id: 'command_verification',
      status: 'needs_input',
      required: true,
      input_requirements: {
        kind: 'command_confirmation',
        commands: [{ id: 'test', command: 'npm test' }],
      },
    }],
  });
  const api = apiFor(needsInput, { submitReadinessInput: vi.fn().mockResolvedValue(needsInput) });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  const checkbox = await screen.findByRole('checkbox', { name: 'npm test' });
  const submit = screen.getByRole('button', { name: 'Confirm and continue' });
  expect(submit).toBeDisabled();
  expect(screen.getByText('Select at least one discovered command to continue.')).toBeVisible();

  checkbox.focus();
  await user.keyboard('[Space]');
  expect(submit).toBeEnabled();
  expect(submit).not.toHaveAttribute('aria-describedby');
  expect(screen.queryByText('Select at least one discovered command to continue.')).not.toBeInTheDocument();
  submit.focus();
  await user.keyboard('[Enter]');
  expect(api.submitReadinessInput).toHaveBeenCalledWith('DEMO', {
    step_id: 'command_verification',
    values: { confirmed_command_ids: ['test'] },
  });
});

test('failed step exposes stable details and retries that exact step', async () => {
  const user = userEvent.setup();
  const failed = homeWith({
    execution: { id: 'exec-3', status: 'failed', current_step_id: 'worker_preflight' },
    lifecycle: 'attention_required',
    nextAction: { id: 'worker_preflight', status: 'failed' },
    steps: [{
      id: 'worker_preflight',
      status: 'failed',
      required: true,
      attempt: 2,
      error: { code: 'worker_unavailable', message: 'Worker is unavailable.', request_id: 'req-17' },
      recovery: ['check_worker', 'retry'],
    }],
  });
  const api = apiFor(failed, { retryReadinessStep: vi.fn().mockResolvedValue(failed) });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  expect(await screen.findByText('worker_unavailable')).toBeVisible();
  expect(screen.getByText('Request ID: req-17')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Retry this step' }));
  expect(api.retryReadinessStep).toHaveBeenCalledWith('DEMO', 'worker_preflight');
});

test.each([
  ['en', 'Worker preflight needs configuration or permission before it can continue.', 'Configure a usable Worker'],
  ['zh-CN', 'Worker 预检需要补充配置或权限后才能继续。', '配置可用的 Worker'],
])('failed and recovery details are product-localized in %s', async (language, message, recovery) => {
  const failed = homeWith({
    execution: { id: 'exec-i18n', status: 'failed', current_step_id: 'worker_preflight' },
    lifecycle: 'attention_required',
    steps: [{
      id: 'worker_preflight',
      status: 'failed',
      required: true,
      error: {
        code: 'worker_preflight_needs_input',
        message: 'Backend fallback must not leak into localized UI.',
      },
      recovery: ['configure_worker'],
    }],
  });

  render(<ProjectHomePage api={apiFor(failed)} language={language} projectKey="DEMO" />);
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.getByText(new RegExp(recovery))).toBeVisible();
  expect(screen.queryByText('Backend fallback must not leak into localized UI.')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: language === 'zh-CN' ? '重试此步骤' : 'Retry this step' })).not.toBeInTheDocument();
});

test('an action error exposes localized recovery and reloads persisted Home state', async () => {
  const user = userEvent.setup();
  const needsInput = homeWith({
    execution: { id: 'exec-action-error', status: 'needs_input', current_step_id: 'constraints_confirmation' },
    lifecycle: 'attention_required',
    steps: [{
      id: 'constraints_confirmation',
      status: 'needs_input',
      required: true,
      input_requirements: { kind: 'constraints', constraints: [] },
    }],
  });
  const conflict = Object.assign(new Error('Backend fallback'), {
    code: 'readiness_input_conflict',
    details: { step_id: 'constraints_confirmation' },
    recovery: ['reload_project'],
    requestId: 'req-action',
  });
  const api = apiFor(needsInput, {
    getProjectHome: vi.fn().mockResolvedValue(needsInput),
    submitReadinessInput: vi.fn().mockRejectedValue(conflict),
  });

  render(<ProjectHomePage api={api} language="zh-CN" projectKey="DEMO" />);
  await user.click(await screen.findByRole('button', { name: '确认并继续' }));
  expect(screen.getByText('该步骤已使用其他输入确认。请先重新加载已保存状态。')).toBeVisible();
  expect(screen.getByText('请求 ID：req-action')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '重新加载项目' }));
  expect(api.getProjectHome).toHaveBeenCalledTimes(2);
});

test('complete readiness renders the structured overview and follows the real backend workflow URL', async () => {
  const user = userEvent.setup();
  const complete = homeWith({
    capabilities: { start_workflow: true },
    execution: { id: 'exec-4', status: 'complete', completed_at: '2026-07-17T12:10:00Z' },
    lifecycle: 'ready',
    nextAction: { id: 'shared_readiness_complete', status: 'complete' },
    steps: [{ id: 'repository_understanding', status: 'complete', required: true, artifact_refs: ['repository_overview'] }],
    artifacts: {
      repository_overview: {
        ref: 'repository_overview',
        type: 'repository_overview',
        data: {
          stack: ['React', 'Python'],
          tree: ['frontend/', 'src/'],
          entrypoints: ['frontend/src/main.jsx'],
          commands: [{ id: 'test', kind: 'test', command: 'npm test' }],
          docs: ['AGENTS.md'],
          constraints: ['Read-only discovery'],
          risks: ['No CI config'],
          next_steps: ['Confirm commands'],
        },
      },
    },
  });
  const navigateWorkflow = vi.fn();
  const api = apiFor(complete, {
    startProjectWorkflow: vi.fn().mockResolvedValue({ workflow_url: '/projects/DEMO/work' }),
  });

  render(<ProjectHomePage api={api} language="en" navigateWorkflow={navigateWorkflow} projectKey="DEMO" />);
  expect(await screen.findByRole('heading', { name: 'Codebase overview' })).toBeVisible();
  expect(screen.getByText('React')).toBeVisible();
  expect(screen.getByText('npm test')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Start workflow' }));
  expect(api.startProjectWorkflow).toHaveBeenCalledWith('DEMO');
  expect(navigateWorkflow).toHaveBeenCalledWith('/projects/DEMO/work');
});

test('project readiness API helpers use the stable executor resource paths', async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response('{}', {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);

  await startReadiness('DEMO SPACE', { inputs: { goal: 'Goal' }, idempotencyKey: 'idem-1' });
  await submitReadinessInput('DEMO SPACE', { step_id: 'command_verification', values: { confirmed_command_ids: ['test'] } });
  await retryReadinessStep('DEMO SPACE', 'worker_preflight');
  await cancelReadiness('DEMO SPACE');
  await getReadinessStepArtifact('DEMO SPACE', 'repository understanding');
  await startProjectWorkflow('DEMO SPACE');

  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/projects/DEMO%20SPACE/readiness/start',
    '/api/projects/DEMO%20SPACE/readiness/input',
    '/api/projects/DEMO%20SPACE/readiness/retry',
    '/api/projects/DEMO%20SPACE/readiness/cancel',
    '/api/projects/DEMO%20SPACE/readiness/steps/repository%20understanding/artifact',
    '/api/projects/DEMO%20SPACE/workflow',
  ]);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    inputs: { goal: 'Goal' },
    idempotency_key: 'idem-1',
  });
});

test('running Home polls persisted state and stops when the execution completes', async () => {
  vi.useFakeTimers();
  const running = homeWith({
    execution: { id: 'exec-poll', status: 'running', current_step_id: 'repository_understanding' },
    steps: [{ id: 'repository_understanding', status: 'running', required: true }],
  });
  const complete = homeWith({
    execution: { id: 'exec-poll', status: 'complete' },
    lifecycle: 'ready',
    steps: [{ id: 'repository_understanding', status: 'complete', required: true }],
  });
  const api = apiFor(running, { getProjectHome: vi.fn().mockResolvedValueOnce(running).mockResolvedValue(complete) });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  await vi.waitFor(() => expect(screen.getByText('GoodTeam is preparing this project')).toBeVisible());
  await vi.advanceTimersByTimeAsync(1_200);
  await vi.waitFor(() => expect(api.getProjectHome).toHaveBeenCalledTimes(2));
  await vi.waitFor(() => expect(screen.getAllByText('Complete').length).toBeGreaterThan(0));
  vi.useRealTimers();
});

test('an older deferred poll cannot overwrite a newer mutation response', async () => {
  vi.useFakeTimers();
  const running = homeWith({
    execution: { id: 'exec-race', status: 'running', current_step_id: 'repository_understanding' },
    steps: [{ id: 'repository_understanding', status: 'running', required: true }],
  });
  running.readiness.state_revision = 1;
  const cancelled = homeWith({
    execution: { id: 'exec-race', status: 'cancelled', current_step_id: '' },
    lifecycle: 'attention_required',
    nextAction: { id: 'repository_understanding', status: 'pending' },
    steps: [{ id: 'repository_understanding', status: 'pending', required: true }],
  });
  cancelled.readiness.state_revision = 2;
  const stalePoll = deferred();
  const api = apiFor(running, {
    cancelReadiness: vi.fn().mockResolvedValue(cancelled),
    getProjectHome: vi.fn()
      .mockResolvedValueOnce(running)
      .mockImplementationOnce(() => stalePoll.promise),
  });

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  await vi.waitFor(() => expect(screen.getByText('GoodTeam is preparing this project')).toBeVisible());
  await vi.advanceTimersByTimeAsync(1_200);
  await vi.waitFor(() => expect(api.getProjectHome).toHaveBeenCalledTimes(2));

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Cancel safely' }));
    await Promise.resolve();
  });
  expect(screen.getAllByText('Cancelled').length).toBeGreaterThan(0);
  await act(async () => {
    stalePoll.resolve(running);
    await Promise.resolve();
  });

  expect(screen.getAllByText('Cancelled').length).toBeGreaterThan(0);
  expect(screen.queryByText('GoodTeam is preparing this project')).not.toBeInTheDocument();
  vi.useRealTimers();
});

test('refresh restores the persisted needs-input step without starting another execution', async () => {
  const persisted = homeWith({
    execution: { id: 'exec-restored', status: 'needs_input', current_step_id: 'constraints_confirmation' },
    lifecycle: 'attention_required',
    steps: [{
      id: 'constraints_confirmation',
      status: 'needs_input',
      required: true,
      input_requirements: { kind: 'constraints', constraints: ['Keep the target read-only'] },
    }],
  });
  const api = apiFor(persisted);
  const first = render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  expect(await screen.findByRole('heading', { name: 'Confirm project constraints' })).toBeVisible();
  first.unmount();

  render(<ProjectHomePage api={api} language="en" projectKey="DEMO" />);
  expect(await screen.findByRole('heading', { name: 'Confirm project constraints' })).toBeVisible();
  expect(api.getProjectHome).toHaveBeenCalledTimes(2);
  expect(api.startReadiness).not.toHaveBeenCalled();
});

test('narrow-screen readiness keeps a single-column contract and named keyboard controls', async () => {
  const css = fs.readFileSync(
    path.resolve(globalThis.process.cwd(), 'src/styles/product.css'),
    'utf8',
  );
  expect(css).toMatch(/@media \(max-width: 767px\)[\s\S]*\.product-shell \.readiness-start-panel,[\s\S]*grid-template-columns: minmax\(0, 1fr\)/);

  const initial = homeWith({ steps: [{ id: 'repository_understanding', status: 'pending', required: true }] });
  render(<ProjectHomePage api={apiFor(initial)} language="en" projectKey="DEMO" />);
  expect(await screen.findByRole('main')).toHaveAttribute('id', 'main-content');
  expect(screen.getByRole('button', { name: 'Start understanding the codebase' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Start workflow' })).toBeDisabled();
});
