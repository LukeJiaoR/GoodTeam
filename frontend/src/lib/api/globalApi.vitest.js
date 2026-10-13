import { expect, test, vi } from 'vitest';
import { requestJson } from './client';
import { getAgents } from './globalApi';

vi.mock('./client', () => ({ requestJson: vi.fn() }));

test('fast provider is published before slow response; abort suppresses late progress', async () => {
  const pending = {};
  requestJson.mockImplementation((url) => new Promise((resolve) => { pending[url.split('=')[1]] = resolve; }));
  const controller = new AbortController();
  const progress = vi.fn();
  const request = getAgents({ signal: controller.signal, onProgress: progress });
  pending.codex({ agents: [{ backend: 'codex', models: ['current'] }] });
  await vi.waitFor(() => expect(progress).toHaveBeenCalledTimes(1));
  expect(progress).toHaveBeenLastCalledWith([{ backend: 'codex', models: ['current'] }]);
  controller.abort();
  for (const provider of ['agy', 'claude', 'gemini', 'opencode', 'pi']) {
    pending[provider]({ agents: [{ backend: provider, models: ['late'] }] });
  }
  await request;
  expect(progress).toHaveBeenCalledTimes(1);
});

test('provider failure is explicit and does not lose other catalogs', async () => {
  requestJson.mockImplementation((url) => url.endsWith('=agy') ? Promise.reject(new Error('timeout'))
    : Promise.resolve({ agents: [{ backend: url.split('=')[1], models_source: 'live', models: ['m'] }] }));
  const result = await getAgents();
  expect(result.agents.find((agent) => agent.backend === 'agy')).toMatchObject({ models_source: 'unavailable', discovery_status: 'failed' });
  expect(result.agents.find((agent) => agent.backend === 'codex').models).toEqual(['m']);
});
