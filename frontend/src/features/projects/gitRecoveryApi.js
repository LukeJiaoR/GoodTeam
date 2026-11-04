import { requestJson } from '../../lib/api/client';

export const gitRecoveryApi = {
  read: (projectKey) => requestJson(`/api/projects/${encodeURIComponent(projectKey)}/home`, { cache: 'no-store' }),
  recover: (projectKey, files, scaffold = false) => requestJson('/api/git-init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_key: projectKey, create_remote: false,
      ...(scaffold ? { scaffold_new_project: true } : { initial_files: files }),
    }),
  }),
};
