import { requestJson } from './client';

export const getProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });
export const getOverview = ({ signal } = {}) => requestJson('/api/state', { signal });
export const getProjectState = (projectKey, { signal } = {}) => requestJson(`/api/state?project=${encodeURIComponent(projectKey)}`, { signal });
export const getAgents = ({ signal } = {}) => requestJson('/api/agents', { signal });

export async function getProjectProjections({ signal } = {}) {
  const payload = await getProjects({ signal });
  const projects = Array.isArray(payload.projects) ? payload.projects : [];
  const states = await Promise.all(projects.map(async (item) => {
    const key = item?.project?.project_key;
    if (!key) return { item, state: null, error: null };
    try { return { item, state: await getProjectState(key, { signal }), error: null }; }
    catch (error) { return { item, state: null, error }; }
  }));
  return states;
}
