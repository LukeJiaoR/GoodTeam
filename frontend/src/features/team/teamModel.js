const ROLE_FALLBACK_LABEL = (role) => String(role || '').replaceAll('_', ' ');

function capabilityTone(capability) {
  if (capability === 'ready') return 'active';
  if (capability === 'degraded') return 'danger';
  return 'neutral';
}

function runtimeLabel(worker) {
  const provider = worker.provider || '';
  const model = worker.model || '';
  if (provider && model) return `${provider} · ${model}`;
  return provider || model || '';
}

export function buildTeamModel(data) {
  const team = data?.team || null;
  if (!team) return null;
  const roles = (team.roles || []).map((entry) => ({
    role: entry.role,
    label: entry.label || ROLE_FALLBACK_LABEL(entry.role),
    state: entry.state || 'enabled',
    capability: entry.capability || 'unstaffed',
    capabilityTone: capabilityTone(entry.capability),
    workers: (entry.workers || []).map((worker) => ({
      id: worker.id,
      name: worker.name || worker.id,
      lifecycle: worker.lifecycle,
      dispatchable: Boolean(worker.dispatchable),
      provider: worker.provider || '',
      model: worker.model || '',
      reasoningDepth: worker.reasoning_depth || 'default',
      additionalPrompt: worker.additional_prompt || '',
      runtimeLabel: runtimeLabel(worker),
    })),
  }));
  const managerRuntime = team.manager?.runtime || {};
  return {
    schemaVersion: team.schema_version || '',
    revision: team.revision || 0,
    roles,
    manager: {
      role: team.manager?.role || 'orchestrator',
      label: team.manager?.label || 'Task Assistant',
      provider: managerRuntime.provider || '',
      model: managerRuntime.model || '',
      reasoningDepth: managerRuntime.reasoning_depth || 'default',
      additionalPrompt: managerRuntime.additional_prompt || '',
      runtimeLabel: runtimeLabel(managerRuntime),
    },
  };
}

export function capabilityCounts(roles) {
  return (roles || []).reduce(
    (counts, role) => ({ ...counts, [role.capability]: (counts[role.capability] || 0) + 1 }),
    { ready: 0, unstaffed: 0, degraded: 0, disabled: 0 },
  );
}

export function rosteredWorkerCount(roles) {
  return (roles || []).reduce((total, role) => total + role.workers.length, 0);
}

/**
 * Diff a worker's edited fields against current truth and return the matching
 * control-plane commands. Order matters for reviewability: identity, then
 * assignment, then runtime.
 */
export function workerEditCommands(worker, draft) {
  const commands = [];
  const name = draft.name.trim();
  if (name && name !== worker.name) commands.push({ kind: 'rename_worker', worker_id: worker.id, name });
  if (draft.role && draft.role !== worker.role) commands.push({ kind: 'change_worker_role', worker_id: worker.id, role: draft.role });
  const runtime = {};
  if (draft.provider && draft.provider !== worker.provider) runtime.provider = draft.provider;
  if ((draft.model || '') !== worker.model) runtime.model = draft.model;
  if (draft.reasoningDepth && draft.reasoningDepth !== worker.reasoningDepth) runtime.reasoning_depth = draft.reasoningDepth;
  if ((draft.additionalPrompt || '') !== worker.additionalPrompt) runtime.additional_prompt = draft.additionalPrompt.trim();
  if (Object.keys(runtime).length) {
    commands.push({ kind: 'change_worker_runtime', worker_id: worker.id, ...runtime });
  }
  if (draft.lifecycle && draft.lifecycle !== worker.lifecycle) {
    if (draft.lifecycle === 'active' && worker.lifecycle === 'inactive') {
      commands.push({ kind: 'activate_worker', worker_id: worker.id });
    } else if (draft.lifecycle === 'inactive' && worker.lifecycle === 'active') {
      commands.push({ kind: 'inactivate_worker', worker_id: worker.id });
    }
  }
  return commands;
}

export function workerCreateCommand(role, draft) {
  const runtime = {};
  if (draft.provider) runtime.provider = draft.provider;
  if (draft.model) runtime.model = draft.model;
  if (draft.reasoningDepth && draft.reasoningDepth !== 'default') runtime.reasoning_depth = draft.reasoningDepth;
  if (draft.additionalPrompt && draft.additionalPrompt.trim()) runtime.additional_prompt = draft.additionalPrompt.trim();
  const command = { kind: 'initialize_worker', role, ensure_role_enabled: true };
  if (draft.name && draft.name.trim()) command.name = draft.name.trim();
  if (Object.keys(runtime).length) command.runtime = runtime;
  return command;
}
