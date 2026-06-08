import {
  CheckCircle,
  Clock,
  Info,
  RefreshCw,
  XCircle,
} from 'lucide-react';

export function getBadgeStyle(status) {
  const norm = String(status || '').toLowerCase().trim();

  if (['complete', 'approved', 'done', 'pass', 'success', 'active', 'running'].includes(norm)) {
    return {
      bg: 'bg-status-success-bg',
      text: 'text-status-success-text',
      border: 'border-status-success/30',
      icon: CheckCircle
    };
  }
  if (['artifact ready', 'artifact_ready', 'ready'].includes(norm)) {
    return {
      bg: 'bg-status-info-bg',
      text: 'text-status-info-text',
      border: 'border-status-info/30',
      icon: RefreshCw
    };
  }
  if (['pending', 'blocked', 'changes_requested', 'changes requested', 'review-needed', 'review needed', 'await_prd_approval', 'await_rfc_approval', 'await_staging_approval', 'await_security_approval', 'await_release_approval', 'await_takeover_approval'].includes(norm)) {
    return {
      bg: 'bg-status-warning-bg',
      text: 'text-status-warning-text',
      border: 'border-status-warning/30',
      icon: Clock
    };
  }
  if (['failed', 'error', 'rejected'].includes(norm)) {
    return {
      bg: 'bg-status-danger-bg',
      text: 'text-status-danger-text',
      border: 'border-status-danger/30',
      icon: XCircle
    };
  }
  return {
    bg: 'bg-surface-sunken',
    text: 'text-text-muted',
    border: 'border-border-subtle/30',
    icon: Info
  };
}

export function getStatusLabel(status, t = (key) => ({
  'status.approved': 'Approved',
  'status.pending': 'Pending',
  'status.complete': 'Complete',
  'status.active': 'Active',
  'status.artifactReady': 'Artifact Ready',
  'chrome.noActiveRun': 'No Activity',
}[key] || key)) {
  const norm = String(status || '').toLowerCase().trim();
  if (norm === 'approved') return `✓ ${t('status.approved')}`;
  if (norm === 'pending') return `⏳ ${t('status.pending')}`;
  if (norm === 'complete') return `✓ ${t('status.complete')}`;
  if (norm === 'active' || norm === 'running') return t('status.active');
  if (norm === 'artifact ready' || norm === 'artifact_ready') return t('status.artifactReady');
  if (norm === 'no activity' || norm === 'no_activity' || norm === 'no_active_run') return t('chrome.noActiveRun');
  return status.replace(/_/g, ' ');
}
