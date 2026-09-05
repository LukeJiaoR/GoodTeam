import { requestJson } from '../../lib/api/client';
import { getAgents } from '../../lib/api/globalApi';
import { getProjectState } from '../runs/runApi';

export const getSettingsAgents = getAgents;
export const getProjectSettings = getProjectState;
export const saveWorkerDefaults = (projectKey, mode, workers = {}, roleModels = {}, workerTypeSettings = {}) => requestJson('/api/project-config', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ project: projectKey, worker_backend_mode: mode, workers, role_models: roleModels, worker_type_settings: workerTypeSettings }),
});

export const settingsApi = { getProjectSettings, getSettingsAgents, saveWorkerDefaults };
