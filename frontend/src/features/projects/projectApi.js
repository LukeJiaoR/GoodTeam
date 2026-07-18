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

function projectReadinessPath(projectKey, suffix) {
  return `/api/projects/${encodeURIComponent(projectKey)}/readiness/${suffix}`;
}

function postProjectReadiness(projectKey, suffix, body = {}) {
  return requestJson(projectReadinessPath(projectKey, suffix), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export const startReadiness = (projectKey, { inputs = {}, idempotencyKey } = {}) => (
  postProjectReadiness(projectKey, 'start', {
    inputs,
    idempotency_key: idempotencyKey,
  })
);

export const submitReadinessInput = (projectKey, input) => (
  postProjectReadiness(projectKey, 'input', input)
);

export const retryReadinessStep = (projectKey, stepId) => (
  postProjectReadiness(projectKey, 'retry', { step_id: stepId })
);

export const cancelReadiness = (projectKey) => (
  postProjectReadiness(projectKey, 'cancel')
);

export const getReadinessStepArtifact = (projectKey, stepId, { signal } = {}) => (
  requestJson(
    `${projectReadinessPath(projectKey, 'steps')}/${encodeURIComponent(stepId)}/artifact`,
    { signal },
  )
);

export const startProjectWorkflow = (projectKey) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/workflow`,
  { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
);

export const projectApi = {
  cancelReadiness,
  confirmProjectMode,
  getReadinessStepArtifact,
  getProjectHome,
  listProjects,
  importProject,
  retryReadinessStep,
  startProjectWorkflow,
  startReadiness,
  submitReadinessInput,
};
