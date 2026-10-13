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
    // Synthetic HTTP/empty-response failures omit identity metadata. Keep the
    // known alias mapping, but never inherit old readiness or capabilities.
    const current = failed ? { ...agent, aliases: agent.aliases ?? old?.aliases } : agent;
    const value = failed && old?.models?.length
      ? { ...current, models: old.models, model_capabilities: {}, stale: true,
        stale_source: old.stale_source || old.models_source, observed_at: old.observed_at, discovery_status: 'failed' }
      : { ...current, discovery_status: failed ? 'failed' : 'complete', stale: false };
    if (index < 0) next.push(value);
    else next[index] = value;
  }
  return next;
}
