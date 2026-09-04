import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import TeamPage from './TeamPage';
import { buildWorkerTypes } from './teamModel';

const mocks = vi.hoisted(() => ({
  getAgents: vi.fn(),
  saveWorkerDefaults: vi.fn(),
  projectState: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('../runs/useProjectState', () => ({
  default: () => ({ data: mocks.projectState(), error: null, refresh: mocks.refresh, status: 'ready' }),
}));

function stateFixture() {
  return {
    active_run: { run_id: 'run-1' },
    start_context: {
      project_name: 'Demo project',
      worker_backend: { mode: 'codex' },
      worker_backend_options: ['codex', 'claude'],
      worker_roles: ['architect', 'implementation', 'qa'],
      role_model_overrides: { architect: 'claude:claude-opus-4-5' },
      worker_type_settings: {
        implementation: {
          reasoning_depth: 'high',
          additional_prompt: 'Keep APIs stable.',
          desired_instances: 2,
        },
      },
      project_report: {
        role_worker_backends: { architect: 'claude' },
        worker_backends_preflight: {
          codex: { ready: true },
          claude: { ready: false, hint: 'Sign in to Claude.' },
        },
      },
    },
    team_workspace: {
      execution_actors: [
        { id: 'worker-1', role: 'implementation', state: 'working', responsibility: 'Build the API', task_id: 'DEMO-1' },
        { id: 'worker-2', role: 'implementation', state: 'waiting', responsibility: 'Update the UI', task_id: 'DEMO-2' },
        { id: 'worker-3', role: 'architect', state: 'blocked', responsibility: 'Review boundaries', task_id: 'DEMO-3' },
      ],
    },
  };
}

function renderTeam(api = { getSettingsAgents: mocks.getAgents, getProjectSettings: vi.fn(), saveWorkerDefaults: mocks.saveWorkerDefaults }) {
  return render(
    <MemoryRouter initialEntries={['/projects/DEMO/team']}>
      <Routes>
        <Route element={<Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />}>
          <Route path="/projects/:projectKey/team" element={<TeamPage api={api} />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  mocks.projectState.mockReturnValue(stateFixture());
  mocks.getAgents.mockResolvedValue({
    agents: [
      { backend: 'codex', available: true, ready: true, models: ['gpt-5.4'], model_default: 'gpt-5.4' },
      { backend: 'claude', available: true, ready: false, models: ['claude-opus-4-5'], model_default: 'claude-opus-4-5' },
    ],
  });
  mocks.saveWorkerDefaults.mockResolvedValue({ ok: true });
  mocks.refresh.mockResolvedValue(stateFixture());
});

mocks.projectState.mockReturnValue(stateFixture());
mocks.getAgents.mockResolvedValue({ agents: [] });

test('groups multiple runtime instances under their Worker Type', () => {
  const rows = buildWorkerTypes(stateFixture(), []);
  const implementation = rows.find((row) => row.role === 'implementation');
  const architect = rows.find((row) => row.role === 'architect');

  expect(implementation.instances).toHaveLength(2);
  expect(implementation.status).toBe('active');
  expect(implementation.backend).toBe('codex');
  expect(implementation.reasoningDepth).toBe('high');
  expect(implementation.additionalPrompt).toBe('Keep APIs stable.');
  expect(implementation.desiredInstances).toBe(2);
  expect(architect.status).toBe('unavailable');
  expect(architect.model).toBe('claude-opus-4-5');
});

test('presents orchestrator as the singleton Task Assistant', async () => {
  const data = stateFixture();
  data.start_context.worker_roles = ['orchestrator', ...data.start_context.worker_roles];
  data.start_context.worker_type_settings.orchestrator = {
    reasoning_depth: 'high',
    additional_prompt: 'Keep decisions concise.',
    desired_instances: 6,
  };
  mocks.projectState.mockReturnValue(data);

  const manager = buildWorkerTypes(data, []).find((row) => row.role === 'orchestrator');
  expect(manager.label).toBe('Task Assistant');
  expect(manager.desiredInstances).toBe(1);

  const user = userEvent.setup();
  renderTeam();
  expect(screen.getByRole('heading', { name: 'Task Assistant' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Orchestrator' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Configure team' }));
  const managerRow = screen.getByTestId('team-configuration-orchestrator');
  expect(within(managerRow).getByLabelText('Desired instances')).toBeDisabled();
  expect(within(managerRow).getByText('Task Assistant is a single logical owner.')).toBeVisible();
});

test('renders one unified Team surface with Worker Types and real instances', async () => {
  renderTeam();

  expect(screen.getByRole('heading', { name: 'Team' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Configure team' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Implementation' })).toBeVisible();
  expect(screen.getByText('2 instances')).toBeVisible();
  expect(screen.getByText('Build the API')).toBeVisible();
  expect(screen.getByText('Update the UI')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'QA' })).toBeVisible();
});

test('edits all type-level settings in one modal', async () => {
  const user = userEvent.setup();
  renderTeam();

  const trigger = screen.getByRole('button', { name: 'Configure team' });
  await user.click(trigger);
  const dialog = screen.getByRole('dialog', { name: 'Configure team' });
  expect(within(dialog).getAllByLabelText('Runtime / provider')).toHaveLength(4);
  expect(within(dialog).getAllByLabelText('Model')).toHaveLength(4);
  expect(within(dialog).getAllByLabelText('Reasoning depth')).toHaveLength(4);
  expect(within(dialog).getAllByLabelText('Desired instances')).toHaveLength(4);
  expect(within(dialog).getAllByLabelText('Additional prompt')).toHaveLength(4);

  const runtimeControls = within(dialog).getAllByLabelText('Runtime / provider').slice(1);
  const modelControls = within(dialog).getAllByLabelText('Model').slice(1);
  const reasoningControls = within(dialog).getAllByLabelText('Reasoning depth').slice(1);
  const desiredControls = within(dialog).getAllByLabelText('Desired instances').slice(1);
  const promptControls = within(dialog).getAllByLabelText('Additional prompt').slice(1);
  await user.selectOptions(runtimeControls[2], 'claude');
  await user.selectOptions(modelControls[2], 'claude-opus-4-5');
  await user.selectOptions(reasoningControls[2], 'medium');
  await user.clear(desiredControls[2]);
  await user.type(desiredControls[2], '3');
  await user.type(promptControls[2], 'Run the regression suite.');
  await user.click(within(dialog).getByRole('button', { name: 'Save configuration' }));

  expect(mocks.saveWorkerDefaults).toHaveBeenCalledWith(
    'DEMO',
    'codex',
    { architect: 'claude', qa: 'claude' },
    { architect: 'claude:claude-opus-4-5', qa: 'claude:claude-opus-4-5' },
    {
      architect: { reasoning_depth: 'default', additional_prompt: '', desired_instances: 1 },
      implementation: { reasoning_depth: 'high', additional_prompt: 'Keep APIs stable.', desired_instances: 2 },
      qa: { reasoning_depth: 'medium', additional_prompt: 'Run the regression suite.', desired_instances: 3 },
    },
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

test('applies one configuration to multiple selected Worker Types', async () => {
  const user = userEvent.setup();
  renderTeam();

  await user.click(screen.getByRole('button', { name: 'Configure team' }));
  const dialog = screen.getByRole('dialog', { name: 'Configure team' });
  await user.click(within(dialog).getByRole('checkbox', { name: 'Select Implementation' }));
  await user.click(within(dialog).getByRole('checkbox', { name: 'Select QA' }));
  await user.selectOptions(within(dialog).getByLabelText('Batch runtime / provider'), 'claude');
  await user.selectOptions(within(dialog).getByLabelText('Batch model'), 'claude-opus-4-5');
  await user.selectOptions(within(dialog).getByLabelText('Batch reasoning depth'), 'high');
  await user.clear(within(dialog).getByLabelText('Batch desired instances'));
  await user.type(within(dialog).getByLabelText('Batch desired instances'), '3');
  await user.type(within(dialog).getByLabelText('Batch additional prompt'), 'Keep the patch focused.');
  await user.click(within(dialog).getByRole('button', { name: 'Apply to 2 types' }));
  await user.click(within(dialog).getByRole('button', { name: 'Save configuration' }));

  expect(mocks.saveWorkerDefaults).toHaveBeenCalledWith(
    'DEMO',
    'codex',
    { architect: 'claude', implementation: 'claude', qa: 'claude' },
    {
      architect: 'claude:claude-opus-4-5',
      implementation: 'claude:claude-opus-4-5',
      qa: 'claude:claude-opus-4-5',
    },
    {
      architect: { reasoning_depth: 'default', additional_prompt: '', desired_instances: 1 },
      implementation: { reasoning_depth: 'high', additional_prompt: 'Keep the patch focused.', desired_instances: 3 },
      qa: { reasoning_depth: 'high', additional_prompt: 'Keep the patch focused.', desired_instances: 3 },
    },
  );
});

test('closes the configuration modal with Escape and restores focus', async () => {
  const user = userEvent.setup();
  renderTeam();
  const trigger = screen.getByRole('button', { name: 'Configure team' });

  await user.click(trigger);
  expect(screen.getByRole('dialog')).toBeVisible();
  await user.keyboard('{Escape}');

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
