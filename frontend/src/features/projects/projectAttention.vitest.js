import { describe, expect, test } from 'vitest';
import { attentionHref, attentionOverview, normalizeProjectAttention, readAttentionSelection, resolveAttentionFocus } from './projectAttention';
import { normalizeProjectsPayload } from './projectList';

const approval = { id: 'workflow:run-1:approval:prd', kind: 'approval', source: 'workflow',
  run_id: 'run-1', target_id: 'prd', title: 'Ship search', reason: 'approval_required' };
const preparation = { id: 'readiness:prep-1:commands', kind: 'needs_input', source: 'readiness',
  run_id: '', target_id: 'commands', title: 'commands', reason: 'needs_input' };
const proposal = { id: 'assistant:proposal:draft-1', kind: 'needs_input', source: 'assistant',
  run_id: '', target_id: 'draft-1', title: 'Improve search', reason: 'clarification_required' };
const blocked = { ...approval, id: 'workflow:run-1:workflow-blocked', kind: 'blocked', target_id: 'run-1', reason: 'execution_blocked' };
function attention(items = []) {
  return { schema_version: 'project_attention.v1', items,
    counts: { blocked: items.filter(x => x.kind === 'blocked').length,
      approval: items.filter(x => x.kind === 'approval').length,
      needs_input: items.filter(x => x.kind === 'needs_input').length, total: items.length } };
}
function project(items = [approval], key = 'A') {
  return { project: { project_key: key, display_name: key, root: `/work/${key}` },
    active_run: { run_id: 'run-1', next_node: 'await_prd_approval' }, attention_count: 1,
    readiness: { lifecycle: 'ready', execution: { id: 'prep-1' }, steps: [{ id: 'commands', status: 'needs_input' }] },
    attention: attention(items) };
}
function state() {
  return { active_run: true, state: { run_id: 'run-1', project_key: 'A' },
    team_workspace: { schema_version: 'team_workspace.v1',
      mission: { source: 'workflow', run_id: 'run-1', project_key: 'A', read_only: false },
      attention_request: { id: 'approval:prd', kind: 'approval', target_id: 'prd' } } };
}
const selection = readAttentionSelection(new URL(attentionHref('A', approval), 'https://local.test').searchParams);
const resolve = (overrides = {}) => resolveAttentionFocus({ selection, projectKey: 'A', runId: 'run-1',
  projects: [project()], data: state(), ...overrides });

describe('attention projection validation', () => {
  test('validates category totals without adding the legacy readiness count again', () => {
    const projects = normalizeProjectsPayload({ projects: [project([preparation, approval])] });
    expect(projects[0].attention_count).toBe(1);
    expect(attentionOverview(projects).counts).toEqual({ blocked: 0, approval: 1, needs_input: 1, total: 2 });
  });
  test.each([
    ['missing', undefined], ['null', null], ['future schema', { ...attention(), schema_version: 'v2' }],
    ['non-list', { ...attention(), items: {} }], ['missing counts', { items: [], schema_version: 'project_attention.v1' }],
    ['wrong count', { ...attention([approval]), counts: { blocked: 0, approval: 0, needs_input: 0, total: 0 } }],
    ['duplicate id', attention([approval, approval])],
    ['wrong kind', attention([{ ...approval, kind: 'urgent' }])],
    ['wrong source', attention([{ ...approval, source: 'url' }])],
    ['wrong gate identity', attention([{ ...approval, target_id: 'release' }])],
    ['wrong run identity', attention([{ ...approval, run_id: 'run-2' }])],
    ['empty title', attention([{ ...approval, title: '' }])],
    ['non-text reason', attention([{ ...approval, reason: {} }])],
    ['blank target', attention([{ ...approval, target_id: ' ' }])],
    ['assistant run', attention([{ ...proposal, run_id: 'run-1' }])],
    ['assistant approval', attention([{ ...proposal, kind: 'approval' }])],
    ['assistant target', attention([{ ...proposal, target_id: 'draft-2' }])],
    ['readiness target', attention([{ ...preparation, target_id: 'constraints' }])],
    ['readiness execution', attention([{ ...preparation, id: 'readiness::commands' }])],
  ])('keeps ordinary launcher navigation when attention is %s', (_label, value) => {
    const result = normalizeProjectsPayload({ projects: [{ ...project(), attention: value }] });
    expect(result[0].project.project_key).toBe('A');
    expect(result[0].attention).toBeNull();
    expect(attentionOverview(result).unavailable).toHaveLength(1);
  });
  test('workflow items require the same currently active list run', () => {
    expect(normalizeProjectAttention(attention([approval]), null)).toBeNull();
  });
  test('review recovery may target a gate rather than the run', () => {
    const recovery = { ...blocked, id: 'workflow:run-1:recovery:takeover', target_id: 'takeover' };
    expect(normalizeProjectAttention(attention([recovery]), { run_id: 'run-1' })?.items).toEqual([recovery]);
  });
  test('deterministically prioritizes blocked, approval, input then project key and exact ID', () => {
    const projects = [project([proposal, preparation, approval], 'Z'), project([blocked], 'B'), project([approval], 'A')];
    const ids = value => attentionOverview(value).items.map(x => `${x.project.project_key}:${x.id}`);
    expect(ids(projects)).toEqual(ids([...projects].reverse()));
    expect(ids(projects)).toEqual(['B:workflow:run-1:workflow-blocked', 'A:workflow:run-1:approval:prd',
      'Z:workflow:run-1:approval:prd', 'Z:assistant:proposal:draft-1', 'Z:readiness:prep-1:commands']);
  });
  test('constructs encoded local links without trusting server URLs', () => {
    const href = attentionHref('A/B ?#', { ...approval, run_id: 'run/?#', url: 'https://attacker.example' });
    const url = new URL(href, 'https://local.test');
    expect(url.origin).toBe('https://local.test');
    expect(url.pathname).toBe('/projects/A%2FB%20%3F%23/work/runs/run%2F%3F%23');
    expect(url.searchParams.get('attention')).toBe(approval.id);
    expect(readAttentionSelection(url.searchParams)).toEqual(selection);
    expect(attentionHref('A', preparation)).toMatch(/^\/projects\/A\/work\?/);
  });
  test.each(['attention=x', 'attention=x&attention=x&attention_source=workflow&attention_kind=approval&attention_target=prd',
    'attention=x&attention_source=other&attention_kind=approval&attention_target=prd'])('rejects ambiguous selection %s', query => {
    expect(readAttentionSelection(new URLSearchParams(query))).toEqual({ invalid: true });
  });
});

describe('exact current resource binding', () => {
  test('selects only the matching current workflow item', () => expect(resolve()).toMatchObject({ status: 'current', target: 'attention' }));
  test.each(['loading', 'stale', 'error'])('%s state cannot select a current resource', freshness => {
    expect(resolve({ freshness }).status).not.toBe('current');
  });
  test.each([
    ['new run', data => { data.state.run_id = 'run-2'; data.team_workspace.mission.run_id = 'run-2'; }],
    ['other project', data => { data.state.project_key = 'B'; }],
    ['historical mission', data => { data.team_workspace.mission.read_only = true; }],
    ['ended run', data => { data.active_run = false; }],
    ['new gate', data => { data.team_workspace.attention_request = { id: 'approval:rfc', kind: 'approval', target_id: 'rfc' }; }],
    ['new intent at same gate', data => { data.team_workspace.attention_request.intent_commit = { status: 'discussing' }; }],
    ['mismatched target', data => { data.team_workspace.attention_request.target_id = 'rfc'; }],
  ])('%s never substitutes a new task or gate', (_label, update) => {
    const data = state(); update(data);
    expect(resolve({ data })).toEqual({ status: 'changed' });
  });
  test.each(['committing', 'ambiguous'])('intent recovery %s focuses its actual blocker surface', status => {
    const data = state();
    data.team_workspace.attention_request = { id: 'intent-recovery:change-1', kind: 'blocker', target_id: 'prd', intent_commit: { status } };
    const recovery = { ...approval, id: 'workflow:run-1:intent-recovery:change-1', kind: 'needs_input' };
    expect(resolve({ data, selection: recovery, projects: [project([recovery])] }))
      .toMatchObject({ status: 'current', target: 'blocker' });
  });
  test('a missing current ID is changed, not an invitation to open the first item', () => {
    expect(resolve({ projects: [project([blocked])] })).toEqual({ status: 'changed' });
    expect(resolve({ runId: '' })).toEqual({ status: 'changed' });
  });
  test('keeps unavailable distinct from no-longer-current', () => {
    expect(resolve({ projects: [{ ...project(), attention: null }] })).toEqual({ status: 'unavailable' });
    expect(resolve({ projects: [] })).toEqual({ status: 'unavailable' });
  });
  test('readiness requires the same execution and step; another step is not substituted', () => {
    const projects = [project([preparation])];
    const data = state(); data.active_run = false;
    data.team_workspace.attention_request = { id: 'project-preparation:prep-1:commands', kind: 'project_preparation', step_id: 'commands' };
    const selection = { ...preparation };
    expect(resolve({ projects, selection, data, runId: '' })).toMatchObject({ status: 'current', target: 'preparation' });
    data.team_workspace.attention_request = { id: 'project-preparation:prep-2:commands', kind: 'project_preparation', step_id: 'commands' };
    expect(resolve({ projects, selection, data, runId: '' })).toEqual({ status: 'changed' });
    projects[0].readiness.execution.id = 'prep-2';
    expect(resolve({ projects, selection, data, runId: '' })).toEqual({ status: 'changed' });
  });
  test.each([null, { id: 'approval:prd', kind: 'approval', target_id: 'prd' }])('resolved preparation cannot remain current when Work moved to %s', attention => {
    const data = state(); data.team_workspace.attention_request = attention;
    expect(resolve({ data, selection: preparation, runId: '', projects: [project([preparation])] }))
      .toEqual({ status: attention ? 'changed' : 'unavailable' });
  });
  test('fresh unconfirmed mode without an exposed input control is unavailable, not resolved', () => {
    const mode = { ...preparation, id: 'readiness:unstarted:project_mode', target_id: 'project_mode' };
    const entry = project([mode]);
    entry.readiness = { lifecycle: 'attention_required', project_mode: 'unconfirmed',
      steps: [{ id: 'project_mode', status: 'needs_input' }] };
    const data = state();
    data.team_workspace.attention_request = null;
    expect(resolve({ projects: [entry], selection: mode, data, runId: '' })).toEqual({ status: 'unavailable' });
  });
  test('malformed readiness step collection stays unavailable', () => {
    const entry = project([preparation]); entry.readiness.steps = {};
    expect(resolve({ selection: preparation, runId: '', projects: [entry] })).toEqual({ status: 'unavailable' });
  });
  test('assistant focus follows the displayed proposal identity and clarification state', () => {
    const projects = [project([proposal])];
    const data = state(); data.team_workspace.proposed_task = { id: 'draft-1', needs_clarification: true };
    expect(resolve({ projects, data, selection: proposal, runId: '' })).toMatchObject({ status: 'current', target: 'draft' });
    data.project_assistant = { proposed_task: { id: 'draft-2', needs_clarification: true } };
    expect(resolve({ projects, data, selection: proposal, runId: '' })).toEqual({ status: 'changed' });
    delete data.project_assistant; data.team_workspace.proposed_task.run_id = 'run-2';
    expect(resolve({ projects, data, selection: proposal, runId: '' })).toEqual({ status: 'changed' });
  });
});
