import { requestJson } from '../../lib/api/client';
import { getProjects } from '../../lib/api/globalApi';
import { getProjectState } from '../runs/runApi';

export const getKnowledgeState = getProjectState;
export const getKnowledgeHome = (projectKey, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/home`,
  { signal },
);
export const getRepositoryUnderstanding = (projectKey, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/readiness/steps/repository_understanding/artifact`,
  { signal },
);
export const getKnowledgeArtifact = (projectKey, key, { signal } = {}) => requestJson(
  `/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(key)}`,
  { signal },
);
export const getMemory = (projectKey, { signal } = {}) => requestJson(`/api/memory?project=${encodeURIComponent(projectKey)}`, { signal });
export const getMemoryEvents = (projectKey, role, query = '', { signal, includeExecution = false } = {}) => {
  const params = new URLSearchParams({ project: projectKey, role, limit: '50' });
  if (query.trim()) params.set('q', query.trim());
  if (includeExecution) params.set('include_execution', 'true');
  return requestJson(`/api/memory/events?${params}`, { signal });
};
export const getMemoryEvent = (projectKey, eventId, { signal } = {}) => requestJson(
  `/api/memory/event?project=${encodeURIComponent(projectKey)}&id=${encodeURIComponent(eventId)}`,
  { signal },
);
export const getCrossProjectMemory = (role, query, scope, { signal } = {}) => {
  const params = new URLSearchParams({ role, q: query, scope: scope.join(','), k: '10' });
  return requestJson(`/api/memory/cross?${params}`, { signal });
};
export const getKnowledgeProjects = getProjects;

export const knowledgeApi = {
  getKnowledgeArtifact, getKnowledgeHome, getKnowledgeState, getMemory,
  getMemoryEvent, getMemoryEvents, getCrossProjectMemory, getKnowledgeProjects,
  getRepositoryUnderstanding,
};
