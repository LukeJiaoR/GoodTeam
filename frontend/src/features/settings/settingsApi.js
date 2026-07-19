import { requestJson } from '../../lib/api/client';
import { getAgents, getOverview } from '../../lib/api/globalApi';
import { getProjectState } from '../runs/runApi';

export const getSettingsOverview = getOverview;
export const getSettingsAgents = getAgents;
export const getWorkspaceSettings = ({ signal } = {}) => requestJson('/api/settings', { signal });
export const getProjectSettings = getProjectState;
export const saveWorkerDefaults = (projectKey, mode, workers = {}, roleModels = {}) => requestJson('/api/project-config', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ project: projectKey, worker_backend_mode: mode, workers, role_models: roleModels }),
});
export const saveWorkspaceSettings = (defaults, { signal } = {}) => requestJson('/api/settings', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(defaults), signal,
});

export const settingsApi = { getProjectSettings, getSettingsAgents, getSettingsOverview, getWorkspaceSettings, saveWorkerDefaults, saveWorkspaceSettings };
