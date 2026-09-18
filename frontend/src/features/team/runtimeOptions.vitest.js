import { describe, expect, it } from 'vitest';
import { changeRuntimeSelection, runtimeOptions } from './teamModel';

const agents = [{ backend: 'codex', aliases: ['codex-cli'], model_default: 'deep', models: ['deep', 'light', 'no-effort'],
  reasoning_depths: ['high'], model_capabilities: {
    deep: { reasoning_depths: ['high', 'xhigh'] }, light: { reasoning_depths: ['none'] }, 'no-effort': { reasoning_depths: [] },
  } }];

describe('model-bound runtime selection', () => {
  it('uses aliases and the discovered default without changing the saved selection', () => {
    const options = runtimeOptions(agents, 'codex-cli', '');
    expect(options.model).toBe('deep');
    expect(options.reasoningDepths).toEqual(['high', 'xhigh']);
  });

  it('treats an explicit empty per-model capability as authoritative', () => {
    expect(runtimeOptions(agents, 'codex', 'no-effort').reasoningDepths).toEqual([]);
  });

  it('resets incompatible effort only in the newly selected draft', () => {
    const draft = { provider: 'codex', model: 'deep', reasoningDepth: 'xhigh', additionalPrompt: 'Keep APIs stable.' };
    expect(changeRuntimeSelection(draft, { model: 'light' }, agents)).toEqual({ ...draft, model: 'light', reasoningDepth: 'default' });
    expect(draft.reasoningDepth).toBe('xhigh');
    expect(changeRuntimeSelection(draft, { model: 'deep' }, agents).reasoningDepth).toBe('xhigh');
  });

  it('keeps configured models visible when discovery is unavailable', () => {
    const unavailable = [{ backend: 'codex', models: [], models_source: 'unavailable', model_capabilities: {} }];
    expect(runtimeOptions(unavailable, 'codex', 'saved-model').models).toEqual(['saved-model']);
    expect(runtimeOptions(unavailable, 'codex', 'saved-model').reasoningDepths).toEqual([]);
  });
});
