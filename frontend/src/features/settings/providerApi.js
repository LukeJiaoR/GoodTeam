import { requestJson } from '../../lib/api/client';

export const getProviders = (signal) => requestJson('/api/providers', { signal });
export const getProviderUsage = (signal) => requestJson('/api/providers/usage', { signal });
const post = (path, body) => requestJson(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export const updateProvider = (body) => post('/api/providers', body);
export const fetchProviderModels = (body) => post('/api/providers/models', body);
