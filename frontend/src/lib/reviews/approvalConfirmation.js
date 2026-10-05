// Presentation/confirmation only: backend policy and evidence stay authoritative.
export function requiresInformedApproval(recommendation) {
  return recommendation?.verdict !== 'approve';
}

export function canConfirmApproval({ disabled = false, requiresReason = true, note = '' } = {}) {
  return !disabled && (!requiresReason || (typeof note === 'string' && Boolean(note.trim())));
}

export function approvalConfirmationKey(value) {
  return JSON.stringify(value, (_key, item) => (
    item && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]]))
      : item
  ));
}
