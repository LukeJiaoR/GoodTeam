import { requestJson } from '../../lib/api/client';

export const listProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });

export const importProject = ({ root }) => requestJson('/api/projects/import', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ root }),
});

export const projectApi = {
  listProjects,
  importProject,
};
