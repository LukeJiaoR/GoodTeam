import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { en } from '../../i18n/locales/en';
import ProviderSettings from './ProviderSettings';
import { fetchProviderModels, getProviders, getProviderUsage, updateProvider } from './providerApi';

vi.mock('./providerApi', () => ({
  getProviders: vi.fn(), getProviderUsage: vi.fn(), updateProvider: vi.fn(), fetchProviderModels: vi.fn(),
}));
const t = (key, values = {}) => Object.entries(values).reduce(
  (text, [name, value]) => text.replaceAll(`{{${name}}}`, value), en[key] || key,
);

test('fetches a model, saves and activates a provider, and preserves an undisclosed key when editing', async () => {
  const user = userEvent.setup();
  let state = { configured: true, revision: 0, providers: [], active: {} };
  getProviders.mockResolvedValue(state);
  getProviderUsage.mockResolvedValue({ totals: { invocations: 3, reported_invocations: 2, total_tokens: 210,
    input_tokens: 200, cached_input_tokens: 80, output_tokens: 10 }, by_model: [] });
  fetchProviderModels.mockResolvedValue({ models: ['model-a', 'model-b'] });
  updateProvider.mockImplementation(async (request) => {
    if (request.action === 'save') state = { ...state, revision: state.revision + 1, providers: [{
      id: 'provider-test', name: request.name, agent: 'codex', protocol: 'responses', base_url: request.base_url,
      model: request.model, has_api_key: true,
    }] };
    if (request.action === 'activate') state = { ...state, revision: state.revision + 1, active: { codex: request.id } };
    return state;
  });
  render(<ProviderSettings t={t} />);
  await screen.findByText('Usage reported for 2 of 3 invocations.');
  expect(screen.getByText('210')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Add provider' }));
  await user.type(screen.getByLabelText('Provider name'), 'Test API');
  await user.type(screen.getByLabelText('Base URL', { exact: false }), 'https://api.example.com/v1');
  await user.type(screen.getByLabelText('API Key', { exact: false }), 'fixture-only-key');
  await user.click(within(screen.getByRole('form', { name: 'Add provider' })).getByRole('button', { name: 'Fetch models' }));
  await user.selectOptions(await screen.findByLabelText('Choose a fetched model'), 'model-b');
  await user.click(screen.getByRole('button', { name: 'Save provider' }));
  await screen.findByText('Provider saved. Choose Use provider to activate it.');
  expect(updateProvider.mock.calls[0][0]).toMatchObject({ model: 'model-b', api_key: 'fixture-only-key' });
  const card = screen.getByRole('article', { name: 'Test API' });
  await user.click(within(card).getByRole('button', { name: 'Use provider' }));
  await screen.findByText('Provider switched. Existing jobs keep their captured connection.');
  expect(within(card).getByText('Active')).toBeVisible();
  await user.click(within(card).getByRole('button', { name: 'Edit provider' }));
  expect(screen.getByLabelText('API Key', { exact: false, selector: 'input[type="password"]' })).toHaveValue('');
  await user.click(screen.getByRole('button', { name: 'Save provider' }));
  await screen.findByText('Provider saved. Choose Use provider to activate it.');
  expect(updateProvider.mock.lastCall[0]).not.toHaveProperty('api_key');
});

test('selects an embedding model on a saved connection without switching the coding provider', async () => {
  const user = userEvent.setup();
  let state = { configured: true, revision: 3, active: { codex: 'coding' }, embeddings: null,
    providers: [{ id: 'coding', name: 'Coding API', model: 'coding-model', base_url: 'https://coding.example/v1' },
      { id: 'local', name: 'Local embeddings', model: '', base_url: 'http://localhost:11434/v1' }] };
  getProviders.mockResolvedValue(state);
  getProviderUsage.mockResolvedValue({ totals: { invocations: 0, reported_invocations: 0 }, by_model: [] });
  fetchProviderModels.mockResolvedValue({ models: ['multilingual-embedding'] });
  updateProvider.mockImplementation(async (request) => {
    state = { ...state, revision: state.revision + 1, embeddings: request.provider_id
      ? { provider_id: request.provider_id, model: request.model } : null };
    return state;
  });
  render(<ProviderSettings t={t} />);
  let form = await screen.findByRole('form', { name: 'Project memory retrieval' });
  await user.selectOptions(within(form).getByLabelText('Memory provider'), 'local');
  await user.click(within(form).getByRole('button', { name: 'Fetch models' }));
  expect(fetchProviderModels).toHaveBeenLastCalledWith({ id: 'local' });
  await user.type(within(form).getByLabelText('Embedding model'), 'multilingual-embedding');
  await user.click(within(form).getByRole('button', { name: 'Save memory retrieval' }));
  await screen.findByText(en['providers.memorySaved']);
  expect(updateProvider.mock.lastCall[0]).toEqual({ action: 'configure_embeddings', expected_revision: 3,
    provider_id: 'local', model: 'multilingual-embedding' });
  expect(within(screen.getByRole('article', { name: 'Coding API' })).getByText('Active')).toBeVisible();
  expect(within(screen.getByRole('article', { name: 'Local embeddings' })).getByRole('button', { name: 'Delete' })).toBeDisabled();
  form = screen.getByRole('form', { name: 'Project memory retrieval' });
  await user.selectOptions(within(form).getByLabelText('Memory provider'), '');
  await user.click(within(form).getByRole('button', { name: 'Save memory retrieval' }));
  await screen.findByText(en['providers.memorySaved']);
  expect(updateProvider.mock.lastCall[0].provider_id).toBe('');
  expect(within(screen.getByRole('article', { name: 'Coding API' })).getByText('Active')).toBeVisible();
});
