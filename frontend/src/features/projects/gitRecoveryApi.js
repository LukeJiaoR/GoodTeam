import { requestJson } from '../../lib/api/client';

export const gitRecoveryApi = {
  read: (projectKey) => requestJson(`/api/projects/${encodeURIComponent(projectKey)}/home`, { cache: 'no-store' }),
  recover: (projectKey, files) => requestJson('/api/git-init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_key: projectKey, initial_files: files, create_remote: false }),
  }),
};
