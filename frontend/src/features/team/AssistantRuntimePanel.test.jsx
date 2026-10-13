import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import AssistantRuntimePanel from './AssistantRuntimePanel';

const t = (key, params) => translate('en', key, params);
const data = {
  team: { roles: [], manager: { runtime: {
    provider: 'codex', model: 'saved-model', reasoning_depth: 'high', additional_prompt: 'Keep APIs stable.',
  } } },
  start_context: { worker_backend_options: ['codex', 'pi'], worker_backend: { mode: 'codex' } },
  task_assistant_session: { runtime: { status: 'unverified' } },
};
const catalog = { agents: [{
  backend: 'codex', available: true, ready: true, hint: 'Local CLI and credential presence detected.',
  models: ['saved-model', 'old-catalog-model'], model_default: 'old-catalog-model', models_source: 'live',
  reasoning_depths: ['high', 'xhigh'],
}] };

function setup(apiOverrides = {}) {
  const api = {
    getSettingsAgents: vi.fn().mockResolvedValue(catalog),
    sendTeamCommands: vi.fn().mockResolvedValue({ ok: true }),
    taskAssistantAction: vi.fn().mockResolvedValue({ task_assistant_session: { runtime: { status: 'verified' } } }),
    ...apiOverrides,
  };
  const props = { data, projectKey: 'DEMO', onClose: vi.fn(), refresh: vi.fn().mockResolvedValue(data), t, api };
  const view = render(<AssistantRuntimePanel {...props} />);
  return { ...view, props, api, user: userEvent.setup() };
}

function modelOptions() {
  return [...screen.getByLabelText('Model').options].map((option) => option.value);
}

test('invalidates a refreshing and failed catalog without changing the draft or running a connection check', async () => {
  let failRefresh;
  const getSettingsAgents = vi.fn().mockResolvedValueOnce(catalog)
    .mockImplementationOnce(() => new Promise((resolve, reject) => { failRefresh = reject; }))
    .mockResolvedValue({ agents: [{ ...catalog.agents[0], models: ['new-catalog-model'], model_default: 'new-catalog-model' }] });
  const { user, api } = setup({ getSettingsAgents });
  await waitFor(() => expect(modelOptions()).toContain('old-catalog-model'));
  await user.selectOptions(screen.getByLabelText('Reasoning depth'), 'xhigh');
  await user.clear(screen.getByLabelText('Additional instructions'));
  await user.type(screen.getByLabelText('Additional instructions'), 'Preserve this unsaved instruction.');
  await user.click(screen.getByRole('button', { name: 'Refresh models' }));
  expect(modelOptions()).toEqual(['', 'saved-model', 'old-catalog-model']);
  expect(screen.getByRole('button', { name: 'Loading models…' })).toBeDisabled();
  expect(screen.getByLabelText('Reasoning depth')).toHaveValue('xhigh');
  expect(screen.queryByText(/Local CLI and credential presence detected/)).not.toBeInTheDocument();
  await act(async () => { failRefresh(new Error('Discovery request failed')); });
  expect(await screen.findByRole('alert')).toHaveTextContent('Discovery request failed');
  expect(modelOptions()).toEqual(['', 'saved-model', 'old-catalog-model']);
  expect(screen.getByLabelText('Reasoning depth')).toHaveValue('xhigh');
  expect(screen.getByLabelText('Additional instructions')).toHaveValue('Preserve this unsaved instruction.');
  expect(api.sendTeamCommands).not.toHaveBeenCalled();
  expect(api.taskAssistantAction).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(modelOptions()).toEqual(['', 'saved-model', 'new-catalog-model']));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Model')).toHaveValue('saved-model');
  expect(screen.getByLabelText('Reasoning depth')).toHaveValue('xhigh');
  expect(api.taskAssistantAction).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Save and check connection' }));
  await waitFor(() => expect(api.taskAssistantAction).toHaveBeenCalledOnce());
  expect(api.sendTeamCommands).toHaveBeenCalledExactlyOnceWith('DEMO', [{
    kind: 'change_manager_runtime', provider: 'codex', model: 'saved-model', reasoning_depth: 'xhigh',
    additional_prompt: 'Preserve this unsaved instruction.',
  }], { reason: 'Update Task Assistant runtime' });
});

test.each([
  { agents: [] },
  { agents: null },
  { agents: { backend: 'codex' } },
  null,
])('marks previous discovery stale after an empty or malformed response: %j', async (payload) => {
  const getSettingsAgents = vi.fn().mockResolvedValueOnce(catalog).mockResolvedValueOnce(payload);
  const { user, api } = setup({ getSettingsAgents });
  await waitFor(() => expect(modelOptions()).toContain('old-catalog-model'));
  await user.click(screen.getByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh models' })).toBeEnabled());
  expect(modelOptions()).toEqual(['', 'saved-model', 'old-catalog-model']);
  expect(screen.getByLabelText('Reasoning depth')).toHaveValue('high');
  expect(screen.getByLabelText('Additional instructions')).toHaveValue('Keep APIs stable.');
  expect(api.sendTeamCommands).not.toHaveBeenCalled();
  expect(api.taskAssistantAction).not.toHaveBeenCalled();
});

test('does not accept an obsolete discovery response after the request source changes', async () => {
  let resolveOld;
  const oldApi = { getSettingsAgents: vi.fn(() => new Promise((resolve) => { resolveOld = resolve; })) };
  const { props, rerender } = setup(oldApi);
  const newApi = { ...props.api, getSettingsAgents: vi.fn().mockResolvedValue({ agents: [{
    ...catalog.agents[0], models: ['current-model'],
  }] }) };
  rerender(<AssistantRuntimePanel {...props} api={newApi} />);
  await waitFor(() => expect(modelOptions()).toEqual(['', 'saved-model', 'current-model']));
  await act(async () => { resolveOld(catalog); });
  expect(oldApi.getSettingsAgents.mock.calls[0][0].signal.aborted).toBe(true);
  expect(modelOptions()).toEqual(['', 'saved-model', 'current-model']);
});

test('provider diagnostic text cannot create executable markup or an action', async () => {
  const hint = '<img src=x onerror="alert(1)"><a href="javascript:alert(1)">Log in</a>';
  const { api } = setup({ getSettingsAgents: vi.fn().mockResolvedValue({ agents: [{ ...catalog.agents[0], hint }] }) });
  const diagnostic = await screen.findByText((content) => content.includes(hint));
  expect(diagnostic.querySelector('img, a, script')).toBeNull();
  expect(within(screen.getByRole('dialog')).queryByRole('button', { name: 'Log in' })).not.toBeInTheDocument();
  expect(api.taskAssistantAction).not.toHaveBeenCalled();
});
