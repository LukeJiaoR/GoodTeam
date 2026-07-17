import { requestJson } from '../../lib/api/client';

export const listProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });

export const importProject = ({ root }) => requestJson('/api/projects/import', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ root }),
});

export const getProjectHome = (projectKey, { signal } = {}) => (
  requestJson(`/api/projects/${encodeURIComponent(projectKey)}/home`, { signal })
);

export const confirmProjectMode = (projectKey, mode) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/mode`,
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  },
);

export const projectApi = {
  confirmProjectMode,
  getProjectHome,
  listProjects,
  importProject,
};
