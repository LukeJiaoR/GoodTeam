import { routes } from '../../lib/routing/routes';

export const ATTENTION_KINDS = ['blocked', 'needs_input'];
const SOURCES = ['readiness', 'assistant', 'continuation'];
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const identity = (value) => typeof value === 'string' && Boolean(value.trim())
  && value === value.trim() && ![...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) && !['.', '..'].includes(value);
const compare = (left, right) => left < right ? -1 : left > right ? 1 : 0;

// An unavailable projection must never turn into an all-clear or break the
// ordinary project launcher. Legacy attention_count remains readiness-only.
export function normalizeProjectAttention(value, projectKey) {
  if (projectKey !== undefined && !identity(projectKey)) return null;
  if (!isRecord(value) || value.schema_version !== 'project_attention.v1'
      || !Array.isArray(value.items) || !isRecord(value.counts)) return null;
  const counts = { blocked: 0, needs_input: 0, total: 0 };
  const seen = new Set();
  for (const item of value.items) {
    if (!isRecord(item) || !identity(item.id) || !identity(item.target_id)
        || !ATTENTION_KINDS.includes(item.kind) || !SOURCES.includes(item.source)
        || typeof item.title !== 'string' || !item.title.trim()
        || typeof item.reason !== 'string' || !item.reason.trim()
        || seen.has(item.id)) return null;
    if (item.source === 'readiness' && (!item.id.startsWith('readiness:') || !item.id.endsWith(`:${item.target_id}`)
        || item.id.length <= `readiness::${item.target_id}`.length)) return null;
    if (item.source === 'assistant' && (item.kind !== 'needs_input'
        || item.id !== `assistant:proposal:${item.target_id}`)) return null;
    if (item.source === 'continuation' && (!item.id.startsWith(`continuation:${item.target_id}:`)
        || item.id === `continuation:${item.target_id}:`)) return null;
    seen.add(item.id);
    counts[item.kind] += 1;
    counts.total += 1;
  }
  if (Object.keys(counts).some((kind) => !Number.isSafeInteger(value.counts[kind])
      || value.counts[kind] !== counts[kind])) return null;
  return { schema_version: value.schema_version, counts, items: value.items.map(item => ({
    id: item.id, kind: item.kind, source: item.source,
    target_id: item.target_id, title: item.title, reason: item.reason,
  })) };
}

export function attentionOverview(projects) {
  const counts = { blocked: 0, needs_input: 0, total: 0 };
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
  const query = new URLSearchParams({ attention: item.id, attention_source: item.source,
    attention_kind: item.kind, attention_target: item.target_id });
  return `${routes.projectWork(projectKey)}?${query}`;
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
export function resolveAttentionFocus({ selection, projectKey, projects, data, freshness = 'ready' }) {
  if (!selection || selection.invalid) return { status: 'unavailable' };
  if (freshness !== 'ready') return { status: freshness === 'loading' ? 'loading' : 'unavailable' };
  const project = projects.find(entry => entry.project.project_key === projectKey);
  if (!project?.attention) return { status: 'unavailable' };
  const item = project.attention.items.find(candidate => ['id', 'source', 'kind', 'target_id']
    .every(key => candidate[key] === selection[key]));
  if (!item) return { status: 'changed' };
  const workspace = data?.team_workspace;
  const mission = workspace?.mission;
  if (!workspace || workspace.schema_version !== 'team_workspace.v1'
      || (mission?.project_key && mission.project_key !== projectKey)) return { status: 'unavailable' };
  const stateProject = mission?.project_key || data.project_assistant?.project_key
    || data.task_assistant_session?.project_key;
  if (stateProject !== projectKey) return { status: 'unavailable' };
  if (workspace.execution_actors !== undefined && !Array.isArray(workspace.execution_actors)) return { status: 'unavailable' };
  const attention = workspace.attention_request;
  if (item.source === 'assistant') {
    const proposal = (data.project_assistant || workspace).proposed_task;
    if (proposal?.id !== item.target_id || proposal.status === 'cancelled'
        || !proposal.needs_clarification) return { status: 'changed' };
    return { status: 'current', item, target: 'draft' };
  }
  if (item.source === 'continuation') {
    const session = data.task_assistant_session;
    const notice = session?.project_continuation_notice;
    if (session?.project_key !== projectKey || notice?.id !== item.id
        || notice.continuation_id !== item.target_id || notice.kind !== item.kind
        || session.project_continuation?.id !== item.target_id) return { status: 'changed' };
    return { status: 'current', item, target: 'continuation' };
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
  if (item.source === 'continuation') {
    const key = `work.manager.continuation.reason.${item.reason}`;
    return t(key) === key ? item.reason : t(key);
  }
  const localKey = `projects.attention.reason.${item.reason}`;
  const local = t(localKey);
  if (local !== localKey) return local;
  const sharedKey = `work.manager.attentionReasons.${item.reason}`;
  const shared = t(sharedKey);
  return shared !== sharedKey ? shared : item.reason;
}


export function attentionTitle(item, t) {
  if (item.source === 'continuation') return t('projects.attention.continuation.title');
  if (item.source !== 'readiness') return item.title;
  const key = `projects.attention.step.${item.target_id}`;
  const label = t(key);
  return label !== key ? label : item.title;
}
