import { translate } from '../../i18n';

export function errorMessage(language, error) {
  const code = String(error?.code || '');
  if (code) {
    const key = `projects.errors.${code}`;
    const localized = translate(language, key);
    if (localized !== key) return localized;
  }
  return error?.message || translate(language, 'product.errors.generic');
}

export function recoveryIds(recovery) {
  const source = Array.isArray(recovery)
    ? recovery
    : recovery && typeof recovery === 'object'
      ? (Array.isArray(recovery.actions) ? recovery.actions : Object.values(recovery))
      : [];
  return source.flatMap((item) => {
    if (typeof item === 'string') return [item];
    if (!item || typeof item !== 'object') return [];
    return [item.id || item.action || item.code].filter(Boolean).map(String);
  });
}

export function recoveryLabel(language, recoveryId) {
  const key = `projects.recovery.${recoveryId}`;
  const localized = translate(language, key);
  return localized === key ? String(recoveryId).replaceAll('_', ' ') : localized;
}

export function canRetryStep(step) {
  if (step?.status !== 'failed') return false;
  const ids = recoveryIds(step?.recovery);
  return ids.some((id) => ['retry', 'retry_readiness', 'retry_request'].includes(id));
}
