import { approvalConfirmationKey, requiresInformedApproval } from '../../../lib/reviews/approvalConfirmation.js';

// This is a UI confirmation contract, not permission to bypass a server gate.
export function requiresInformedWorkApproval(brief) {
  return requiresInformedApproval(brief?.recommendation);
}

export function workApprovalContext({ brief, gate, projectKey }) {
  const target = brief?.approval_target;
  const nonempty = (value) => typeof value === 'string' && Boolean(value.trim());
  if (brief?.schema_version !== 'manager_approval_brief.v1'
    || !nonempty(projectKey) || !nonempty(gate) || brief.approval !== gate
    || !target || target.target_type !== 'review' || target.target_id !== gate
    || !nonempty(target.run_id) || !nonempty(target.artifact_key)
    || !nonempty(target.artifact_version) || target.artifact_version === 'missing') return null;

  return {
    // Include the displayed evidence, not only the artifact version: a revised
    // recommendation for the same artifact requires a new informed decision.
    key: approvalConfirmationKey([projectKey, gate, brief]),
    target: { ...target },
    requiresReason: requiresInformedWorkApproval(brief),
  };
}
