import { describe, expect, test } from 'vitest';
import { attentionHref, normalizeProjectAttention, readAttentionSelection, resolveAttentionFocus } from './projectAttention';
import { normalizeProjectsPayload } from './projectList';

const preparation = { id: 'readiness:prep-1:commands', kind: 'needs_input', source: 'readiness',
  target_id: 'commands', title: 'commands', reason: 'needs_input' };
const proposal = { id: 'assistant:proposal:draft-1', kind: 'needs_input', source: 'assistant',
  target_id: 'draft-1', title: 'Improve search', reason: 'clarification_required' };
function project(items = [preparation]) {
  return { project: { project_key: 'A', display_name: 'A', root: '/work/A' },
    has_active_batch: false, attention_count: 1,
    readiness: { lifecycle: 'ready', execution: { id: 'prep-1' }, steps: [{ id: 'commands', status: 'needs_input' }] },
    attention: { schema_version: 'project_attention.v1', items, counts: { blocked: 0, needs_input: items.length, total: items.length } } };
}
function state() {
  return { project_assistant: { project_key: 'A' },
    team_workspace: { schema_version: 'team_workspace.v1',
      attention_request: { id: 'project-preparation:prep-1:commands', kind: 'project_preparation', step_id: 'commands' } } };
}
const resolve = (overrides = {}) => resolveAttentionFocus({ selection: preparation, projectKey: 'A',
  projects: [project()], data: state(), ...overrides });

test('validates attention counts while keeping launcher navigation available on malformed attention', () => {
  const entry = project();
  expect(normalizeProjectAttention(entry.attention, 'A')?.counts.total).toBe(1);
  entry.attention.counts.total = 0;
  expect(normalizeProjectsPayload({ projects: [entry] })[0].attention).toBeNull();
});

test('attention links preserve the exact target and encode project identity', () => {
  const url = new URL(attentionHref('A /', preparation), 'https://local.test');
  expect(url.pathname).toBe('/projects/A%20%2F/work');
  expect(readAttentionSelection(url.searchParams)).toMatchObject({ id: preparation.id, target_id: preparation.target_id });
});

describe('exact current resource binding', () => {
  test('readiness requires the same execution and step; another step is not substituted', () => {
    const projects = [project([preparation])];
    const data = state(); data.active_run = false;
    data.team_workspace.attention_request = { id: 'project-preparation:prep-1:commands', kind: 'project_preparation', step_id: 'commands' };
    const selection = { ...preparation };
    expect(resolve({ projects, selection, data })).toMatchObject({ status: 'current', target: 'preparation' });
    data.team_workspace.attention_request = { id: 'project-preparation:prep-2:commands', kind: 'project_preparation', step_id: 'commands' };
    expect(resolve({ projects, selection, data })).toEqual({ status: 'changed' });
    projects[0].readiness.execution.id = 'prep-2';
    expect(resolve({ projects, selection, data })).toEqual({ status: 'changed' });
  });
  test.each([null, { id: 'project-mode:1', kind: 'project_mode' }])('resolved preparation cannot remain current when Work moved to %s', attention => {
    const data = state(); data.team_workspace.attention_request = attention;
    expect(resolve({ data, selection: preparation, projects: [project([preparation])] }))
      .toEqual({ status: attention ? 'changed' : 'unavailable' });
  });
  test('fresh unconfirmed mode without an exposed input control is unavailable, not resolved', () => {
    const mode = { ...preparation, id: 'readiness:unstarted:project_mode', target_id: 'project_mode' };
    const entry = project([mode]);
    entry.readiness = { lifecycle: 'attention_required', project_mode: 'unconfirmed',
      steps: [{ id: 'project_mode', status: 'needs_input' }] };
    const data = state();
    data.team_workspace.attention_request = null;
    expect(resolve({ projects: [entry], selection: mode, data })).toEqual({ status: 'unavailable' });
  });
  test('malformed readiness step collection stays unavailable', () => {
    const entry = project([preparation]); entry.readiness.steps = {};
    expect(resolve({ selection: preparation, projects: [entry] })).toEqual({ status: 'unavailable' });
  });
  test('assistant focus follows the displayed proposal identity and clarification state', () => {
    const projects = [project([proposal])];
    const data = state(); data.project_assistant.proposed_task = { id: 'draft-1', needs_clarification: true };
    expect(resolve({ projects, data, selection: proposal })).toMatchObject({ status: 'current', target: 'draft' });
    data.project_assistant = { ...data.project_assistant, proposed_task: { id: 'draft-2', needs_clarification: true } };
    expect(resolve({ projects, data, selection: proposal })).toEqual({ status: 'changed' });
    data.project_assistant.proposed_task = { ...data.project_assistant.proposed_task, id: 'draft-1', status: 'cancelled' };
    expect(resolve({ projects, data, selection: proposal })).toEqual({ status: 'changed' });
  });
});
