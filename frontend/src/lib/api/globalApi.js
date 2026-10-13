import { requestJson } from './client';

export const getProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });
const catalogProviders = ['codex', 'claude', 'agy', 'gemini', 'opencode', 'pi'];
export const getAgents = async ({ signal, onProgress } = {}) => {
  const agents = await Promise.all(catalogProviders.map(async (provider) => {
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
