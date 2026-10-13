import { expect, test } from 'vitest';
import { mergeCatalog, refreshCatalog } from './catalogState';
import { runtimeOptions } from './teamModel';

test('refresh keeps names but invalidates previous model capabilities until current response', () => {
  const old = [{ backend: 'codex', models: ['m'], models_source: 'live', observed_at: 'old',
    model_capabilities: { m: { reasoning_depths: ['high'], reasoning_default: 'high' } } }];
  const loading = refreshCatalog(old);
  expect(loading[0].models).toEqual(['m']);
  expect(runtimeOptions(loading, 'codex', 'm').reasoningDepths).toEqual([]);
  const failed = mergeCatalog(loading, [{ backend: 'codex', models: [], models_source: 'unavailable' }]);
  expect(failed[0]).toMatchObject({ models: ['m'], stale: true, models_source: 'unavailable', observed_at: 'old' });
  const current = mergeCatalog(failed, [{ backend: 'codex', models: ['m'], models_source: 'live',
    model_capabilities: { m: { reasoning_depths: [], reasoning_default: undefined } } }]);
  expect(current[0].stale).toBe(false);
  expect(runtimeOptions(current, 'codex', 'm').reasoningDepths).toEqual([]);
});

test('provider progress leaves unrelated pending provider isolated', () => {
  const previous = refreshCatalog([{ backend: 'agy', models: ['old'] }]);
  const current = mergeCatalog(previous, [{ backend: 'codex', models: ['new'], models_source: 'live' }]);
  expect(current[0].discovery_status).toBe('loading');
  expect(current[1].discovery_status).toBe('complete');
});

test('reported model default is descriptive and explicit empty support overrides adapter fallback', () => {
  const agents = [{ backend: 'codex', models: ['m'], model_default: 'm', models_source: 'live', reasoning_depths: ['low'],
    model_capabilities: { m: { reasoning_depths: ['high'], reasoning_default: 'high' }, plain: { reasoning_depths: [] } } }];
  expect(runtimeOptions(agents, 'codex', '')).toMatchObject({ reasoningSource: 'model', reasoningDefault: 'high', reasoningDepths: ['high'] });
  expect(runtimeOptions(agents, 'codex', 'plain').reasoningDepths).toEqual([]);
});
