const KNOWN_LIFECYCLES = new Set(['attention_required', 'preparing', 'ready', 'legacy']);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function invalidPayload() {
  return Object.assign(new Error('Invalid project list response.'), {
    code: 'invalid_response',
  });
}

export function normalizeProjectsPayload(payload) {
  if (!isRecord(payload) || !Array.isArray(payload.projects)) throw invalidPayload();
  const seenKeys = new Set();
  const projects = [];

  for (const item of payload.projects) {
    if (!isRecord(item) || !isRecord(item.project) || !isRecord(item.readiness)) {
      throw invalidPayload();
    }
    const projectKey = item.project.project_key;
    const root = item.project.root;
    const lifecycle = item.readiness.lifecycle;
    const attentionCount = item.attention_count;
    if (
      typeof projectKey !== 'string'
      || !projectKey.trim()
      || typeof root !== 'string'
      || typeof lifecycle !== 'string'
      || !Number.isInteger(attentionCount)
      || attentionCount < 0
    ) {
      throw invalidPayload();
    }
    if (item.active_run !== null) {
      if (
        !isRecord(item.active_run)
        || typeof item.active_run.run_id !== 'string'
        || !item.active_run.run_id
        || typeof item.active_run.next_node !== 'string'
        || !item.active_run.next_node
      ) {
        throw invalidPayload();
      }
    }
    if (seenKeys.has(projectKey)) continue;
    seenKeys.add(projectKey);
    projects.push({
      ...item,
      project: {
        ...item.project,
        project_key: projectKey,
        display_name: typeof item.project.display_name === 'string' && item.project.display_name
          ? item.project.display_name
          : projectKey,
        root,
      },
      readiness: {
        ...item.readiness,
        lifecycle: KNOWN_LIFECYCLES.has(lifecycle) ? lifecycle : 'legacy',
      },
      attention_count: attentionCount,
      active_run: item.active_run,
    });
  }
  return projects;
}
