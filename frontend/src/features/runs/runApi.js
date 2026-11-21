import { requestJson } from '../../lib/api/client';

const jsonPost = (path, body, { signal } = {}) => requestJson(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
});

export const getProjectState = (projectKey, { signal, goalLimit } = {}) => requestJson(
  `/api/state?project=${encodeURIComponent(projectKey)}${goalLimit === undefined ? '' : `&goal_limit=${encodeURIComponent(goalLimit)}`}`,
  { signal },
);

export const getGoalHistory = (projectKey, before, { signal } = {}) => requestJson(
  `/api/goal-history?project=${encodeURIComponent(projectKey)}&before=${encodeURIComponent(before)}&goal_limit=10`,
  { signal, cache: 'no-store' },
);

export const getProjectRuns = (projectKey, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/runs`,
  { signal },
);

export const getProjectRun = (projectKey, runId, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/runs/${encodeURIComponent(runId)}`,
  { signal },
);

export const getTaskInputContext = (projectKey, runId, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/runs/${encodeURIComponent(runId)}/input-context`,
  { signal, cache: 'no-store' },
);

export const sendTaskAssistantMessage = (projectKey, body, options) => jsonPost(
  `/api/projects/${encodeURIComponent(projectKey)}/task-assistant/messages`, body, options,
);
export const uploadConversationAttachment = (projectKey, body, options) => jsonPost(
  `/api/projects/${encodeURIComponent(projectKey)}/task-assistant/attachments`, body, options,
);
export const taskAssistantAction = (projectKey, body, options) => jsonPost(
  `/api/projects/${encodeURIComponent(projectKey)}/task-assistant/actions`, body, options,
);
export const terminateRun = (projectKey, expectedRunId, options) => jsonPost('/api/terminate', {
  project_key: projectKey, expected_run_id: expectedRunId,
}, options);

export const runApi = {
  getProjectState, getGoalHistory, getProjectRuns, getProjectRun, getTaskInputContext,
  sendTaskAssistantMessage, taskAssistantAction, terminateRun,
};
