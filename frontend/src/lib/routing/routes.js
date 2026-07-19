const segment = (value) => encodeURIComponent(String(value || '').trim());

export const routes = Object.freeze({
  projects: () => '/projects',
  inbox: () => '/inbox',
  activity: () => '/activity',
  settings: () => '/settings',
  projectHome: (projectKey) => `/projects/${segment(projectKey)}/home`,
  projectWork: (projectKey) => `/projects/${segment(projectKey)}/work`,
  projectRun: (projectKey, runId) => (
    `/projects/${segment(projectKey)}/work/runs/${segment(runId)}`
  ),
  projectReview: (projectKey) => `/projects/${segment(projectKey)}/review`,
  projectReviewItem: (projectKey, reviewId) => (
    `/projects/${segment(projectKey)}/review/${segment(reviewId)}`
  ),
  projectKnowledge: (projectKey) => `/projects/${segment(projectKey)}/knowledge`,
  projectSettings: (projectKey, section = 'general') => (
    `/projects/${segment(projectKey)}/settings/${segment(section)}`
  ),
});

export function legacyDestination(search = '') {
  const params = new URLSearchParams(search);
  const projectKey = params.get('project')?.trim();
  if (!projectKey) return routes.projects();
  const tab = params.get('tab')?.trim().toLowerCase();
  if (['approvals', 'workbench'].includes(tab)) return routes.projectReview(projectKey);
  if (['artifacts', 'docs', 'memory', 'agents_memory'].includes(tab)) {
    return routes.projectKnowledge(projectKey);
  }
  if (['system', 'agents'].includes(tab)) return routes.projectSettings(projectKey, 'general');
  if (tab === 'overview') return routes.projectHome(projectKey);
  return routes.projectWork(projectKey);
}
