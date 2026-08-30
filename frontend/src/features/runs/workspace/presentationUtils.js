export const WORK_VIEWS = ['plan', 'activity', 'execution', 'messages', 'delivery'];

export function tone(status) {
  if (['complete', 'completed', 'done', 'approved', 'success'].includes(status)) return 'success';
  if (['blocked', 'failed', 'error', 'stopped'].includes(status)) return 'danger';
  if ([
    'active', 'running', 'doing', 'working', 'reviewing', 'fixing',
    'review-needed', 'needs_input', 'attention_required', 'changes_requested',
  ].includes(status)) return 'active';
  return 'neutral';
}

export function translated(t, key, fallback, params = {}) {
  const value = t(key, params);
  return value === key ? fallback : value;
}

export function statusText(status, t) {
  return translated(t, `work.team.status.${status}`, status || t('work.team.status.up_next'));
}

export function reviewGateName(gate, t) {
  if (!gate) return '';
  const key = `work.stage.${gate}`;
  const value = t(key);
  return value !== key ? value : String(gate).toUpperCase();
}

export function recommendationReasonText(reason, t) {
  if (!reason) return '';
  if (typeof reason === 'string') return reason;
  if (reason.kind === 'clean') return t('review.recommendation.reason.clean');
  return reason.text || reason.kind || '';
}
