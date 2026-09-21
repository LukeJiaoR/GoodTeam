// Presentation only. Never interpret a message or manufacture an executable action.
import { statusText, translated } from './presentationUtils';

export function currentWorkContext({ workspace, projectKey, title, freshness = 'ready', historical = false, t }) {
  const mission = workspace?.mission;
  const identityMismatch = Boolean(mission?.project_key && mission.project_key !== projectKey);
  const base = { title: title || t('work.current.emptyTitle'), status: 'unknown', target: null };
  const result = (status, reason, target = null) => ({ ...base, status, reason, target });
  if (identityMismatch || freshness !== 'ready') {
    return result('unknown', t(freshness === 'stale' ? 'work.current.stale' : 'work.current.unknown'), 'refresh');
  }
  if (historical || mission?.read_only) return result('historical', t('work.current.readOnly'), 'outcomes');
  if (!workspace || workspace.schema_version !== 'team_workspace.v1') {
    return result('unknown', t('work.current.unknown'), 'refresh');
  }
  const attention = workspace.attention_request;
  const intent = attention?.intent_commit;
  if (intent && ['stale', 'ambiguous', 'committing'].includes(intent.status)) {
    return result('needs_input', t(`work.current.intent.${intent.status}`), 'attention');
  }
  if (attention?.kind === 'blocker') {
    return result('blocked', translated(t, `work.manager.attentionReasons.${attention.reason}`, attention.reason || t('work.current.blocked')), 'attention');
  }
  if (['pending', 'stale', 'failed'].includes(workspace.pending_team_decision?.status)) {
    return result('needs_input', t(workspace.pending_team_decision.status === 'pending'
      ? 'work.current.teamDecision' : 'assistant.teamStale'), 'team');
  }
  if (intent && ['discussing', 'ready_to_confirm'].includes(intent.status)) {
    return result('needs_input', t('work.current.changePending'), 'attention');
  }
  if (attention?.kind === 'approval') {
    return result('awaiting_review', t('work.manager.attentionReasons.approval_required'), 'attention');
  }
  if (attention) {
    const reason = attention.kind === 'project_preparation'
      ? t(`work.taskAssistant.preparation.${attention.input_requirements?.kind || 'project_preparation'}`)
      : translated(t, `work.manager.attentionReasons.${attention.reason}`, t('work.current.inputNeeded'));
    return result('needs_input', reason, 'attention');
  }
  const draft = workspace.proposed_task;
  if (draft && !draft.run_id && draft.status !== 'cancelled') {
    return result('needs_input', t('work.current.draftPending'), 'draft');
  }
  if (Object.values(workspace.assistant_requests || {}).some((request) => ['failed', 'interrupted'].includes(request.status))) {
    return result('needs_input', t('work.current.requestRecovery'), 'recovery');
  }
  if (!mission || mission.source === 'task_assistant') return result('idle', t('work.current.start'), 'composer');
  if (['completed', 'complete', 'done', 'stopped', 'failed', 'cancelled'].includes(mission.status)) {
    return result('ended', t('work.current.ended'), 'outcomes');
  }
  if (['working', 'running', 'active', 'reviewing', 'fixing', 'waiting', 'blocked'].includes(mission.status)) {
    return result(mission.status, statusText(mission.status, t), mission.status === 'blocked' ? 'attention' : 'execution');
  }
  return result('unknown', t('work.current.unknown'), 'refresh');
}

// Find the existing surface; this deliberately does not invoke an API mutation.
export const WORK_CONTEXT_TARGETS = {
  attention: { tab: 'manager', selector: '.team-attention, .assistant-project-mode, .team-task-proposal' },
  team: { tab: 'manager', selector: '.assistant-team-decision' },
  draft: { tab: 'manager', selector: '.team-task-proposal' },
  recovery: { tab: 'manager', selector: '.assistant-request' },
  composer: { tab: 'manager', selector: '.team-manager-dock textarea, .work-start-panel textarea' },
  outcomes: { tab: 'outcomes', selector: '.team-pane--outcomes' },
  execution: { tab: 'execution', selector: '.team-pane--execution' },
};

export function focusWorkContext(root, target, { reducedMotion = false } = {}) {
  const destination = WORK_CONTEXT_TARGETS[target];
  if (!root || !destination) return;
  const scope = root.dataset.contextScope;
  const tab = root.querySelector(`#team-tab-${destination.tab}`);
  // The existing mobile controller owns which pane is visible.
  if (tab && tab.getAttribute('aria-selected') === 'false') tab.click();
  globalThis.requestAnimationFrame(() => {
    if (!root.isConnected || root.dataset.contextScope !== scope) return;
    const node = root.querySelector(destination.selector);
    if (!node || node.closest('[hidden], [inert]')) return;
    node.scrollIntoView?.({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'nearest' });
    if (!node.matches('input, textarea, button, a[href]')) node.setAttribute('tabindex', '-1');
    node.focus?.({ preventScroll: true });
  });
}
