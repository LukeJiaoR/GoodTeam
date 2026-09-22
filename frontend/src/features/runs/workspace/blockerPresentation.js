export function blockerSummary(attention, t) {
  const reason = String(attention?.reason || '');
  return t(/\[rate_limit(?:[,\]])/.test(reason) ? 'work.blocker.rateLimit' : 'work.blocker.paused');
}
