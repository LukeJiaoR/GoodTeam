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
export const sendTaskAssistantMessage = (projectKey, body, options) => jsonPost(
  `/api/projects/${encodeURIComponent(projectKey)}/task-assistant/messages`, body, options,
);
export const taskAssistantAction = (projectKey, body, options) => jsonPost(
  `/api/projects/${encodeURIComponent(projectKey)}/task-assistant/actions`, body, options,
);
export const retryRun = (projectKey, options) => jsonPost('/api/retry', { project_key: projectKey }, options);
export const terminateRun = (projectKey, options) => jsonPost('/api/terminate', { project_key: projectKey }, options);
export const dispatchTrack = (body, options) => jsonPost('/api/dispatch-track', body, options);
export const startSmoke = (body, options) => jsonPost('/api/smoke-run', body, options);
export const startChangeIntent = (projectKey, approval, expectedTarget = {}, options) => jsonPost(
  '/api/intent-commit/start',
  {
    project_key: projectKey,
    approval,
    expected_run_id: expectedTarget.run_id,
    expected_target_type: expectedTarget.target_type,
    expected_target_id: expectedTarget.target_id,
    expected_task_id: expectedTarget.task_id,
    expected_artifact_key: expectedTarget.artifact_key,
    expected_artifact_version: expectedTarget.artifact_version,
    expected_head_sha: expectedTarget.head_sha,
    initial_text: options?.initialText,
  },
  options,
);
export const discussChangeIntent = (projectKey, intentId, text, options) => jsonPost(
  '/api/intent-commit/discuss',
  { project_key: projectKey, intent_id: intentId, text },
  options,
);
export const keepDiscussingChangeIntent = (projectKey, intentId, options) => jsonPost(
  '/api/intent-commit/keep-discussing',
  { project_key: projectKey, intent_id: intentId },
  options,
);
export const confirmChangeIntent = (projectKey, intentId, options) => jsonPost(
  '/api/intent-commit/confirm',
  { project_key: projectKey, intent_id: intentId },
  options,
);

export const runApi = {
  confirmChangeIntent,
  discussChangeIntent,
  dispatchTrack,
  getDevLoop,
  getMessages,
  getProjectState,
  getProjectRun,
  getProjectRuns,
  getWorkflowGraph,
  keepDiscussingChangeIntent,
  retryRun,
  sendTaskAssistantMessage,
  startChangeIntent,
  startRun,
  startSmoke,
  taskAssistantAction,
  terminateRun,
};
