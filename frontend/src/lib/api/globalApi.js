import { requestJson } from './client';

export const getProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });
const catalogProviders = ['codex', 'claude', 'codebuddy', 'agy', 'gemini', 'opencode', 'pi'];
export function catalogProvider(provider) {
  const canonical = { anthropic: 'claude', antigravity: 'agy', 'pi-rpc': 'pi' }[provider] || provider;
  return catalogProviders.includes(canonical) ? canonical : '';
}

export const getAgents = async ({ provider, signal, onProgress } = {}) => {
  const providers = provider ? [catalogProvider(provider)].filter(Boolean) : catalogProviders;
  const agents = await Promise.all(providers.map(async (provider) => {
    let result;
    try {
      const payload = await requestJson(`/api/agents?provider=${provider}`, { signal, cache: 'no-store' });
      result = payload.agents || [];
    } catch (error) {
      if (signal?.aborted) throw error;
      result = [{ backend: provider, models: [], models_source: 'unavailable', discovery_status: 'failed' }];
    }
    if (!signal?.aborted) onProgress?.(result);
    return result;
  }));
  return { agents: agents.flat() };
};
