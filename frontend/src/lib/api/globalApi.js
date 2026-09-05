import { requestJson } from './client';

export const getProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });
export const getAgents = ({ signal } = {}) => requestJson('/api/agents', { signal });
