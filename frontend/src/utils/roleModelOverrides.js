export function parseRoleModelOverrideLines(roleModelOverrides) {
  return roleModelOverrides
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export function parseRoleModelOverridesMap(roleModelOverrides) {
  return Object.fromEntries(
    parseRoleModelOverrideLines(roleModelOverrides)
      .map((line) => line.split('=', 2))
      .filter((parts) => parts.length === 2 && parts[0] && parts[1])
  );
}

export function formatRoleModelOverridesText(roleModelOverrides) {
  return Object.entries(roleModelOverrides)
    .map(([role, model]) => `${role}=${model}`)
    .join('\n');
}

export function formatRoleModelOverridesMap(roleModelOverrides) {
  return formatRoleModelOverridesText(roleModelOverrides);
}

export function expandInitBucketWorkerOverrides(bucketSelections, buckets) {
  const workers = {};
  buckets.forEach((bucket) => {
    const backend = String(bucketSelections[bucket.key]?.backend || '').trim();
    if (!backend) return;
    bucket.roles.forEach((role) => {
      workers[role] = backend;
    });
  });
  return workers;
}

export function expandInitBucketModelOverrides(bucketSelections, buckets, fallbackBackends = {}) {
  const roleModels = {};
  buckets.forEach((bucket) => {
    const selection = bucketSelections[bucket.key] || {};
    const selectedModel = selection.model === '__custom__' ? selection.customModel : selection.model;
    const model = String(selectedModel || '').trim();
    if (!model) return;

    const backend = String(selection.backend || fallbackBackends[bucket.key] || '').trim();
    const qualifiedModel = backend && !model.includes(':') ? `${backend}:${model}` : model;
    bucket.roles.forEach((role) => {
      roleModels[role] = qualifiedModel;
    });
  });
  return roleModels;
}
