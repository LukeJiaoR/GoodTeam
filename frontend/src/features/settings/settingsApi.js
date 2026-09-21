import { requestJson } from '../../lib/api/client';
import { getAgents } from '../../lib/api/globalApi';
import { getProjectState, taskAssistantAction } from '../runs/runApi';

export const getSettingsAgents = getAgents;
export const getProjectSettings = getProjectState;
export const sendTeamCommands = (projectKey, commands, { reason = '', commandKey = '' } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/team/commands`,
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ commands, reason, command_key: commandKey }),
  },
);
export const getTeamEvents = (projectKey, { signal } = {}) => requestJson(
  `/api/projects/${encodeURIComponent(projectKey)}/team/events`,
  { signal },
);

export const getSkillLibrary = ({ signal } = {}) => requestJson('/api/skills', { signal });
export const previewLocalSkill = (path, { commandKey } = {}) => requestJson(
  '/api/skills/local/preview',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, command_key: commandKey }),
  },
);
export const installLocalSkill = (proposalId) => requestJson(
  '/api/skills/local/install',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proposal_id: proposalId }),
  },
);

export const settingsApi = { getProjectSettings, getSettingsAgents, sendTeamCommands, getTeamEvents, getSkillLibrary, previewLocalSkill, installLocalSkill, taskAssistantAction };
