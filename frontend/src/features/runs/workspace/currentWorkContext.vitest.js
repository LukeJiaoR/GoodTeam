import { describe, expect, it, vi } from 'vitest';
import { currentWorkContext, focusWorkContext } from './currentWorkContext';

const t = (key) => key;
const workspace = { schema_version: 'team_workspace.v1', mission: { project_key: 'A', status: 'working', source: 'workflow' } };
const context = (overrides = {}) => currentWorkContext({ workspace, projectKey: 'A', title: 'Keep APIs stable', t, ...overrides });

describe('current work presentation', () => {
  it('uses execution truth, not a completed message or percentage', () => {
    expect(context()).toMatchObject({ status: 'working', target: 'execution' });
  });
  it.each(['stale', 'error', 'loading'])('never presents %s data as current success', (freshness) => {
    expect(context({ freshness })).toMatchObject({ status: 'unknown', target: 'refresh' });
  });
  it('rejects a foreign project projection', () => {
    expect(context({ projectKey: 'B' })).toMatchObject({ status: 'unknown', target: 'refresh' });
  });
  it('does not invent an available state for missing or unsupported projection', () => {
    expect(context({ workspace: null })).toMatchObject({ status: 'unknown', target: 'refresh' });
    expect(context({ workspace: { schema_version: 'future' } })).toMatchObject({ status: 'unknown' });
  });
  it('history ignores actionable current attention', () => {
    expect(context({ historical: true, workspace: { ...workspace, attention_request: { kind: 'approval', actions: ['approve'] } } }))
      .toMatchObject({ status: 'historical', target: 'outcomes' });
  });
  it('approval is a location, not a new approval command', () => {
    const value = { ...workspace, attention_request: { kind: 'approval', actions: ['approve'] } };
    expect(context({ workspace: value })).toMatchObject({ status: 'awaiting_review', target: 'attention' });
    expect(value.attention_request.actions).toEqual(['approve']);
  });
  it.each(['stale', 'ambiguous', 'committing'])('shows %s change recovery instead of a success', (status) => {
    expect(context({ workspace: { ...workspace, attention_request: { kind: 'approval', intent_commit: { status } } } }))
      .toMatchObject({ status: 'needs_input', reason: `work.current.intent.${status}`, target: 'attention' });
  });
  it('completed execution is not accepted delivery', () => {
    expect(context({ workspace: { ...workspace, mission: { ...workspace.mission, status: 'completed' } } }))
      .toMatchObject({ status: 'ended', reason: 'work.current.ended', target: 'outcomes' });
  });
  it('a cancelled draft cannot invite starting the old task', () => {
    expect(context({ workspace: { ...workspace, mission: { source: 'task_assistant' }, proposed_task: { status: 'cancelled' } } }))
      .toMatchObject({ status: 'idle', target: 'composer' });
  });
  it('a delayed focus cannot jump into a different run', () => {
    let callback;
    vi.stubGlobal('requestAnimationFrame', (fn) => { callback = fn; });
    const root = document.createElement('main');
    root.dataset.contextScope = 'A:run-1';
    root.innerHTML = '<section class="team-attention"></section>';
    document.body.append(root);
    const focus = vi.spyOn(root.firstChild, 'focus');
    focusWorkContext(root, 'attention');
    root.dataset.contextScope = 'A:run-2';
    callback();
    expect(focus).not.toHaveBeenCalled();
    root.remove();
    vi.unstubAllGlobals();
  });
});
