import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import TeamPage from './TeamPage';
import { buildTeamModel, capabilityCounts, workerCreateCommand, workerEditCommands } from './teamModel';

const mocks = vi.hoisted(() => ({
  getAgents: vi.fn(),
  sendTeamCommands: vi.fn(),
  getTeamEvents: vi.fn(),
  taskAssistantAction: vi.fn(),
  projectState: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('../runs/useProjectState', () => ({
  default: () => ({ data: mocks.projectState(), error: null, refresh: mocks.refresh, status: 'ready' }),
}));

function teamFixture() {
  return {
    schema_version: 'team_capability_view.v1',
    revision: 4,
    roles: [
      {
        role: 'implementation',
        label: 'Implementation',
        state: 'enabled',
        capability: 'ready',
        workers: [
          { id: 'implementation-a', name: 'Builder A', lifecycle: 'active', dispatchable: true, provider: 'codex', model: 'gpt-5.4', reasoning_depth: 'high', additional_prompt: 'Keep APIs stable.' },
          { id: 'implementation-b', name: 'Builder B', lifecycle: 'inactive', dispatchable: false, provider: 'claude', model: '', reasoning_depth: 'default', additional_prompt: '' },
        ],
      },
      {
        role: 'qa',
        label: 'QA',
        state: 'enabled',
        capability: 'degraded',
        workers: [
          { id: 'qa-a', name: 'QA A', lifecycle: 'inactive', dispatchable: false, provider: 'fake', model: '', reasoning_depth: 'default', additional_prompt: '' },
        ],
      },
      {
        role: 'research',
        label: 'Research',
        state: 'enabled',
        capability: 'unstaffed',
        workers: [],
      },
      {
        role: 'planner',
        label: 'Planner',
        state: 'disabled',
        capability: 'disabled',
        workers: [],
      },
    ],
    manager: {
      role: 'orchestrator',
      label: 'Task Assistant',
      runtime: { provider: 'codex', model: 'gpt-5.4', reasoning_depth: 'high', additional_prompt: '' },
    },
  };
}

function stateFixture() {
  return {
    active_run: { run_id: 'run-1' },
    team: teamFixture(),
    start_context: {
      project_name: 'Demo project',
      worker_backend_options: ['codex', 'claude', 'fake'],
    },
  };
}

function renderTeam(api = {
  getSettingsAgents: mocks.getAgents,
  getProjectSettings: mocks.projectState,
  sendTeamCommands: mocks.sendTeamCommands,
  getTeamEvents: mocks.getTeamEvents,
  taskAssistantAction: mocks.taskAssistantAction,
}) {
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
      { backend: 'codex', available: true, ready: true, models: ['gpt-5.4'], model_default: 'gpt-5.4', reasoning_depths: ['low', 'medium', 'high', 'xhigh'] },
      { backend: 'claude', available: true, ready: false, models: ['claude-opus-4-5'], model_default: 'claude-opus-4-5', reasoning_depths: ['low', 'medium', 'high'] },
    ],
  });
  mocks.sendTeamCommands.mockResolvedValue({ ok: true, decision_id: 'dec-1', team: teamFixture() });
  mocks.getTeamEvents.mockResolvedValue({ events: [] });
  mocks.refresh.mockResolvedValue(stateFixture());
});

mocks.projectState.mockReturnValue(stateFixture());
mocks.getAgents.mockResolvedValue({ agents: [] });

test('saves and checks the assistant connection inside runtime configuration', async () => {
  const user = userEvent.setup();
  const state = stateFixture();
  state.task_assistant_session = {
    runtime: { provider: 'codex', model: 'gpt-5.4', reasoning_depth: 'high', status: 'unverified' },
  };
  mocks.projectState.mockReturnValue(state);
  let completeCheck;
  mocks.taskAssistantAction.mockImplementation(() => new Promise((resolve) => { completeCheck = resolve; }));
  renderTeam();
  const configuration = screen.getByRole('region', { name: 'Task Assistant' });
  expect(within(configuration).queryByRole('button', { name: 'Check connection' })).not.toBeInTheDocument();
  expect(mocks.taskAssistantAction).not.toHaveBeenCalled();
  mocks.sendTeamCommands.mockResolvedValue({ ok: true });
  await user.click(within(configuration).getByRole('button', { name: 'Configure assistant' }));
  const dialog = screen.getByRole('dialog');
  const check = within(dialog).getByRole('button', { name: 'Save and check connection' });
  expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument();
  await user.click(check);
  expect(mocks.sendTeamCommands).toHaveBeenCalledOnce();
  expect(mocks.taskAssistantAction).toHaveBeenCalledExactlyOnceWith('DEMO', {
    action: 'check_runtime', confirmed: true, message_id: expect.any(String),
  });
  expect(check).toBeDisabled();
  expect(within(dialog).getByLabelText('Model')).toBeDisabled();
  expect(within(dialog).getByText('Checking connection')).toBeVisible();
  completeCheck({ task_assistant_session: { runtime: { ...state.task_assistant_session.runtime, status: 'verified' } } });
  await waitFor(() => expect(within(dialog).getByText('Connection verified')).toBeVisible());
  expect(mocks.refresh).toHaveBeenCalledOnce();
  expect(dialog).toBeVisible();
  expect(check).toBeEnabled();
});

test('builds a layered team model with roles, capability, and workers', () => {
  const team = buildTeamModel(stateFixture());
  expect(team.revision).toBe(4);
  // The model maps every payload role in order; the roster may be any size.
  expect(team.roles.map((role) => role.role)).toEqual(['implementation', 'qa', 'research', 'planner']);
  const implementation = team.roles[0];
  expect(implementation.workers).toHaveLength(2);
  expect(implementation.workers[0].runtimeLabel).toBe('codex · gpt-5.4');
  expect(implementation.workers[0].role).toBe('implementation');
  expect(team.manager.label).toBe('Task Assistant');
  expect(capabilityCounts(team.roles)).toEqual({ ready: 1, unstaffed: 1, degraded: 1, disabled: 1 });
});

test('configures selected roles in one decision while preserving worker identity and lifecycle', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Bulk configure' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).queryByRole('checkbox', { name: /Planner/ })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('checkbox', { name: /Task Assistant/ })).not.toBeInTheDocument();
  await user.click(within(dialog).getByRole('checkbox', { name: 'Select Quality & delivery' }));
  await user.selectOptions(within(dialog).getByLabelText('Model'), 'gpt-5.4');
  await user.selectOptions(within(dialog).getByLabelText('Reasoning depth'), 'medium');
  expect(within(dialog).getByText('Update 2 workers · Add 1 workers')).toBeVisible();
  await user.click(within(dialog).getByRole('button', { name: 'Apply configuration' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledOnce());
  expect(mocks.sendTeamCommands.mock.calls[0][1]).toEqual([
    { kind: 'change_worker_runtime', worker_id: 'implementation-a', reasoning_depth: 'medium' },
    { kind: 'change_worker_runtime', worker_id: 'implementation-b', provider: 'codex', model: 'gpt-5.4', reasoning_depth: 'medium' },
    { kind: 'initialize_worker', role: 'research', ensure_role_enabled: false, runtime: { provider: 'codex', model: 'gpt-5.4', reasoning_depth: 'medium' } },
  ]);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Worker settings saved.')).toBeVisible();
});

test('bulk configuration requires a selection and does not save unchanged workers', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Bulk configure' }));
  const dialog = screen.getByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Clear selection' }));
  expect(within(dialog).getByRole('button', { name: 'Apply configuration' })).toBeDisabled();
  await user.click(within(dialog).getByRole('checkbox', { name: 'Select Quality & delivery' }));
  await user.selectOptions(within(dialog).getByLabelText('Agent'), 'fake');
  expect(within(dialog).getByText('These workers already use this configuration.')).toBeVisible();
  expect(within(dialog).getByRole('button', { name: 'Apply configuration' })).toBeDisabled();
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(mocks.sendTeamCommands).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Bulk configure' })).toHaveFocus();
});

test('configures a responsibility group without requiring the user to open its roles', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Configure Design & implementation' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getAllByRole('checkbox')).toHaveLength(1);
  expect(within(dialog).getByRole('checkbox', { name: 'Select Design & implementation' })).toBeChecked();
  await user.selectOptions(within(dialog).getByLabelText('Agent'), 'fake');
  await user.click(within(dialog).getByRole('button', { name: 'Apply configuration' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledOnce());
  const commands = mocks.sendTeamCommands.mock.calls[0][1];
  expect(commands.map((command) => command.worker_id)).toEqual(['implementation-a', 'implementation-b']);
  expect(commands.every((command) => command.kind === 'change_worker_runtime')).toBe(true);
});

test('prefills an existing group configuration without silently resetting its model or reasoning', async () => {
  const state = stateFixture();
  state.team.roles = [{ ...state.team.roles[0], workers: [state.team.roles[0].workers[0]] }];
  mocks.projectState.mockReturnValue(state);
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Configure Design & implementation' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Agent')).toHaveValue('codex');
  expect(within(dialog).getByLabelText('Model')).toHaveValue('gpt-5.4');
  expect(within(dialog).getByLabelText('Reasoning depth')).toHaveValue('high');
  expect(within(dialog).getByRole('button', { name: 'Apply configuration' })).toBeDisabled();
});

test('can expand a selected responsibility and choose an individual role', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Bulk configure' }));
  const dialog = screen.getByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Clear selection' }));
  await user.click(within(dialog).getByRole('button', { name: 'Show roles in Requirements & planning' }));
  expect(within(dialog).queryByRole('checkbox', { name: /Planner/ })).not.toBeInTheDocument();
  await user.click(within(dialog).getByRole('checkbox', { name: /Research/ }));
  expect(within(dialog).getByRole('checkbox', { name: 'Select Requirements & planning' })).toBeChecked();
  await user.click(within(dialog).getByRole('button', { name: 'Apply configuration' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledOnce());
  expect(mocks.sendTeamCommands.mock.calls[0][1]).toEqual([
    { kind: 'initialize_worker', role: 'research', ensure_role_enabled: false, runtime: { provider: 'codex', model: 'gpt-5.6-sol' } },
  ]);
});

test('uses the project backend explicitly when creating a worker', async () => {
  const state = stateFixture();
  state.start_context.worker_backend = { mode: 'fake' };
  mocks.projectState.mockReturnValue(state);
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Requirements & planning' }));
  const research = screen.getByRole('heading', { name: 'Research' }).closest('article');
  await user.click(within(research).getByRole('button', { name: 'Add', exact: true }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Agent')).toHaveValue('fake');
  await user.click(within(dialog).getByRole('button', { name: 'Add worker' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledOnce());
  expect(mocks.sendTeamCommands.mock.calls[0][1]).toEqual([
    { kind: 'initialize_worker', role: 'research', ensure_role_enabled: true, runtime: { provider: 'fake' } },
  ]);
});

test('new workers default to Codex with an explicit GPT-5.6 Sol model even without a catalog', async () => {
  mocks.getAgents.mockResolvedValue({ agents: [] });
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Add worker' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Agent')).toHaveValue('codex');
  expect(within(dialog).getByLabelText('Model')).toHaveValue('gpt-5.6-sol');
  await user.selectOptions(within(dialog).getByLabelText('Role'), 'research');
  await user.click(within(dialog).getByRole('button', { name: 'Add worker' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledOnce());
  expect(mocks.sendTeamCommands.mock.calls[0][1]).toEqual([
    { kind: 'initialize_worker', role: 'research', ensure_role_enabled: true, runtime: { provider: 'codex', model: 'gpt-5.6-sol' } },
  ]);
});

test('allows a custom model after discovery fails, retains the draft on save failure, and retries idempotently', async () => {
  mocks.getAgents.mockRejectedValue(new Error('Discovery unavailable'));
  mocks.sendTeamCommands.mockRejectedValueOnce(new Error('Save could not be confirmed.')).mockResolvedValue({ ok: true });
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));
  await user.click(screen.getByRole('button', { name: 'Edit Implementation / Builder A (implementation-a)' }));
  const dialog = screen.getByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Enter model ID' }));
  await user.clear(within(dialog).getByLabelText('Model'));
  await user.type(within(dialog).getByLabelText('Model'), ' custom/model ');
  await user.click(within(dialog).getByRole('button', { name: 'Save worker' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Save could not be confirmed.');
  expect(within(dialog).getByLabelText('Model')).toHaveValue(' custom/model ');
  await user.click(within(dialog).getByRole('button', { name: 'Save worker' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledTimes(2));
  const [first, second] = mocks.sendTeamCommands.mock.calls;
  expect(first[1]).toEqual([{ kind: 'change_worker_runtime', worker_id: 'implementation-a', model: 'custom/model', reasoning_depth: 'default' }]);
  expect(second[2].commandKey).toBe(first[2].commandKey);
  expect(first[2].commandKey).toEqual(expect.any(String));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('keeps a saving bulk dialog open and prevents duplicate submissions', async () => {
  let finishSave;
  mocks.sendTeamCommands.mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve; }));
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Bulk configure' }));
  const dialog = screen.getByRole('dialog');
  await user.dblClick(within(dialog).getByRole('button', { name: 'Apply configuration' }));
  expect(mocks.sendTeamCommands).toHaveBeenCalledOnce();
  expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled();
  await user.keyboard('{Escape}');
  expect(dialog).toBeVisible();
  finishSave({ ok: true });
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

test('disabling a role goes through the control-plane command', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));

  const implementation = screen.getByRole('heading', { name: 'Implementation' }).closest('article');
  await user.click(within(implementation).getByRole('button', { name: 'Disable' }));

  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'disable_role', role: 'implementation' }],
    expect.objectContaining({ reason: expect.any(String) }),
  ));
  expect(mocks.refresh).toHaveBeenCalled();
});

test('pausing and deleting a worker emits lifecycle commands', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));

  await user.click(screen.getByRole('button', { name: 'Pause Implementation / Builder A (implementation-a)' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'inactivate_worker', worker_id: 'implementation-a' }],
    expect.anything(),
  ));

  await user.click(screen.getByRole('button', { name: 'Delete Implementation / Builder B (implementation-b); logical delete, history stays' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'delete_worker', worker_id: 'implementation-b' }],
    expect.anything(),
  ));
});

test('activating a paused worker emits activate command', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));

  await user.click(screen.getByRole('button', { name: 'Activate Implementation / Builder B (implementation-b)' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'activate_worker', worker_id: 'implementation-b' }],
    expect.anything(),
  ));
});

test('edits worker identity and runtime into diff-based commands', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));

  await user.click(screen.getByRole('button', { name: 'Edit Implementation / Builder A (implementation-a)' }));
  const dialog = screen.getByRole('dialog');
  await user.clear(within(dialog).getByLabelText('Name'));
  await user.type(within(dialog).getByLabelText('Name'), 'Builder Prime');
  await user.selectOptions(within(dialog).getByLabelText('Reasoning depth'), 'medium');
  await user.click(within(dialog).getByRole('button', { name: 'Save worker' }));

  // Exactly the diffed mutations, as one decision; the helper's emission
  // order is a reviewability choice, not a domain contract.
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalled());
  const [, commands] = mocks.sendTeamCommands.mock.calls.at(-1);
  expect(commands).toHaveLength(2);
  expect(commands).toEqual(expect.arrayContaining([
    { kind: 'rename_worker', worker_id: 'implementation-a', name: 'Builder Prime' },
    { kind: 'change_worker_runtime', worker_id: 'implementation-a', reasoning_depth: 'medium' },
  ]));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('adds a worker with an explicit role and runtime', async () => {
  const user = userEvent.setup();
  renderTeam();

  await user.click(screen.getByRole('button', { name: 'Add worker' }));
  const dialog = screen.getByRole('dialog');
  await user.selectOptions(within(dialog).getByLabelText('Role'), 'research');
  await user.selectOptions(within(dialog).getByLabelText('Agent'), 'codex');
  await user.selectOptions(within(dialog).getByLabelText('Model'), 'gpt-5.4');
  await user.click(within(dialog).getByRole('button', { name: 'Add worker' }));

  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [workerCreateCommand('research', { name: '', provider: 'codex', model: 'gpt-5.4', reasoningDepth: 'default', additionalPrompt: '' })],
    expect.anything(),
  ));
});

test('loads the inherited agent models and follows each model effort catalog', async () => {
  mocks.getAgents.mockResolvedValue({ agents: [{
    backend: 'codex', available: true, models_source: 'live',
    model_default: 'model-deep', models: ['model-deep', 'model-light', 'model-plain'],
    reasoning_depths: ['low', 'medium', 'high', 'xhigh'],
    model_capabilities: {
      'model-deep': { reasoning_depths: ['high', 'xhigh', 'max', 'ultra'] },
      'model-light': { reasoning_depths: ['none'] },
      'model-plain': { reasoning_depths: [] },
    },
  }] });
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Add worker' }));
  const dialog = screen.getByRole('dialog');
  const model = within(dialog).getByLabelText('Model');
  const reasoning = within(dialog).getByLabelText('Reasoning depth');
  expect(within(model).getByRole('option', { name: 'Agent default (model-deep)' })).toBeInTheDocument();
  expect(within(model).getByRole('option', { name: 'model-deep', exact: true })).toBeInTheDocument();
  await user.selectOptions(model, '');
  await user.selectOptions(reasoning, 'ultra');
  await user.selectOptions(model, 'model-light');
  expect(reasoning).toHaveValue('default');
  expect([...reasoning.options].map((item) => item.value)).toEqual(['default', 'none']);
  await user.selectOptions(model, 'model-plain');
  expect(reasoning).toBeDisabled();
  await user.selectOptions(model, 'model-deep');
  await user.selectOptions(reasoning, 'ultra');
  await user.selectOptions(within(dialog).getByLabelText('Role'), 'research');
  await user.click(within(dialog).getByRole('button', { name: 'Add worker' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith('DEMO', [{
    kind: 'initialize_worker', role: 'research', ensure_role_enabled: true,
    runtime: { provider: 'codex', model: 'model-deep', reasoning_depth: 'ultra' },
  }], expect.anything()));
});

test('refreshes model discovery and resets unsupported effort on provider changes', async () => {
  mocks.getAgents.mockResolvedValue({ agents: [
    { backend: 'codex', aliases: ['codex'], models: ['gpt-5.4'], model_default: 'gpt-5.4', reasoning_depths: ['high', 'xhigh'] },
    { backend: 'gemini', aliases: ['gemini'], models: ['auto'], model_default: 'auto', reasoning_depths: [] },
  ] });
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));
  await user.click(screen.getByRole('button', { name: 'Edit Implementation / Builder A (implementation-a)' }));
  const dialog = screen.getByRole('dialog');
  await user.click(await within(dialog).findByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(mocks.getAgents).toHaveBeenCalledTimes(2));
  await user.selectOptions(within(dialog).getByLabelText('Reasoning depth'), 'xhigh');
  await user.selectOptions(within(dialog).getByLabelText('Agent'), 'gemini');
  expect(within(dialog).getByLabelText('Reasoning depth')).toHaveValue('default');
  expect(within(dialog).getByLabelText('Reasoning depth')).toBeDisabled();
});

test('failed refresh drops the previous catalog instead of serving stale models', async () => {
  mocks.getAgents.mockResolvedValueOnce({ agents: [{
    backend: 'codex', available: true, models: ['live-only-model'], model_default: 'live-only-model',
    model_capabilities: { 'live-only-model': { reasoning_depths: ['ultra'] } },
  }] }).mockResolvedValue({ agents: [{
    backend: 'codex', available: true, models: [], models_source: 'unavailable', model_capabilities: {},
  }] });
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Add worker' }));
  const dialog = screen.getByRole('dialog');
  await user.click(await within(dialog).findByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(mocks.getAgents).toHaveBeenCalledTimes(2));
  expect([...within(dialog).getByLabelText('Model').options].map((item) => item.value)).toEqual(['', 'gpt-5.6-sol']);
  expect(within(dialog).getByText('Model discovery is unavailable. Refresh the list or enter a model ID.')).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Reasoning depth')).toBeDisabled();
});

test('edits the Task Assistant runtime without making it a role', async () => {
  const user = userEvent.setup();
  mocks.taskAssistantAction.mockResolvedValue({ task_assistant_session: { runtime: { status: 'verified' } } });
  renderTeam();

  const managerSection = screen.getByRole('heading', { name: 'Task Assistant' }).closest('section');
  await user.click(within(managerSection).getByRole('button', { name: 'Configure assistant' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).queryByLabelText('Role')).not.toBeInTheDocument();
  await user.selectOptions(within(dialog).getByLabelText('Agent'), 'claude');
  await user.click(within(dialog).getByRole('button', { name: 'Save and check connection' }));

  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'change_manager_runtime', provider: 'claude', model: '', reasoning_depth: 'high', additional_prompt: '' }],
    expect.anything(),
  ));
});

test('surfaces command failures to the user', async () => {
  mocks.sendTeamCommands.mockRejectedValue(new Error('The team control plane rejected this command.'));
  const user = userEvent.setup();
  renderTeam();
  await user.click(screen.getByRole('button', { name: 'Show roles in Design & implementation' }));

  const implementation = screen.getByRole('heading', { name: 'Implementation' }).closest('article');
  await user.click(within(implementation).getByRole('button', { name: 'Disable' }));
  // The failure must be announced accessibly, never silently swallowed.
  // The exact copy (or a friendly mapping of an internal error code) is free
  // to evolve.
  const alert = await screen.findByRole('alert');
  expect(alert).toBeVisible();
  expect(within(alert).getByText(/rejected this command/)).toBeVisible();
});

test('worker edit commands diff identity, role, runtime, and lifecycle', () => {
  const worker = {
    id: 'implementation-a',
    name: 'Builder A',
    role: 'implementation',
    lifecycle: 'inactive',
    provider: 'codex',
    model: '',
    reasoningDepth: 'default',
    additionalPrompt: '',
  };
  const commands = workerEditCommands(worker, {
    name: 'Builder A2',
    role: 'qa',
    lifecycle: 'active',
    provider: 'claude',
    model: 'claude-opus-4-5',
    reasoningDepth: 'high',
    additionalPrompt: 'Be brief.',
  });
  // The command *set* is the contract: exactly the four diffed mutations and
  // nothing else. The commands run as one domain transaction where this
  // order is not load-bearing, so the helper's emission order stays free to
  // change for reviewability.
  expect(commands).toHaveLength(4);
  expect(commands).toEqual(expect.arrayContaining([
    { kind: 'rename_worker', worker_id: 'implementation-a', name: 'Builder A2' },
    { kind: 'change_worker_role', worker_id: 'implementation-a', role: 'qa' },
    { kind: 'change_worker_runtime', worker_id: 'implementation-a', provider: 'claude', model: 'claude-opus-4-5', reasoning_depth: 'high', additional_prompt: 'Be brief.' },
    { kind: 'activate_worker', worker_id: 'implementation-a' },
  ]));
});


test('opens the shared Skill Library from Team without changing role state', async () => {
  const user = userEvent.setup();
  const api = {
    getSettingsAgents: mocks.getAgents,
    getProjectSettings: mocks.projectState,
    sendTeamCommands: mocks.sendTeamCommands,
    getTeamEvents: mocks.getTeamEvents,
    taskAssistantAction: mocks.taskAssistantAction,
    getSkillLibrary: vi.fn().mockResolvedValue({ schema_version: 'skill_library.v1', skills: [] }),
    previewLocalSkill: vi.fn(),
    installLocalSkill: vi.fn(),
  };
  renderTeam(api);

  await user.click(screen.getByRole('button', { name: 'Skills' }));
  expect(await screen.findByRole('dialog', { name: 'Skills' })).toBeVisible();
  expect(api.getSkillLibrary).toHaveBeenCalledOnce();
  expect(mocks.sendTeamCommands).not.toHaveBeenCalled();
  expect(screen.getByText('No Skills installed yet.')).toBeVisible();
});
