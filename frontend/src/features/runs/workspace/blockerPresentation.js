export function isArtifactRevisionBlocker(attention) {
  return /^Artifact [a-z_]+ changed\. Derived approvals and artifacts were invalidated\./m.test(String(attention?.reason || ''));
}

export function blockerSummary(attention, t) {
  if (isArtifactRevisionBlocker(attention)) return t('work.blocker.artifactRevision');
  const reason = String(attention?.reason || '');
  return t(/\[rate_limit(?:[,\]])/.test(reason) ? 'work.blocker.rateLimit' : 'work.blocker.paused');
}
