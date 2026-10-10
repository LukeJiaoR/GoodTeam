/* eslint-disable react-refresh/only-export-components -- helper functions are exported for unit tests. */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock,
  Code,
  Copy,
  ExternalLink,
  Eye,
  FileCode,
  FileText,
  Link as LinkIcon,
  ShieldAlert,
  ShieldCheck,
  Tag,
  User,
  X,
} from 'lucide-react';
import EvidenceSummary from '../../../components/EvidenceSummary';
import Button from '../../../components/ui/Button';
import FileLocation from '../../../components/ui/FileLocation';
import { documentTitle } from '../../../lib/presentation/filePresentation';
import MarkdownView from '../../../components/ui/MarkdownView';
import StatusBadge from '../../../components/ui/StatusBadge';
import { routes } from '../../../lib/routing/routes';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

const TEAM_STATUS_ALIASES = {
  canceled: 'stopped',
  complete: 'completed',
  doing: 'working',
  review: 'reviewing',
};

const NON_INFORMATIVE_VALUES = new Set([
  '', '-', '–', '—', 'n/a', 'na', 'none', 'not set', 'null', 'undefined',
]);
const SCAFFOLD_DESCRIPTION = /^workflow\s+stage\s+task\s+for\s+[^.]+\.?$/i;

function meaningfulText(value) {
  if (value === null || value === undefined) return '';
  const text = String(value).trim();
  return NON_INFORMATIVE_VALUES.has(text.toLowerCase()) ? '' : text;
}

function taskTitle(task) {
  const taskId = meaningfulText(task?.id);
  return [task?.title, task?.objective, task?.name]
    .map(meaningfulText)
    .find((title) => title && title !== taskId) || '';
}

function projectFacingDescription(value) {
  const description = meaningfulText(value);
  return description && !SCAFFOLD_DESCRIPTION.test(description) ? description : '';
}

function translatedValue(t, key) {
  const value = meaningfulText(t(key));
  return value && value !== key ? value : '';
}

function localizedTimestamp(value, language) {
  const timestamp = Date.parse(meaningfulText(value));
  if (!Number.isFinite(timestamp)) return '';
  try {
    return new Intl.DateTimeFormat(language, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(timestamp));
  } catch {
    return '';
  }
}

export function resolveTaskDependencies(task, tasks) {
  if (!Array.isArray(task?.dependencies) || !Array.isArray(tasks)) return [];

  const tasksById = new Map();
  for (const candidate of tasks) {
    const candidateId = meaningfulText(candidate?.id);
    if (!candidateId) continue;
    const matches = tasksById.get(candidateId) || [];
    matches.push(candidate);
    tasksById.set(candidateId, matches);
  }

  const seen = new Set();
  const resolved = [];
  for (const dependency of task.dependencies) {
    const dependencyId = meaningfulText(
      dependency && typeof dependency === 'object' ? dependency.id : dependency,
    );
    if (!dependencyId || seen.has(dependencyId)) continue;
    seen.add(dependencyId);

    const matches = tasksById.get(dependencyId) || [];
    if (matches.length !== 1) continue;
    const title = taskTitle(matches[0]);
    if (!title || title === dependencyId) continue;
    resolved.push({ id: dependencyId, title });
  }
  return resolved;
}

function statusTone(status) {
  if (['done', 'complete', 'completed', 'approved'].includes(status)) return 'success';
  if (['blocked', 'failed', 'rejected', 'canceled', 'stopped'].includes(status)) return 'danger';
  if ([
    'in_progress', 'doing', 'active', 'working', 'review', 'reviewing',
    'changes_requested', 'fixing',
  ].includes(status)) return 'active';
  if (['todo', 'up_next', 'pending', 'waiting'].includes(status)) return 'pending';
  return 'neutral';
}

function taskStatusLabel(status, t) {
  const planKey = `work.plan.status.${status}`;
  const planLabel = t(planKey);
  if (planLabel !== planKey) return planLabel;

  const teamStatus = TEAM_STATUS_ALIASES[status] || status;
  const teamKey = `work.team.status.${teamStatus}`;
  const teamLabel = t(teamKey);
  return teamLabel === teamKey ? '' : teamLabel;
}

function reviewTone(review) {
  if (review?.is_actionable) return 'active';
  if (review?.decision === 'approved' || review?.status === 'approved') return 'success';
  if (review?.decision === 'rejected') return 'danger';
  if (review?.decision === 'changes_requested') return 'warning';
  return 'neutral';
}

function asReviewObject(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return { value }; }
}

export function findMatchingReview(task, reviews) {
  if (!task || !Array.isArray(reviews) || !reviews.length) return null;

  // Manager-centered Work passes the durable review identity projected for
  // this exact outcome. An explicit blank means "this task has no review";
  // compatibility heuristics are only for legacy tasks that lack the field.
  if (Object.prototype.hasOwnProperty.call(task, 'review_target_id')) {
    const reviewTargetId = String(task.review_target_id || '').trim();
    if (!reviewTargetId) return null;
    return reviews.find((review) => String(review.approval || '') === reviewTargetId) || null;
  }

  // 1. If any review is actionable, check if it belongs to this task's scope
  const actionable = reviews.find((r) => r.is_actionable);
  if (actionable) {
    if (task.role === 'architect' && ['rfc', 'security'].includes(actionable.approval)) {
      return actionable;
    }
    if (task.role === 'product_requirement' && actionable.approval === 'prd') {
      return actionable;
    }
    if (task.role === 'qa' && actionable.approval === 'staging') {
      return actionable;
    }
    if (task.role === 'release' && actionable.approval === 'release') {
      return actionable;
    }
    if (task.role === 'takeover' && actionable.approval === 'takeover') {
      return actionable;
    }
  }

  // 2. Exact ref matching
  const refs = [...(task.external_refs || []), ...(task.artifact_refs || [])];
  for (const ref of refs) {
    const cleanRef = String(ref).replace(/^stage:/, '').trim().toLowerCase();
    const match = reviews.find((r) => r.approval?.toLowerCase() === cleanRef || r.artifact?.toLowerCase() === cleanRef);
    if (match) return match;
  }

  // 3. Role mapping (prioritizing actionable or latest pending gate for that role)
  const roleMap = {
    product_requirement: ['prd'],
    architect: ['security', 'rfc'],
    implementation: ['implementation_handoff'],
    qa: ['staging'],
    release: ['release'],
    takeover: ['takeover'],
  };
  if (task.role && roleMap[task.role]) {
    const targetApprovals = roleMap[task.role];
    for (const target of targetApprovals) {
      const match = reviews.find((r) => r.approval?.toLowerCase() === target || r.artifact?.toLowerCase() === target);
      if (match && (match.is_actionable || target === targetApprovals[0])) return match;
    }
    const fallbackMatch = reviews.find((r) => targetApprovals.includes(r.approval?.toLowerCase()));
    if (fallbackMatch) return fallbackMatch;
  }

  const title = String(task.title || '').toLowerCase();
  if (title.includes('prd')) return reviews.find((r) => r.approval === 'prd') || null;
  if (title.includes('rfc') || title.includes('architect') || title.includes('security')) {
    return reviews.find((r) => r.approval === 'security') || reviews.find((r) => r.approval === 'rfc') || null;
  }
  if (title.includes('takeover')) return reviews.find((r) => r.approval === 'takeover') || null;
  if (title.includes('release')) return reviews.find((r) => r.approval === 'release') || null;
  if (title.includes('qa') || title.includes('verify') || title.includes('staging')) return reviews.find((r) => r.approval === 'staging') || null;

  return null;
}

export default function WorkTaskDetailDialog({
  language,
  task,
  tasks = [],
  reviews = [],
  projectKey = '',
  onClose,
  onOpenReview,
  t,
}) {
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const titleId = useId();
  const descId = useId();

  const review = useMemo(() => findMatchingReview(task, reviews), [task, reviews]);
  const hasArtifact = Boolean(review?.content && String(review.content).trim().length > 0);
  const isActionable = Boolean(review?.is_actionable);

  const [activeTab, setActiveTab] = useState(() => (isActionable ? 'approval' : 'details'));
  const [previewMode, setPreviewMode] = useState('rendered');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        if (event.target?.closest?.('.file-location[open]')) return;
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key === 'Tab' && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown, true);
    closeButtonRef.current?.focus();

    return () => {
      window.removeEventListener('keydown', handleKeyDown, true);
    };
  }, [onClose]);

  if (!task) return null;

  const title = taskTitle(task) || t('work.plan.taskDetails');
  const description = projectFacingDescription(task.description);
  const role = meaningfulText(task.role);
  const status = meaningfulText(task.status);
  const priority = meaningfulText(task.priority);
  const roleLabel = role ? translatedValue(t, `work.plan.role.${role}`) : '';
  const statusLabel = status ? taskStatusLabel(status, t) : '';
  const priorityLabel = priority
    ? translatedValue(t, `work.plan.priority.${priority}`)
    : '';
  const dependencies = resolveTaskDependencies(task, tasks);
  const acceptanceCriteria = Array.isArray(task.acceptance_criteria)
    ? task.acceptance_criteria.map(meaningfulText).filter(Boolean)
    : [];
  const createdAt = localizedTimestamp(task.created_at, language);
  const updatedAt = localizedTimestamp(task.updated_at, language);
  const hasBadges = Boolean(statusLabel || priorityLabel || roleLabel);
  const hasAttributes = Boolean(
    (review && (roleLabel || statusLabel || priorityLabel))
    || dependencies.length
    || createdAt
    || updatedAt,
  );
  const primaryOutcomeLabel = meaningfulText(task.primary_outcome?.label);

  const handleCopyArtifact = async () => {
    if (!review?.content) return;
    try {
      await navigator.clipboard.writeText(review.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore clipboard error
    }
  };

  const recommendation = review?.recommendation && typeof review.recommendation === 'object'
    ? review.recommendation
    : null;
  const evidence = asReviewObject(review?.evidence_snapshot);

  return (
    <div
      className="task-detail-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="task-detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={activeTab === 'details' && description ? descId : undefined}
      >
        <header className="task-detail-header">
          <div className="task-detail-header-lead">
            <h2 id={titleId} className="task-detail-title">
              {title}
            </h2>
          </div>
          <div className="task-detail-header-actions">
            <button
              ref={closeButtonRef}
              type="button"
              className="task-detail-close-btn"
              onClick={onClose}
              aria-label={t('work.plan.close')}
            >
              <X size={18} />
            </button>
          </div>
        </header>

        {isActionable ? (
          <div className="task-detail-action-banner">
            <ShieldAlert size={16} className="task-detail-action-banner-icon" />
            <span>{t('work.plan.approval.actionableNotice')}</span>
            {activeTab !== 'approval' ? (
              <button
                type="button"
                className="task-detail-banner-action"
                onClick={() => setActiveTab('approval')}
              >
                {t('work.plan.approval.goToApproval')} →
              </button>
            ) : null}
          </div>
        ) : null}

        {review ? (
          <nav className="task-detail-tabs" aria-label={t('work.plan.taskDetails')}>
            <button
              type="button"
              className={`task-detail-tab ${activeTab === 'details' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('details')}
            >
              <Tag size={14} />
              {t('work.plan.tabs.details')}
            </button>
            <button
              type="button"
              className={`task-detail-tab ${activeTab === 'preview' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('preview')}
            >
              <FileText size={14} />
              {t('work.plan.tabs.preview')}
              {hasArtifact ? <span className="task-detail-tab-dot-success" title="Artifact ready" /> : null}
            </button>
            <button
              type="button"
              className={`task-detail-tab ${activeTab === 'approval' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('approval')}
            >
              <ShieldCheck size={14} />
              {t('work.plan.tabs.approval')}
              {isActionable ? <span className="task-detail-tab-dot-action" title="Awaiting approval" /> : null}
            </button>
          </nav>
        ) : hasBadges ? (
          <div className="task-detail-badges">
            {statusLabel ? (
              <StatusBadge tone={statusTone(status)}>
                {statusLabel}
              </StatusBadge>
            ) : null}
            {priorityLabel ? (
              <span className={`task-detail-priority-tag priority-${priority}`}>
                {priorityLabel}
              </span>
            ) : null}
            {roleLabel ? (
              <span className="task-detail-role-tag">
                <User size={13} />
                {roleLabel}
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="task-detail-body">
          {activeTab === 'details' && (
            <>
              {description ? (
                <section className="task-detail-section">
                  <h3>{t('work.plan.description')}</h3>
                  <p id={descId} className="task-detail-description">{description}</p>
                </section>
              ) : null}

              {hasAttributes ? (
                <section className="task-detail-section">
                  <h3>{t('work.plan.attributes')}</h3>
                  <dl className="task-detail-dl">
                    {review && roleLabel ? (
                      <div>
                        <dt><User size={14} /> {t('work.plan.role')}</dt>
                        <dd>{roleLabel}</dd>
                      </div>
                    ) : null}
                    {review && statusLabel ? (
                      <div>
                        <dt><Tag size={14} /> {t('work.plan.status')}</dt>
                        <dd>{statusLabel}</dd>
                      </div>
                    ) : null}
                    {review && priorityLabel ? (
                      <div>
                        <dt><AlertCircle size={14} /> {t('work.plan.priority')}</dt>
                        <dd>{priorityLabel}</dd>
                      </div>
                    ) : null}
                    {dependencies.length ? (
                      <div>
                        <dt><LinkIcon size={14} /> {t('work.plan.dependencies')}</dt>
                        <dd>
                          <ul className="task-detail-value-list">
                            {dependencies.map((dependency) => (
                              <li key={dependency.id}>{dependency.title}</li>
                            ))}
                          </ul>
                        </dd>
                      </div>
                    ) : null}
                    {createdAt ? (
                      <div>
                        <dt><Clock size={14} /> {t('work.plan.created')}</dt>
                        <dd><time dateTime={task.created_at}>{createdAt}</time></dd>
                      </div>
                    ) : null}
                    {updatedAt ? (
                      <div>
                        <dt><Clock size={14} /> {t('work.plan.updated')}</dt>
                        <dd><time dateTime={task.updated_at}>{updatedAt}</time></dd>
                      </div>
                    ) : null}
                  </dl>
                </section>
              ) : null}

              {acceptanceCriteria.length ? (
                <section className="task-detail-section">
                  <h3>{t('work.plan.acceptanceCriteria')}</h3>
                  <ul className="task-detail-criteria-list">
                    {acceptanceCriteria.map((item, idx) => (
                      <li key={idx}>
                        <CheckCircle2 size={14} className="task-detail-criteria-icon" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {primaryOutcomeLabel ? (
                <section className="task-detail-section">
                  <h3>{t('work.team.primaryOutcome')}</h3>
                  {meaningfulText(task.primary_outcome?.href) ? (
                    <a
                      className="task-detail-workbench-link"
                      href={task.primary_outcome.href}
                      rel="noreferrer"
                      target="_blank"
                    >
                      <ExternalLink size={13} />
                      {primaryOutcomeLabel}
                    </a>
                  ) : <p>{primaryOutcomeLabel}</p>}
                </section>
              ) : null}
            </>
          )}

          {activeTab === 'preview' && (
            <section className="task-detail-section task-detail-preview-section">
              <div className="task-detail-preview-header">
                <div>
                  <h3 className="task-detail-preview-title">
                    <FileCode size={15} />
                    {review?.artifact ? documentTitle(review.artifact, t) : t('work.plan.artifact.title')}
                  </h3>
                </div>
                <div className="task-detail-preview-actions">
                  {hasArtifact ? (
                    <div className="task-detail-preview-toggle-group">
                      <button
                        type="button"
                        className={`task-detail-preview-toggle-btn ${previewMode === 'rendered' ? 'is-active' : ''}`}
                        onClick={() => setPreviewMode('rendered')}
                        title={t('work.plan.artifact.rendered')}
                      >
                        <Eye size={13} />
                        <span>{t('work.plan.artifact.rendered')}</span>
                      </button>
                      <button
                        type="button"
                        className={`task-detail-preview-toggle-btn ${previewMode === 'raw' ? 'is-active' : ''}`}
                        onClick={() => setPreviewMode('raw')}
                        title={t('work.plan.artifact.raw')}
                      >
                        <Code size={13} />
                        <span>{t('work.plan.artifact.raw')}</span>
                      </button>
                    </div>
                  ) : null}
                  {hasArtifact ? (
                    <button
                      type="button"
                      className="task-detail-icon-btn"
                      onClick={handleCopyArtifact}
                      title={t('work.plan.artifact.copy')}
                    >
                      {copied ? <Check size={14} color="#34d399" /> : <Copy size={14} />}
                      <span>{copied ? t('work.plan.artifact.copied') : t('work.plan.artifact.copy')}</span>
                    </button>
                  ) : null}
                  {projectKey && review?.approval ? (
                    onOpenReview ? <button type="button" className="task-detail-workbench-link" onClick={() => onOpenReview(review.approval)}>
                      <FileText size={13} aria-hidden="true" />{t('work.panel.reviewDetails')}
                    </button> : <Link
                      to={routes.projectReviewItem(projectKey, review.approval)}
                      className="task-detail-workbench-link"
                    >
                      <ExternalLink size={13} />
                      {t('work.plan.artifact.openWorkbench')}
                    </Link>
                  ) : null}
                </div>
              </div>
              {review?.artifact_path ? <FileLocation key={review.artifact_path} path={review.artifact_path} t={t} /> : null}

              {hasArtifact ? (
                previewMode === 'rendered' ? (
                  <div className="task-detail-artifact-rendered-wrap" tabIndex={0}>
                    <MarkdownView content={review.content} />
                  </div>
                ) : (
                  <pre className="task-detail-artifact-preview" tabIndex={0}>
                    {review.content}
                  </pre>
                )
              ) : (
                <div className="task-detail-artifact-empty">
                  <FileText size={38} className="task-detail-empty-icon" />
                  <p>{t('work.plan.artifact.empty')}</p>
                </div>
              )}
            </section>
          )}

          {activeTab === 'approval' && (
            <section className="task-detail-section task-detail-approval-section">
              <div className="task-detail-approval-status-card">
                <div className="task-detail-approval-status-header">
                  <div>
                    <span className="product-eyebrow">{t('work.plan.approval.status')}</span>
                    <h3 className="task-detail-approval-gate-name">
                      {review?.title || `${review?.approval?.toUpperCase()} GATE`}
                    </h3>
                  </div>
                  <StatusBadge tone={reviewTone(review)}>
                    {review?.decision ? t(`work.plan.status.${review.decision}`, {}, review.decision) : (review?.status || 'pending')}
                  </StatusBadge>
                </div>

                {recommendation?.verdict ? (
                  <div className="task-detail-recommendation-box">
                    <strong>{t('work.plan.approval.recommendation')}: {recommendation.verdict}</strong>
                    {recommendation.reasons?.map((reason, idx) => (
                      <p key={idx}>{typeof reason === 'string' ? reason : reason.text}</p>
                    ))}
                  </div>
                ) : null}

                <EvidenceSummary evidence={evidence} t={t} />
              </div>

              {/* Decisions use the version-bound Review page, not this task projection. */}
              {!isActionable ? (
                <div className="task-detail-static-state-card">
                  {review?.decision === 'approved' ? (
                    <p className="text-emerald-400 font-medium">{t('work.plan.approval.approvedState')}</p>
                  ) : review?.decision === 'changes_requested' ? (
                    <p className="text-amber-400 font-medium">{t('work.plan.approval.changesState')}</p>
                  ) : review?.decision === 'rejected' ? (
                    <p className="text-rose-400 font-medium">{t('work.plan.approval.rejectedState')}</p>
                  ) : (
                    <p className="text-zinc-400">{t('work.plan.approval.pendingState')}</p>
                  )}
                </div>
              ) : null}

              {projectKey && review?.approval ? (
                <div className="task-detail-workbench-footer">
                  {onOpenReview ? <button type="button" className="task-detail-workbench-link" onClick={() => onOpenReview(review.approval)}>
                      <FileText size={13} aria-hidden="true" />{t('work.panel.reviewDetails')}
                    </button> : <Link
                    to={routes.projectReviewItem(projectKey, review.approval)}
                    className="task-detail-workbench-link"
                  >
                    <ExternalLink size={13} />
                    {t('work.plan.approval.openWorkbench')} →
                  </Link>}
                </div>
              ) : null}
            </section>
          )}
        </div>

        <footer className="task-detail-footer">
          {isActionable && activeTab !== 'approval' ? (
            <Button
              variant="primary"
              onClick={() => setActiveTab('approval')}
            >
              {t('work.plan.approval.goToApproval')}
            </Button>
          ) : null}
          <Button variant="secondary" onClick={onClose}>
            {t('work.plan.close')}
          </Button>
        </footer>
      </div>
    </div>
  );
}