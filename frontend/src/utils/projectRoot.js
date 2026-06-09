export function resolveProjectRootFromRepos(repos = {}) {
  return (
    repos.root?.path
    || repos.docs?.path
    || repos.backend?.path
    || repos.frontend?.path
    || Object.values(repos)[0]?.path
    || ''
  );
}
