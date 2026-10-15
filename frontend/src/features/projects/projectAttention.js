import { routes } from '../../lib/routing/routes';

export const ATTENTION_KINDS = ['blocked', 'approval', 'needs_input'];
const SOURCES = ['workflow', 'readiness', 'assistant'];
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const identity = (value) => typeof value === 'string' && Boolean(value.trim())
  && value === value.trim() && ![...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) && !['.', '..'].includes(value);
const compare = (left, right) => left < right ? -1 : left > right ? 1 : 0;

// An unavailable projection must never turn into an all-clear or break the
// ordinary project launcher. Legacy attention_count remains readiness-only.
export function normalizeProjectAttention(value, activeRun = null, projectKey) {
  if (projectKey !== undefined && !identity(projectKey)) return null;
  if (!isRecord(value) || value.schema_version !== 'project_attention.v1'
      || !Array.isArray(value.items) || !isRecord(value.counts)) return null;
  const counts = { blocked: 0, approval: 0, needs_input: 0, total: 0 };
  const seen = new Set();
  for (const item of value.items) {
    if (!isRecord(item) || !identity(item.id) || !identity(item.target_id)
        || !ATTENTION_KINDS.includes(item.kind) || !SOURCES.includes(item.source)
        || typeof item.title !== 'string' || !item.title.trim()
        || typeof item.reason !== 'string' || !item.reason.trim()
        || typeof item.run_id !== 'string' || seen.has(item.id)) return null;
    if (item.source === 'workflow') {
      if (!identity(item.run_id) || item.run_id !== activeRun?.run_id
          || !item.id.startsWith(`workflow:${item.run_id}:`)
          || item.id === `workflow:${item.run_id}:`
          || (item.kind === 'approval' && item.id !== `workflow:${item.run_id}:approval:${item.target_id}`)) return null;
    } else if (item.run_id !== '') return null;
    if (item.source === 'readiness' && (item.kind === 'approval'
        || !item.id.startsWith('readiness:') || !item.id.endsWith(`:${item.target_id}`)
        || item.id.length <= `readiness::${item.target_id}`.length)) return null;
    if (item.source === 'assistant' && (item.kind !== 'needs_input'
        || item.id !== `assistant:proposal:${item.target_id}`)) return null;
    seen.add(item.id);
    counts[item.kind] += 1;
    counts.total += 1;
  }
  if (Object.keys(counts).some((kind) => !Number.isSafeInteger(value.counts[kind])
      || value.counts[kind] !== counts[kind])) return null;
  return { schema_version: value.schema_version, counts, items: value.items.map(item => ({
    id: item.id, kind: item.kind, source: item.source, run_id: item.run_id,
    target_id: item.target_id, title: item.title, reason: item.reason,
  })) };
}

export function attentionOverview(projects) {
  const counts = { blocked: 0, approval: 0, needs_input: 0, total: 0 };
  const unavailable = [];
  const items = [];
  for (const project of projects) {
    if (!project.attention) { unavailable.push(project.project); continue; }
    for (const item of project.attention.items) {
      items.push({ ...item, project: project.project });
      counts[item.kind] += 1;
      counts.total += 1;
    }
  }
  items.sort((left, right) => ATTENTION_KINDS.indexOf(left.kind) - ATTENTION_KINDS.indexOf(right.kind)
    || compare(left.project.project_key, right.project.project_key) || compare(left.id, right.id));
  return { counts, items, unavailable };
}

export function attentionHref(projectKey, item) {
  const path = item.source === 'workflow'
    ? routes.projectRun(projectKey, item.run_id) : routes.projectWork(projectKey);
  const query = new URLSearchParams({ attention: item.id, attention_source: item.source,
    attention_kind: item.kind, attention_target: item.target_id });
  return `${path}?${query}`;
}

export function readAttentionSelection(search) {
  const keys = ['attention', 'attention_source', 'attention_kind', 'attention_target'];
  if (!keys.some(key => search.has(key))) return null;
  if (keys.some(key => search.getAll(key).length !== 1 || !identity(search.get(key)))) return { invalid: true };
  const selection = { id: search.get('attention'), source: search.get('attention_source'),
    kind: search.get('attention_kind'), target_id: search.get('attention_target') };
  return SOURCES.includes(selection.source) && ATTENTION_KINDS.includes(selection.kind)
    ? selection : { invalid: true };
}

// A verified list identity is necessary but insufficient: the Work surface must
// still represent that exact run/gate, preparation execution/step, or proposal.
export function resolveAttentionFocus({ selection, projectKey, runId = '', projects, data, freshness = 'ready' }) {
  if (!selection || selection.invalid) return { status: 'unavailable' };
  if (freshness !== 'ready') return { status: freshness === 'loading' ? 'loading' : 'unavailable' };
  const project = projects.find(entry => entry.project.project_key === projectKey);
  if (!project?.attention) return { status: 'unavailable' };
  const item = project.attention.items.find(candidate => ['id', 'source', 'kind', 'target_id']
    .every(key => candidate[key] === selection[key]));
  if (!item || (item.source === 'workflow' ? item.run_id !== runId : Boolean(runId))) return { status: 'changed' };
  const workspace = data?.team_workspace;
  const mission = workspace?.mission;
  if (!workspace || workspace.schema_version !== 'team_workspace.v1'
      || (mission?.project_key && mission.project_key !== projectKey)) return { status: 'unavailable' };
  const stateProject = mission?.project_key || data.project_assistant?.project_key
    || data.task_assistant_session?.project_key || data.state?.project_key;
  if (stateProject !== projectKey) return { status: 'unavailable' };
  if (workspace.execution_actors !== undefined && !Array.isArray(workspace.execution_actors)) return { status: 'unavailable' };
  const attention = workspace.attention_request;
  if (item.source === 'workflow') {
    const actualKind = ['discussing', 'ready_to_confirm', 'stale', 'ambiguous', 'committing'].includes(attention?.intent_commit?.status)
      ? 'needs_input' : attention?.kind === 'blocker' ? 'blocked' : attention?.kind === 'approval' ? 'approval' : '';
    if (!data.active_run || data.state?.run_id !== runId || data.state?.project_key !== projectKey
        || mission?.source !== 'workflow' || mission.run_id !== runId || mission.read_only
        || item.id !== `workflow:${runId}:${attention?.id}`
        || (attention?.target_id || runId) !== item.target_id || actualKind !== item.kind
        || (item.kind === 'blocked' && (workspace.execution_actors || []).some(actor => actor.role !== 'orchestrator' && actor.state === 'working'))) return { status: 'changed' };
    return { status: 'current', item, target: attention.kind === 'blocker' ? 'blocker' : 'attention' };
  }
  if (item.source === 'assistant') {
    const proposal = (data.project_assistant || workspace).proposed_task;
    if (proposal?.id !== item.target_id || proposal.run_id || proposal.status === 'cancelled'
        || !proposal.needs_clarification || mission?.read_only) return { status: 'changed' };
    return { status: 'current', item, target: 'draft' };
  }
  if (!Array.isArray(project.readiness.steps)) return { status: 'unavailable' };
  const step = project.readiness.steps.find(value => value?.id === item.target_id);
  if (!step || (step.status === 'failed' ? 'blocked' : step.status === 'needs_input' ? 'needs_input' : '') !== item.kind
      || item.id !== `readiness:${project.readiness.execution?.id || 'unstarted'}:${item.target_id}`) return { status: 'changed' };
  // A blocked preparation may have no editable surface. Keep its exact reason
  // in the focused notice; never focus a different pending step or proposal.
  if (item.target_id === 'project_mode' && project.readiness.project_mode === 'unconfirmed'
      && attention?.kind === 'project_mode') return { status: 'current', item, target: 'projectMode' };
  const target = attention?.kind === 'project_preparation' && attention.step_id === item.target_id
    && `readiness:${attention.id?.slice('project-preparation:'.length)}` === item.id
    ? 'preparation' : null;
  if (item.kind === 'needs_input' && !target) return { status: attention ? 'changed' : 'unavailable' };
  return { status: 'current', item, target };
}

export function attentionReason(item, t) {
  const localKey = `projects.attention.reason.${item.reason}`;
  const local = t(localKey);
  if (local !== localKey) return local;
  const sharedKey = `work.manager.attentionReasons.${item.reason}`;
  const shared = t(sharedKey);
  return shared !== sharedKey ? shared : item.reason;
}


export function attentionTitle(item, t) {
  if (item.source !== 'readiness') return item.title;
  const key = `projects.attention.step.${item.target_id}`;
  const label = t(key);
  return label !== key ? label : item.title;
}
