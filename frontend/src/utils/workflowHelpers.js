export function stepTitle(step, t) {
  if (!step) return '';
  if (step.approval === 'takeover') return t('work.pipeline.takeover');
  const stageKey = `work.stage.${step.key}`;
  const translated = t(stageKey);
  if (translated && translated !== stageKey) return translated;
  return step.title || '';
}

export function statusLabel(step, t) {
  if (!step) return '';
  const key = `work.pipeline.status.${step.status}`;
  const translated = t(key);
  return translated === key ? (step.status_label || step.status) : translated;
}
