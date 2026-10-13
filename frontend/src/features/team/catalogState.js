export function refreshCatalog(agents) {
  return agents.map((agent) => ({ ...agent, discovery_status: 'loading', stale: true }));
}

export function finishCatalog(previous, incoming = []) {
  const received = new Set(incoming.map((agent) => agent.backend));
  return mergeCatalog(previous, [
    ...previous.filter((agent) => !received.has(agent.backend)).map((agent) => ({
      backend: agent.backend, models: [], models_source: 'unavailable', discovery_status: 'failed',
    })),
    ...incoming,
  ]);
}

export function mergeCatalog(previous, incoming) {
  const next = [...previous];
  for (const agent of incoming) {
    const index = next.findIndex((item) => item.backend === agent.backend);
    const old = next[index];
    const failed = agent.models_source === 'unavailable' || agent.discovery_status === 'failed';
    const value = failed && old?.models?.length
      ? { ...agent, models: old.models, model_capabilities: {}, stale: true,
        stale_source: old.stale_source || old.models_source, observed_at: old.observed_at, discovery_status: 'failed' }
      : { ...agent, discovery_status: failed ? 'failed' : 'complete', stale: false };
    if (index < 0) next.push(value);
    else next[index] = value;
  }
  return next;
}
