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
      { backend: 'codex', available: true, ready: true, models: ['gpt-5.4'], model_default: 'gpt-5.4' },
      { backend: 'claude', available: true, ready: false, models: ['claude-opus-4-5'], model_default: 'claude-opus-4-5' },
    ],
  });
  mocks.sendTeamCommands.mockResolvedValue({ ok: true, decision_id: 'dec-1', team: teamFixture() });
  mocks.getTeamEvents.mockResolvedValue({ events: [] });
  mocks.refresh.mockResolvedValue(stateFixture());
});

mocks.projectState.mockReturnValue(stateFixture());
mocks.getAgents.mockResolvedValue({ agents: [] });

test('builds a layered team model with roles, capability, and workers', () => {
  const team = buildTeamModel(stateFixture());
  expect(team.revision).toBe(4);
  // The model maps every payload role in order; the roster may be any size.
  expect(team.roles.map((role) => role.role)).toEqual(['implementation', 'qa', 'research', 'planner']);
  const implementation = team.roles[0];
  expect(implementation.workers).toHaveLength(2);
  expect(implementation.workers[0].runtimeLabel).toBe('codex · gpt-5.4');
  expect(team.manager.label).toBe('Task Assistant');
  expect(capabilityCounts(team.roles)).toEqual({ ready: 1, unstaffed: 1, degraded: 1, disabled: 1 });
});

test('renders roles and workers as separate layers with capability states', async () => {
  renderTeam();

  expect(screen.getByRole('heading', { name: 'Team' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Implementation' })).toBeVisible();
  expect(screen.getByText('Builder A')).toBeVisible();
  expect(screen.getByText('Builder B')).toBeVisible();
  const qa = screen.getByRole('heading', { name: 'QA' }).closest('article');
  expect(within(qa).getByText('Degraded')).toBeVisible();
  const research = screen.getByRole('heading', { name: 'Research' }).closest('article');
  expect(within(research).getByText('Unstaffed')).toBeVisible();
  const planner = screen.getByRole('heading', { name: 'Planner' }).closest('article');
  expect(within(planner).getByText('Disabled')).toBeVisible();
  // The legacy type-level configuration surface stays gone.
  expect(screen.queryByText('Desired instances')).not.toBeInTheDocument();
});

test('disabling a role goes through the control-plane command', async () => {
  const user = userEvent.setup();
  renderTeam();

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

  await user.click(screen.getByRole('button', { name: 'Pause Builder A' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'inactivate_worker', worker_id: 'implementation-a' }],
    expect.anything(),
  ));

  await user.click(screen.getByRole('button', { name: 'Delete Builder B; logical delete, history stays' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'delete_worker', worker_id: 'implementation-b' }],
    expect.anything(),
  ));
});

test('activating a paused worker emits activate command', async () => {
  const user = userEvent.setup();
  renderTeam();

  await user.click(screen.getByRole('button', { name: 'Activate Builder B' }));
  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'activate_worker', worker_id: 'implementation-b' }],
    expect.anything(),
  ));
});

test('edits worker identity and runtime into diff-based commands', async () => {
  const user = userEvent.setup();
  renderTeam();

  await user.click(screen.getByRole('button', { name: 'Edit Builder A' }));
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
  await user.selectOptions(within(dialog).getByLabelText('Runtime / provider'), 'codex');
  await user.selectOptions(within(dialog).getByLabelText('Model'), 'gpt-5.4');
  await user.click(within(dialog).getByRole('button', { name: 'Add worker' }));

  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [workerCreateCommand('research', { name: '', provider: 'codex', model: 'gpt-5.4', reasoningDepth: 'default', additionalPrompt: '' })],
    expect.anything(),
  ));
});

test('edits the Task Assistant runtime without making it a role', async () => {
  const user = userEvent.setup();
  renderTeam();

  const managerSection = screen.getByRole('heading', { name: 'Task Assistant' }).closest('section');
  await user.click(within(managerSection).getByRole('button', { name: 'Runtime' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).queryByLabelText('Role')).not.toBeInTheDocument();
  await user.selectOptions(within(dialog).getByLabelText('Runtime / provider'), 'claude');
  await user.click(within(dialog).getByRole('button', { name: 'Save runtime' }));

  await waitFor(() => expect(mocks.sendTeamCommands).toHaveBeenCalledWith(
    'DEMO',
    [{ kind: 'change_manager_runtime', provider: 'claude', model: '', reasoning_depth: 'high', additional_prompt: '' }],
    expect.anything(),
  ));
});

test('history panel groups the decision ledger', async () => {
  mocks.getTeamEvents.mockResolvedValue({
    events: [
      { id: 'e1', decision_id: 'dec-1', timestamp: '2026-09-05T10:00:00Z', actor: 'user', surface: 'team_ui', event: 'team_role.disabled', target: { kind: 'team_role', id: 'research' }, reason: 'Disable Research', cause: 'user_command' },
      { id: 'e2', decision_id: 'dec-1', timestamp: '2026-09-05T10:00:00Z', actor: 'system', surface: 'team_ui', event: 'worker.inactivated', target: { kind: 'worker', id: 'research-a' }, reason: '', cause: 'cascade' },
    ],
  });
  const user = userEvent.setup();
  renderTeam();

  await user.click(screen.getByRole('button', { name: 'History' }));
  const dialog = screen.getByRole('dialog');
  // One decision header for the shared decision id: both causal events stay
  // grouped, visible, and attributed. The raw ledger enum stays a backend
  // contract; the panel may translate event labels later.
  expect(within(dialog).getAllByText('dec-1')).toHaveLength(1);
  expect(within(dialog).getAllByRole('listitem')).toHaveLength(2);
  expect(within(dialog).getByText('Disable Research')).toBeVisible();
  expect(within(dialog).getByText(/research-a/)).toBeVisible();
  expect(within(dialog).getByText(/cascaded/)).toBeVisible();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('surfaces command failures to the user', async () => {
  mocks.sendTeamCommands.mockRejectedValue(new Error('The team control plane rejected this command.'));
  const user = userEvent.setup();
  renderTeam();

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

test('finds roles by worker runtime and filters setup needs without changing the team', async () => {
  const user = userEvent.setup();
  renderTeam();
  await user.type(screen.getByRole('searchbox', { name: 'Find a role, worker or model' }), 'gpt-5.4');
  expect(screen.getByRole('heading', { name: 'Implementation' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'QA' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Needs setup' }));
  expect(screen.getByText('No matches. Try another search or clear your filters.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Clear search' }));
  expect(screen.getByRole('heading', { name: 'QA' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Research' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Implementation' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Planner' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Disabled', exact: true }));
  expect(screen.getByRole('heading', { name: 'Planner' })).toBeVisible();
  expect(mocks.sendTeamCommands).not.toHaveBeenCalled();
});
