export function fileName(path) {
  if (typeof path !== 'string') return '';
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).at(-1) || path;
}

export function isAbsoluteFilePath(value) {
  return typeof value === 'string' && /^(?:\/|~\/|[A-Za-z]:[\\/]|\\\\)/.test(value);
}

const DOCUMENT_KINDS = new Set([
  'takeover_context', 'task_ledger', 'takeover', 'prd', 'rfc', 'qa',
  'security_review', 'implementation', 'release', 'plan', 'review',
]);

export function documentTitle(key, t) {
  if (DOCUMENT_KINDS.has(key)) return t(`documents.kind.${key}`);
  return String(key || '').replace(/[_-]+/g, ' ').replace(/^./, (letter) => letter.toUpperCase());
}
