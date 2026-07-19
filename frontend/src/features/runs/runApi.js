import { requestJson } from '../../lib/api/client';

const jsonPost = (path, body, { signal } = {}) => requestJson(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
});

export const getProjectState = (projectKey, { signal } = {}) => requestJson(
  `/api/state?project=${encodeURIComponent(projectKey)}`,
  { signal },
);

export const getProjectRuns = (projectKey, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/runs`,
  { signal },
);

export const getProjectRun = (projectKey, runId, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/runs/${encodeURIComponent(runId)}`,
  { signal },
);

export const getWorkflowGraph = (projectKey, { signal } = {}) => requestJson(
  `/api/workflow/graph?project=${encodeURIComponent(projectKey)}`,
  { signal },
);

export const getDevLoop = (projectKey, { signal } = {}) => requestJson(
  `/api/devloop?project=${encodeURIComponent(projectKey)}`,
  { signal },
);

export const getMessages = (projectKey, runId, gate = '', { signal } = {}) => {
  const params = new URLSearchParams({ project: projectKey, run: runId });
  if (gate) params.set('gate', gate);
  return requestJson(`/api/messages?${params}`, { signal });
};

export const startRun = (body, options) => jsonPost('/api/start', body, options);
export const retryRun = (projectKey, options) => jsonPost('/api/retry', { project_key: projectKey }, options);
export const terminateRun = (projectKey, options) => jsonPost('/api/terminate', { project_key: projectKey }, options);
export const dispatchTrack = (body, options) => jsonPost('/api/dispatch-track', body, options);
export const startSmoke = (body, options) => jsonPost('/api/smoke-run', body, options);

export const runApi = {
  dispatchTrack,
  getDevLoop,
  getMessages,
  getProjectState,
  getProjectRun,
  getProjectRuns,
  getWorkflowGraph,
  retryRun,
  startRun,
  startSmoke,
  terminateRun,
};
