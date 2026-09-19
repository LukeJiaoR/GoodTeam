import { describe, expect, it } from 'vitest';
import { availableChangeTarget, changeContextStatus, matchesSavedChange } from './conversationChangeContext';

const target = { run_id: 'run-1', target_type: 'review', target_id: 'rfc', artifact_key: 'rfc', artifact_version: 'v1' };
const mission = { project_key: 'A', run_id: 'run-1', source: 'workflow', status: 'awaiting_approval' };
const attention = { kind: 'approval', target_id: 'rfc', actions: ['request_changes'], approval_brief: { schema_version: 'manager_approval_brief.v1', approval_target: target } };
const context = { mission, attention, projectKey: 'A' };
describe('bounded conversational change entry', () => {
  it('uses the exact existing actionable review target', () => {
    expect(availableChangeTarget(context)).toEqual({ ...target, approval: 'rfc', project_key: 'A' });
  });
  it.each([
    { mission: { ...mission, read_only: true } },
    { mission: { ...mission, project_key: 'B' } },
    { mission: { ...mission, run_id: 'old' } },
    { mission: { ...mission, status: 'completed' } },
    { attention: { ...attention, actions: ['review'] } },
    { attention: { ...attention, kind: 'blocker' } },
    { attention: { ...attention, intent_commit: { id: 'pending' } } },
    { attention: { ...attention, approval_brief: null } },
  ])('does not invent a change capability for unsafe contexts', (patch) => {
    expect(availableChangeTarget({ ...context, ...patch })).toBeNull();
  });
  it('requires an exact durable acknowledgement before clearing text', () => {
    const wanted = availableChangeTarget(context);
    const intent = { id: 'i', project_key: 'A', status: 'discussing', mission, target, draft_text: '  Web only.\n' };
    expect(matchesSavedChange({ intent_commit: intent }, wanted, intent.draft_text)).toBe(true);
    for (const patch of [{ project_key: 'B' }, { draft_text: 'Web only.' }, { status: 'committed' }, { target: { ...target, artifact_version: 'v2' } }]) {
      expect(matchesSavedChange({ intent_commit: { ...intent, ...patch } }, wanted, intent.draft_text)).toBe(false);
    }
    expect(matchesSavedChange({}, wanted, intent.draft_text)).toBe(false);
  });
  it('unknown and committed remain distinct from successful execution', () => {
    expect(changeContextStatus({ status: 'invented_success' })).toBe('unknown');
    expect(changeContextStatus({ status: 'committed' })).toBe('committed');
    expect(changeContextStatus(null)).toBe('message');
  });
});
