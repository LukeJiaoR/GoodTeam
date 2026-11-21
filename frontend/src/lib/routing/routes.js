const segment = (value) => encodeURIComponent(String(value || '').trim());

export const routes = Object.freeze({
  projects: () => '/projects',
  projectTeam: (projectKey) => `/projects/${segment(projectKey)}/team`,
  projectWork: (projectKey) => `/projects/${segment(projectKey)}/work`,
  projectKnowledge: (projectKey) => `/projects/${segment(projectKey)}/knowledge`,
});

export function legacyDestination(search = '') {
  const params = new URLSearchParams(search);
  const projectKey = params.get('project')?.trim();
  if (!projectKey) return routes.projects();
  const tab = params.get('tab')?.trim().toLowerCase();
  if (['artifacts', 'docs', 'memory', 'agents_memory'].includes(tab)) {
    return routes.projectKnowledge(projectKey);
  }
  if (['system', 'agents', 'overview'].includes(tab)) return routes.projectTeam(projectKey);
  return routes.projectWork(projectKey);
}
