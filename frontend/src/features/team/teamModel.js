const ACTIVE_STATES = new Set(['working', 'reviewing', 'fixing']);
const UNAVAILABLE_STATES = new Set(['blocked', 'failed', 'stopped']);
const ROLE_LABELS = {
  orchestrator: 'Orchestrator',
  research: 'Research',
  product_requirement: 'Product / Requirement',
  architect: 'Architect',
  review: 'Review',
  planner: 'Planner',
  ui: 'UI',
  implementation: 'Implementation',
  frontend: 'Frontend',
  backend: 'Backend',
  qa: 'QA',
  code_review: 'Code review',
  release: 'Release',
  retro_memory: 'Retro / Memory',
};

function splitModel(value) {
  const text = String(value || '');
  const separator = text.indexOf(':');
  return separator < 0 ? { backend: '', model: text } : {
    backend: text.slice(0, separator),
    model: text.slice(separator + 1),
  };
}

function workerStatus(instances, backendReady) {
  if (instances.some((item) => ACTIVE_STATES.has(item.state))) return 'active';
  if (backendReady === false || instances.some((item) => UNAVAILABLE_STATES.has(item.state))) return 'unavailable';
  return 'idle';
}

export function buildWorkerTypes(data, agents = []) {
  const context = data?.start_context || {};
  const report = context.project_report || {};
  const globalBackend = context.worker_backend?.mode || 'codex';
  const roleBackends = report.role_worker_backends || {};
  const roleModels = context.role_model_overrides || {};
  const workerTypeSettings = context.worker_type_settings || {};
  const preflight = report.worker_backends_preflight || {};
  const actors = data?.team_workspace?.execution_actors || [];
  const agentsByBackend = Object.fromEntries(agents.map((agent) => [agent.backend, agent]));

  return (context.worker_roles || []).map((role) => {
    const backend = roleBackends[role] || globalBackend;
    const explicitModel = splitModel(roleModels[role]);
    const agent = agentsByBackend[backend];
    const instances = actors.filter((actor) => actor.role === role);
    const backendReady = preflight[backend]?.ready ?? agent?.ready;
    const settings = workerTypeSettings[role] || {};
    return {
      role,
      label: ROLE_LABELS[role] || role.replaceAll('_', ' '),
      backend,
      backendReady,
      model: explicitModel.model || agent?.model_default || '',
      modelExplicit: Boolean(explicitModel.model),
      reasoningDepth: settings.reasoning_depth || 'default',
      additionalPrompt: settings.additional_prompt || '',
      desiredInstances: Number(settings.desired_instances) || 1,
      instances,
      status: workerStatus(instances, backendReady),
    };
  });
}
