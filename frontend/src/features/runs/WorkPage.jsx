/* eslint-disable react-refresh/only-export-components -- helper functions are exported for unit tests. */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Bot,
  ChevronRight,
  Clock3,
  FileCheck2,
  Send,
} from 'lucide-react';
import { Link, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import WorkflowCanvas from '../../components/WorkflowCanvas';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import TaskDetailModal, { findMatchingReview } from '../../components/TaskDetailModal';
import { useToast } from '../../components/ui/ToastProvider';
import { routes } from '../../lib/routing/routes';
import { statusLabel, stepTitle } from '../../utils/workflowHelpers';
import { reviewApi } from '../reviews/reviewApi';
import { runApi } from './runApi';
import useProjectState from './useProjectState';

const VIEWS = ['plan', 'activity', 'execution', 'messages', 'delivery'];
const MOBILE_TEAM_TABS = ['execution', 'manager', 'outcomes'];
const MOBILE_TEAM_QUERY = '(max-width: 767px)';

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(() => (
    globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches || false
  ));
  useEffect(() => {
    const mq = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!mq) return undefined;
    const handler = (event) => setReduced(event.matches);
    mq.addEventListener?.('change', handler);
    return () => mq.removeEventListener?.('change', handler);
  }, []);
  return reduced;
}

function useMobileTeamWorkspace() {
  const [mobile, setMobile] = useState(() => (
    globalThis.matchMedia?.(MOBILE_TEAM_QUERY)?.matches || false
  ));
  useEffect(() => {
    const mq = globalThis.matchMedia?.(MOBILE_TEAM_QUERY);
    if (!mq) return undefined;
    const handler = (event) => setMobile(event.matches);
    mq.addEventListener?.('change', handler);
    return () => mq.removeEventListener?.('change', handler);
  }, []);
  return mobile;
}

function tone(status) {
  if (['complete', 'completed', 'done', 'approved', 'success'].includes(status)) return 'success';
  if (['blocked', 'failed', 'error', 'stopped'].includes(status)) return 'danger';
  if ([
    'active', 'running', 'doing', 'working', 'reviewing', 'fixing',
    'review-needed', 'needs_input', 'attention_required', 'changes_requested',
  ].includes(status)) return 'active';
  return 'neutral';
}

function translated(t, key, fallback, params = {}) {
  const value = t(key, params);
  return value === key ? fallback : value;
}

function statusText(status, t) {
  return translated(t, `work.team.status.${status}`, status || t('work.team.status.up_next'));
}

function executionStatusReason(actor, t) {
  if (actor.state === 'waiting' && actor.dependency_task_title) {
    return translated(
      t,
      'work.team.reasons.waiting_for_dependency',
      `Waiting for “${actor.dependency_task_title}” to complete.`,
      { dependency: actor.dependency_task_title },
    );
  }
  const reason = actor.status_reason || actor.dependency_reason;
  return reason ? translated(t, `work.team.reasons.${reason}`, reason) : '';
}

function taskProgress(status) {
  if (['complete', 'completed', 'done', 'approved', 'success'].includes(status)) return 100;
  if (['reviewing'].includes(status)) return 78;
  if (['fixing'].includes(status)) return 62;
  if (['active', 'running', 'doing', 'working'].includes(status)) return 48;
  if (['blocked', 'failed', 'error'].includes(status)) return 24;
  if (['waiting', 'pending'].includes(status)) return 12;
  return 4;
}

function matchingOutcome(actor, outcomes) {
  return outcomes.find((task) => task.id === actor.task_id)
    || outcomes.find((task) => task.owner === actor.role && !['completed', 'stopped'].includes(task.status))
    || null;
}

function TeamAvatar({ label, manager = false }) {
  const initial = String(label || '').trim().slice(0, 1).toUpperCase() || 'G';
  return (
    <span aria-hidden="true" className={`team-avatar${manager ? ' team-avatar--manager' : ''}`}>
      {manager ? <Bot size={18} strokeWidth={1.9} /> : initial}
    </span>
  );
}

function TaskProgress({ status }) {
  return (
    <span aria-hidden="true" className="team-task-progress">
      <span style={{ width: `${taskProgress(status)}%` }} />
    </span>
  );
}

function ExecutionStatusIndicator({ actor, reducedMotion, t }) {
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const tooltipId = useId();
  const status = statusText(actor.state, t);
  const reason = executionStatusReason(actor, t);
  const label = reason
    ? translated(
      t,
      'work.team.executionStatusLabel',
      `${status}. ${reason}`,
      { status, reason },
    )
    : status;
  const ambient = ['working', 'reviewing', 'fixing'].includes(actor.state) && !reducedMotion;

  return (
    <span
      aria-describedby={tooltipOpen ? tooltipId : undefined}
      aria-label={label}
      className="team-status-led-trigger"
      data-motion={ambient ? 'ambient' : 'static'}
      data-state={actor.state || 'up_next'}
      onBlur={() => setTooltipOpen(false)}
      onFocus={() => setTooltipOpen(true)}
      onMouseEnter={() => setTooltipOpen(true)}
      onMouseLeave={() => setTooltipOpen(false)}
      role="img"
      tabIndex={0}
    >
      <span aria-hidden="true" className="team-status-led" />
      <span
        aria-hidden={!tooltipOpen}
        className="team-status-led-tooltip"
        data-open={tooltipOpen ? 'true' : 'false'}
        id={tooltipId}
        role="tooltip"
      >
        <strong>{status}</strong>
        {reason ? <span>{reason}</span> : null}
      </span>
    </span>
  );
}

function shortRunId(runId) {
  return String(runId || '').trim().slice(0, 8);
}

function runTitle(run, t) {
  const title = [run?.title, run?.idea, run?.goal]
    .map((value) => String(value || '').trim())
    .find(Boolean);
  return title || t('work.history.untitled', { runId: shortRunId(run?.run_id) });
}

function runOptionLabel(run, t) {
  const statusKey = run.status === 'active'
    ? 'work.history.activeOption'
    : run.status === 'complete'
      ? 'work.history.completedOption'
      : 'work.history.archivedOption';
  return t(statusKey, { title: runTitle(run, t) });
}

function currentWorkflowStep(steps = []) {
  return steps.find((step) => ['active', 'review-needed', 'blocked'].includes(step.status))
    || steps.find((step) => !['done', 'complete', 'completed'].includes(step.status))
    || null;
}

function formattedRunUpdate(value, language) {
  const timestamp = Date.parse(value || '');
  if (!Number.isFinite(timestamp)) return '';
  return new Intl.DateTimeFormat(language, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(timestamp));
}

function cleanMarkdownText(text) {
  if (!text || typeof text !== 'string') return '';
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/[*_~]+/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^[#\s*\->`]+/gm, '')
    .trim();
}

function extractContentDigest(content) {
  if (!content || typeof content !== 'string') return '';
  const lines = content.split('\n')
    .map((l) => cleanMarkdownText(l))
    .filter(Boolean);
  const firstMeaningful = lines.find((l) => l.length > 5 && !l.startsWith('===') && !l.startsWith('---'));
  if (!firstMeaningful) return '';
  return firstMeaningful.length > 160 ? `${firstMeaningful.slice(0, 157)}...` : firstMeaningful;
}

function formatArtifactFileName(path) {
  if (!path || typeof path !== 'string') return '';
  const parts = path.split('/').filter(Boolean);
  return parts.slice(-2).join('/') || path;
}

function reviewGateTitle(approval, t) {
  const gateMap = {
    takeover: 'work.review.titles.takeover',
    prd: 'work.review.titles.prd',
    rfc: 'work.review.titles.rfc',
    security: 'work.review.titles.security',
    staging: 'work.review.titles.staging',
    release: 'work.review.titles.release',
  };
  const key = gateMap[approval];
  if (key) {
    const label = t(key);
    if (label !== key) return label;
  }
  const stageKey = `work.stage.${approval}`;
  const stageName = t(stageKey);
  return stageName !== stageKey ? t('work.review.gateTitle', { gate: stageName }) : t('work.review.generic.title');
}

function reviewGateName(gate, t) {
  if (!gate) return '';
  const key = `work.stage.${gate}`;
  const translated = t(key);
  return translated !== key ? translated : String(gate).toUpperCase();
}

function recommendationReasonText(reason, t) {
  if (!reason) return '';
  if (typeof reason === 'string') return reason;
  if (reason.kind === 'clean') return t('review.recommendation.reason.clean');
  return reason.text || reason.kind || '';
}

function reviewPrompt(review, t) {
  if (review?.recovery?.available) {
    return {
      action: t('work.review.recovery.action'),
      artifactFile: formatArtifactFileName(review.artifact_path),
      description: review.recovery.note || t('work.review.recovery.description'),
      eyebrow: t('work.review.eyebrow'),
      gate: 'takeover',
      recommendation: review.recommendation,
      title: t('work.review.recovery.title'),
    };
  }

  const gateName = reviewGateName(review?.approval, t);
  const digest = extractContentDigest(review?.content);
  const topReason = recommendationReasonText(review?.recommendation?.reasons?.[0], t);

  if (review?.approval === 'takeover') {
    return {
      action: t('work.review.takeover.action'),
      artifactFile: formatArtifactFileName(review.artifact_path),
      description: digest || t('work.review.takeover.description'),
      digest,
      eyebrow: t('work.review.eyebrow'),
      gate: 'takeover',
      recommendation: review.recommendation,
      title: t('work.review.takeover.title'),
    };
  }

  const title = reviewGateTitle(review?.approval, t);

  const description = digest
    || (topReason ? `${topReason}` : t('work.review.gateDescription', { gate: gateName }));

  const action = t('work.review.gateAction', { gate: gateName });

  return {
    action,
    artifactFile: formatArtifactFileName(review?.artifact_path),
    description,
    digest,
    eyebrow: t('work.review.gateEyebrow', { gate: gateName }),
    gate: review?.approval,
    recommendation: review?.recommendation,
    title,
  };
}

function WorkSkeleton({ t }) {
  return (
    <div className="product-workspace-skeleton" aria-label={t('product.states.loading')} role="status">
      <span /><span /><span />
    </div>
  );
}

function ManagerMissionEntry({ busy, data, embedded = true, error, onStart, t }) {
  const [goal, setGoal] = useState('');
  const submit = (event) => {
    event.preventDefault();
    if (goal.trim()) onStart(goal.trim());
  };
  return (
    <form className={embedded ? 'team-manager-entry' : 'work-start-panel'} onSubmit={submit}>
      <div>
        <p className="product-eyebrow">{t('work.start.eyebrow')}</p>
        {embedded ? <h3>{t('work.start.title')}</h3> : <h2>{t('work.start.title')}</h2>}
        <p>{t('work.start.description')}</p>
      </div>
      <label>
        <span>{t('work.start.goal')}</span>
        <textarea rows="4" required value={goal} onChange={(event) => setGoal(event.target.value)} />
      </label>
      {error ? <ProductError error={error} t={t} /> : null}
      <Button busy={busy} disabled={!goal.trim()} type="submit">{t('work.start.action')}</Button>
      <p className="product-boundary-note">
        {t('work.start.boundary', { profile: data?.start_context?.model_profile || 'balanced' })}
      </p>
    </form>
  );
}

export function WorkFocusBanner({
  approvalReviews = [],
  blockedCount = 0,
  busyDecision = '',
  onDecide,
  onRetry,
  onTerminate,
  projectKey,
  retrying = false,
  runTitle = '',
  t,
  terminating = false,
}) {
  const actionableReview = approvalReviews?.find((review) => review.is_actionable);
  const recoverableReview = approvalReviews?.find((review) => review.recovery?.available);
  const priorityReview = actionableReview || recoverableReview;
  const prompt = priorityReview ? reviewPrompt(priorityReview, t) : null;

  if (!prompt && blockedCount <= 0) {
    return null;
  }

  const rec = prompt?.recommendation;
  const verdictKey = rec?.verdict === 'approve'
    ? 'work.review.recommendationApprove'
    : rec?.verdict === 'changes_requested'
      ? 'work.review.recommendationChanges'
      : rec?.verdict === 'caution'
        ? 'work.review.recommendationCaution'
        : rec?.verdict === 'reject'
          ? 'work.review.recommendationReject'
          : '';

  const recReason = recommendationReasonText(rec?.reasons?.[0], t);

  return (
    <section className="work-focus-banner" data-tone={prompt ? 'warning' : 'danger'}>
      {prompt ? (
        <div className="work-focus-banner__content">
          <p className="product-eyebrow">{prompt.eyebrow}</p>
          <h2>{prompt.title}</h2>
          {prompt.digest ? (
            <div className="work-focus-banner__digest">{prompt.digest}</div>
          ) : (
            <p>{prompt.description}</p>
          )}
          <div className="work-focus-banner__meta">
            {rec?.verdict ? (
              <span className="work-focus-banner__recommendation" data-verdict={rec.verdict}>
                {verdictKey ? t(verdictKey) : rec.verdict}
                {recReason ? ` · ${recReason}` : ''}
              </span>
            ) : null}
            {prompt.artifactFile ? (
              <code className="work-focus-banner__path">{t('work.review.artifactFile', { file: prompt.artifactFile })}</code>
            ) : null}
          </div>
          <div className="work-focus-banner__actions">
            {onDecide && priorityReview?.is_actionable ? (
              <Button
                variant="primary"
                busy={busyDecision === 'approved'}
                onClick={() => onDecide(priorityReview.approval, 'approved', '')}
              >
                {t('work.review.quickApprove')}
              </Button>
            ) : null}
            <Link
              className={`product-button ${onDecide && priorityReview?.is_actionable ? 'product-button--secondary' : 'product-button--primary'}`}
              to={routes.projectReviewItem(projectKey, priorityReview.approval)}
            >
              {onDecide && priorityReview?.is_actionable ? t('work.review.viewDetails') : prompt.action}
            </Link>
            {onTerminate ? (
              <details className="work-run-controls">
                <summary>{t(prompt ? 'work.actions.abandonRun' : 'work.actions.runControls')}</summary>
                <div className="work-run-controls__body">
                  <p>{t('work.actions.terminateScope', { runTitle })}</p>
                  <Button busy={terminating} variant="danger" onClick={onTerminate}>{t('work.actions.terminate')}</Button>
                </div>
              </details>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="work-focus-banner__content">
          <p className="product-eyebrow">{t('work.plan.blocked')}</p>
          <h2>{t('work.actions.retryBlocked')}</h2>
          <p>{t('work.actions.description')}</p>
          <div className="work-focus-banner__actions">
            {onRetry ? (
              <Button busy={retrying} onClick={onRetry}>{t('work.actions.retryBlocked')}</Button>
            ) : null}
            {onTerminate ? (
              <details className="work-run-controls">
                <summary>{t('work.actions.runControls')}</summary>
                <div className="work-run-controls__body">
                  <p>{t('work.actions.terminateScope', { runTitle })}</p>
                  <Button busy={terminating} variant="danger" onClick={onTerminate}>{t('work.actions.terminate')}</Button>
                </div>
              </details>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}

export function ActivityView({ data, language, projectKey, t }) {
  const actionItems = data?.action_items || [];
  return (
    <div className="work-activity-view">
      <header className="work-section-header">
        <div>
          <h2>{t('work.actions.title')}</h2>
          <p>{t('work.actions.description')}</p>
        </div>
        {actionItems.length ? (
          <span className="work-activity-count-badge">
            {actionItems.length} {t('work.actions.countLabel', { count: actionItems.length })}
          </span>
        ) : null}
      </header>

      {actionItems.length ? (
        <ol className="work-activity-timeline" aria-label={t('work.actions.title')}>
          {actionItems.map((action, index) => {
            const parsed = Date.parse(action.timestamp);
            const formattedTime = Number.isFinite(parsed)
              ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(parsed))
              : (action.timestamp || '—');
            const nodeTransition = action.node_transition || action.after_node || '';
            const actionType = action.action || action.type || 'action';

            return (
              <li key={action.id || `${actionType}-${action.timestamp}-${index}`} className="work-activity-item">
                <div className="work-activity-item__dot" />
                <div className="work-activity-item__content">
                  <div className="work-activity-item__header">
                    <strong className="work-activity-item__title">{action.title || actionType}</strong>
                    {nodeTransition ? (
                      <code className="work-activity-item__transition">{nodeTransition}</code>
                    ) : null}
                    <time className="work-activity-item__time" dateTime={action.timestamp}>{formattedTime}</time>
                  </div>
                  {action.note || action.description || action.message ? (
                    <p className="work-activity-item__note">{action.note || action.description || action.message}</p>
                  ) : null}
                  {action.approval ? (
                    <div className="work-activity-item__actions">
                      <Link
                        className="work-activity-item__link"
                        to={routes.projectReviewItem(projectKey, action.approval)}
                      >
                        {t('work.actions.viewReview', { approval: action.approval })} →
                      </Link>
                    </div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="product-empty-copy">{t('work.actions.empty')}</p>
      )}
    </div>
  );
}

function HistoricalRunView({ detail, projectKey, summary, t }) {
  const state = detail?.state || {};
  const title = runTitle({ ...state, ...summary }, t);
  return (
    <section className="work-historical-view">
      <p className="product-eyebrow">{t('work.history.title')}</p>
      <h2>{title}</h2>
      <p>{t('work.history.readOnly')}</p>
      <dl className="settings-data-list">
        <div><dt>{t('work.history.goal')}</dt><dd>{summary.idea || state.idea || '—'}</dd></div>
        <div><dt>{t('work.history.source')}</dt><dd>{summary.source_type || state.source_type || '—'}</dd></div>
        <div><dt>{t('work.history.status')}</dt><dd>{summary.status || '—'}</dd></div>
        <div><dt>{t('work.history.currentNode')}</dt><dd>{state.current_node || summary.current_node || '—'}</dd></div>
        <div><dt>{t('work.history.nextNode')}</dt><dd>{state.next_node || summary.next_node || '—'}</dd></div>
        <div><dt>{t('work.history.updated')}</dt><dd><time dateTime={summary.updated_at}>{summary.updated_at || '—'}</time></dd></div>
      </dl>
      <Link to={routes.projectWork(projectKey)}>{t('work.history.current')}</Link>
    </section>
  );
}

export function findTaskForApprovalOrId(board, { approval, task: taskId, reviews }) {
  if (!board?.columns) return null;
  const allTasks = Object.values(board.columns).flat();
  if (taskId) {
    const byId = allTasks.find((item) => String(item?.id || '').toLowerCase() === String(taskId).toLowerCase());
    if (byId) return byId;
  }
  if (approval) {
    const targetApproval = String(approval).toLowerCase();
    if (Array.isArray(reviews) && reviews.length) {
      const match = allTasks.find((task) => {
        const rev = findMatchingReview(task, reviews);
        return rev && String(rev.approval || '').toLowerCase() === targetApproval;
      });
      if (match) return match;
    }
    const stageMap = {
      takeover: 'stage:takeover',
      prd: 'stage:product_requirement',
      rfc: 'stage:architect',
      security: 'stage:architect',
      staging: 'stage:qa',
      release: 'stage:release',
    };
    const targetStage = stageMap[targetApproval];
    if (targetStage) {
      const byStage = allTasks.find((task) =>
        (task?.external_refs || []).some((ref) => String(ref).toLowerCase() === targetStage)
      );
      if (byStage) return byStage;
    }
    const roleMap = {
      takeover: 'takeover',
      prd: 'product_requirement',
      rfc: 'architect',
      security: 'architect',
      staging: 'qa',
      release: 'release',
    };
    const targetRole = roleMap[targetApproval];
    if (targetRole) {
      const byRole = allTasks.find((task) => task?.role === targetRole);
      if (byRole) return byRole;
    }
  }
  return null;
}

export function PlanView({
  approvalReviews = [],
  board,
  busyDecision = '',
  onDecide,
  onRetry,
  onTerminate,
  projectKey,
  retrying = false,
  reviews = [],
  runTitle = '',
  steps = [],
  t,
  terminating = false,
}) {
  const [search, setSearch] = useSearchParams();
  const [manuallySelectedTask, setManuallySelectedTask] = useState(null);
  const approvalParam = search.get('approval');
  const taskParam = search.get('task');

  const resolvedReviews = approvalReviews.length ? approvalReviews : reviews;

  const deepLinkedTask = useMemo(() => {
    if (approvalParam || taskParam) {
      return findTaskForApprovalOrId(board, { approval: approvalParam, task: taskParam, reviews: resolvedReviews });
    }
    return null;
  }, [approvalParam, taskParam, board, resolvedReviews]);

  const selectedTask = manuallySelectedTask || deepLinkedTask;

  const handleClose = () => {
    setManuallySelectedTask(null);
    if (search.get('approval') || search.get('task')) {
      const next = new URLSearchParams(search);
      next.delete('approval');
      next.delete('task');
      setSearch(next, { replace: true });
    }
  };

  const columns = ['backlog', 'doing', 'blocked', 'done'];
  return (
    <div className="work-plan-container">
      <WorkFocusBanner
        approvalReviews={resolvedReviews}
        blockedCount={board?.counts?.blocked || 0}
        busyDecision={busyDecision}
        onDecide={onDecide}
        onRetry={onRetry}
        onTerminate={onTerminate}
        projectKey={projectKey}
        retrying={retrying}
        runTitle={runTitle}
        steps={steps}
        t={t}
        terminating={terminating}
      />
      <div className="work-board">
        {columns.map((column) => (
          <section key={column}>
            <h2>{t(`work.plan.${column}`)} <span>{board?.counts?.[column] || 0}</span></h2>
            <ul className="work-board-column-list">
              {(board?.columns?.[column] || []).map((task, index) => (
                <li
                  key={task.id || `${column}-${index}`}
                  className="work-board-card"
                  role="button"
                  tabIndex={0}
                  aria-haspopup="dialog"
                  onClick={() => setManuallySelectedTask(task)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setManuallySelectedTask(task);
                    }
                  }}
                >
                  <div className="work-board-card-header">
                    {task.id ? <span className="work-board-card-id">{task.id}</span> : null}
                    {task.role ? (
                      <span className="work-board-card-role">
                        {t(`work.plan.role.${task.role}`, {}, task.role)}
                      </span>
                    ) : null}
                    {task.priority ? (
                      <span className={`work-board-card-priority priority-${task.priority}`}>
                        {t(`work.plan.priority.${task.priority}`, {}, task.priority)}
                      </span>
                    ) : null}
                  </div>
                  <strong className="work-board-card-title">{task.title || task.name || task.id}</strong>
                  {task.description ? <p className="work-board-card-desc">{task.description}</p> : null}
                  <div className="work-board-card-footer">
                    <span className="work-board-card-hint">{t('work.plan.clickToView')}</span>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {selectedTask ? (
          <TaskDetailModal
            task={selectedTask}
            reviews={resolvedReviews}
            projectKey={projectKey}
            onDecide={onDecide}
            onClose={handleClose}
            t={t}
          />
        ) : null}
      </div>
    </div>
  );
}

function ExecutionView({ defaultAdapter = 'codex', jobs, onDispatch, t }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [dispatched, setDispatched] = useState(null);
  const [values, setValues] = useState({ name: '', role: 'implementation', adapter: defaultAdapter, task: '' });
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      const payload = await onDispatch(values);
      setDispatched({ name: values.name, role: values.role });
      setOpen(false);
      setValues({ name: '', role: 'implementation', adapter: defaultAdapter, task: '' });
      return payload;
    } catch (caught) {
      setError(caught);
      return null;
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!dispatched) return undefined;
    const timer = globalThis.setTimeout(() => setDispatched(null), 3000);
    return () => globalThis.clearTimeout(timer);
  }, [dispatched]);
  return (
    <div className="work-section-stack">
      <header className="work-section-header">
        <div><h2>{t('work.execution.title')}</h2><p>{t('work.execution.description')}</p></div>
        <Button onClick={() => setOpen((value) => !value)}>{t('work.execution.dispatch')}</Button>
      </header>
      {dispatched ? <p className="work-dispatch-result" role="status">{t('work.execution.dispatched', { name: dispatched.name, role: dispatched.role })}</p> : null}
      {open ? (
        <form className="product-inline-form" onSubmit={submit}>
          {['name', 'role', 'adapter', 'task'].map((field) => (
            <label key={field}><span>{t(`work.execution.${field}`)}</span>
              {field === 'task'
                ? <textarea required rows="3" value={values[field]} onChange={(event) => setValues({ ...values, [field]: event.target.value })} />
                : <input required value={values[field]} onChange={(event) => setValues({ ...values, [field]: event.target.value })} />}
            </label>
          ))}
          {error ? <ProductError error={error} t={t} /> : null}
          <Button busy={busy} type="submit">{t('work.execution.confirmDispatch')}</Button>
        </form>
      ) : null}
      {jobs.length ? <ul className="work-item-list">{jobs.map((job, index) => (
        <li key={job.job_id || job.id || index}>
          <div><strong>{job.role || job.name || t('work.execution.worker')}</strong><code>{job.job_id || job.id}</code></div>
          <StatusBadge tone={tone(job.status)}>{job.status}</StatusBadge>
        </li>
      ))}</ul> : <p className="product-empty-copy">{t('work.execution.empty')}</p>}
    </div>
  );
}

function MessagesView({ data, projectKey, t }) {
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState('loading');
  const runId = data?.state?.run_id;
  useEffect(() => {
    if (!runId) { queueMicrotask(() => { setMessages([]); setStatus('ready'); }); return undefined; }
    const controller = new AbortController();
    const load = () => runApi.getMessages(projectKey, runId, '', { signal: controller.signal })
      .then((payload) => { setMessages(payload.messages || []); setStatus('ready'); })
      .catch((error) => { if (error.name !== 'AbortError') setStatus('error'); });
    void load();
    const timer = globalThis.setInterval(() => {
      if (!globalThis.document?.hidden) void load();
    }, 5_000);
    return () => { controller.abort(); globalThis.clearInterval(timer); };
  }, [projectKey, runId]);
  if (status === 'loading') return <WorkSkeleton t={t} />;
  if (!messages.length) return <p className="product-empty-copy">{t('work.messages.empty')}</p>;
  return <ol className="work-message-list">{messages.map((message, index) => (
    <li key={message.id || index}><strong>{message.role || message.actor || t('work.messages.system')}</strong><p>{message.content || message.text}</p></li>
  ))}</ol>;
}

function asEntries(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.entries(value).map(([key, entry]) => ({ key, ...(typeof entry === 'object' && entry ? entry : { value: entry }) }));
  return [];
}

function DeliverySection({ children, raw, title, t }) {
  return (
    <section>
      <h3>{title}</h3>
      {children}
      <details><summary>{t('work.delivery.raw')}</summary><pre>{raw}</pre></details>
    </section>
  );
}

function DeliveryView({ data, projectKey, t }) {
  const [details, setDetails] = useState(null);
  const [error, setError] = useState(null);
  const [smokeBusy, setSmokeBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    runApi.getDevLoop(projectKey, { signal: controller.signal }).then(setDetails)
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught); });
    return () => controller.abort();
  }, [projectKey]);
  const smoke = async () => {
    setSmokeBusy(true); setError(null);
    try { await runApi.startSmoke({ suite: 'self', cycles: 1, dry_run: true, worker_mode: 'fake' }); }
    catch (caught) { setError(caught); }
    finally { setSmokeBusy(false); }
  };
  const pullRequests = asEntries(details?.pull_requests || details?.prs || []);
  const checks = asEntries(details?.ci || details?.checks || []);
  const evidence = asEntries(data?.resources || details?.evidence || []);
  const smokeRuns = asEntries(data?.smoke_run_items || []);
  return (
    <div className="work-section-stack">
      <header className="work-section-header"><div><h2>{t('work.delivery.title')}</h2><p>{t('work.delivery.description')}</p></div>
        <Button busy={smokeBusy} onClick={smoke}>{t('work.delivery.smoke')}</Button></header>
      {error ? <ProductError error={error} t={t} /> : null}
      <div className="work-delivery-grid">
        <DeliverySection title={t('work.delivery.pullRequests')} raw={JSON.stringify(pullRequests, null, 2)} t={t}>
          {pullRequests.length ? <ul className="work-delivery-list">{pullRequests.map((pr, index) => (
            <li key={pr.number || pr.id || pr.title || index}>
              <strong>{pr.title || `#${pr.number || index}`}</strong>
              {pr.branch ? <code>{pr.branch}</code> : null}
              <StatusBadge tone={tone(pr.state)}>{pr.state || t('work.delivery.state')}</StatusBadge>
              {pr.html_url ? <a href={pr.html_url} target="_blank" rel="noreferrer">{t('work.delivery.open')}</a> : null}
            </li>
          ))}</ul> : <p className="product-empty-copy">{t('work.delivery.empty')}</p>}
        </DeliverySection>
        <DeliverySection title={t('work.delivery.ci')} raw={JSON.stringify(checks, null, 2)} t={t}>
          {checks.length ? <ul className="work-delivery-list">{checks.map((check, index) => (
            <li key={check.name || check.id || index}>
              <strong>{check.name || check.title || check.check_name || `#${index}`}</strong>
              <StatusBadge tone={tone(check.status || check.conclusion)}>{check.status || check.conclusion || t('work.delivery.state')}</StatusBadge>
              {check.html_url || check.details_url ? <a href={check.html_url || check.details_url} target="_blank" rel="noreferrer">{t('work.delivery.open')}</a> : null}
            </li>
          ))}</ul> : <p className="product-empty-copy">{t('work.delivery.empty')}</p>}
        </DeliverySection>
        <DeliverySection title={t('work.delivery.evidence')} raw={JSON.stringify(evidence, null, 2)} t={t}>
          {evidence.length ? <ul className="work-delivery-list">{evidence.map((item, index) => (
            <li key={item.key || item.id || item.path || index}>
              <strong>{item.key || item.name || item.id || `#${index}`}</strong>
              {item.path || item.value ? <code>{item.path || item.value}</code> : null}
            </li>
          ))}</ul> : <p className="product-empty-copy">{t('work.delivery.empty')}</p>}
        </DeliverySection>
        <DeliverySection title={t('work.delivery.smokeRuns')} raw={JSON.stringify(smokeRuns, null, 2)} t={t}>
          {smokeRuns.length ? <ul className="work-delivery-list">{smokeRuns.map((run, index) => (
            <li key={run.id || run.run_id || index}>
              <strong>{run.suite || run.name || t('work.delivery.smokeRuns')}</strong>
              <StatusBadge tone={tone(run.status)}>{run.status || t('work.delivery.state')}</StatusBadge>
              {run.report_path ? <code>{run.report_path}</code> : null}
            </li>
          ))}</ul> : <p className="product-empty-copy">{t('work.delivery.empty')}</p>}
        </DeliverySection>
      </div>
    </div>
  );
}

function timelineCopy(item, t) {
  if (item.actor === 'user' && item.event === 'mission_requested') return item.content;
  const details = item.details || {};
  return translated(
    t,
    `work.manager.events.${item.event}`,
    item.content || translated(t, 'work.manager.events.progress', 'The team state changed.'),
    {
      artifact: details.artifact || t('work.manager.deliverable'),
      decision: details.decision ? statusText(details.decision, t) : '',
      gate: details.gate ? reviewGateName(details.gate, t) : '',
      taskCount: details.task_count || 0,
    },
  );
}

function TimelineItem({ item, language, t }) {
  const timestamp = Date.parse(item.timestamp);
  const formatted = Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp))
    : '';
  const actorLabel = item.actor === 'user' ? t('work.manager.you') : t('work.manager.name');
  const copy = timelineCopy(item, t);
  return (
    <li className="team-manager-event" data-actor={item.actor} data-kind={item.kind}>
      <TeamAvatar label={actorLabel} manager={item.actor !== 'user'} />
      <div className="team-manager-event__bubble">
        <div className="team-manager-event__meta">
          <strong>{actorLabel}</strong>
          {formatted ? <time dateTime={item.timestamp}>{formatted}</time> : null}
        </div>
        <p>{copy}</p>
        {item.content && item.event !== 'mission_requested' && copy !== item.content ? (
          <blockquote>{item.content}</blockquote>
        ) : null}
      </div>
    </li>
  );
}

function ManagerAttention({
  attention,
  busy,
  onDecide,
  onReview,
  onRetry,
  projectKey,
  t,
}) {
  const [requestingChanges, setRequestingChanges] = useState(false);
  const [note, setNote] = useState('');
  if (!attention) return null;
  const actions = new Set(attention.actions || []);
  const gate = attention.target_id || '';
  const decide = (decision, decisionNote = '') => {
    Promise.resolve()
      .then(() => onDecide?.(gate, decision, decisionNote))
      .catch(() => {});
  };
  const submitChanges = (event) => {
    event.preventDefault();
    if (!note.trim()) return;
    decide('changes_requested', note.trim());
  };
  return (
    <section className="team-attention" data-kind={attention.kind} aria-label={t('work.manager.attention')}>
      <p className="team-attention__label">
        <AlertCircle aria-hidden="true" size={15} strokeWidth={2} />
        <span>{t('work.manager.attention')}</span>
      </p>
      <h3 id="team-attention-title">
        {translated(t, `work.manager.attentionKinds.${attention.kind}`, attention.reason)}
      </h3>
      <p>{translated(t, `work.manager.attentionReasons.${attention.reason}`, attention.reason)}</p>
      <div className="team-attention__actions">
        {actions.has('approve') ? (
          <Button busy={busy === 'decision'} onClick={() => decide('approved')}>
            {t('work.review.quickApprove')}
          </Button>
        ) : null}
        {actions.has('request_changes') ? (
          <Button variant="secondary" onClick={() => setRequestingChanges((value) => !value)}>
            {t('work.manager.requestChanges')}
          </Button>
        ) : null}
        {actions.has('review') && onReview ? (
          <Button variant="secondary" onClick={(event) => onReview(event.currentTarget)}>
            {t('work.review.viewDetails')}
          </Button>
        ) : actions.has('review') ? (
          <Link className="product-button product-button--secondary" to={routes.projectReviewItem(projectKey, gate)}>
            {t('work.review.viewDetails')}
          </Link>
        ) : null}
        {actions.has('retry') ? (
          <Button busy={busy === 'retry'} onClick={onRetry}>{t('work.actions.retryBlocked')}</Button>
        ) : null}
      </div>
      {requestingChanges ? (
        <form className="team-attention__form" onSubmit={submitChanges}>
          <label>
            <span>{t('work.manager.changeReason')}</span>
            <textarea required rows="3" value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <div>
            <Button busy={busy === 'decision'} disabled={!note.trim()} type="submit">{t('work.manager.sendChanges')}</Button>
            <Button variant="secondary" onClick={() => setRequestingChanges(false)}>{t('work.manager.keepWorking')}</Button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

function ManagerMissionControls({ busy, canCancel, onTerminate, t }) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  if (!canCancel) return null;
  return (
    <div className="team-manager-controls">
      {!confirmCancel ? (
        <button type="button" onClick={() => setConfirmCancel(true)}>{t('work.manager.cancelMission')}</button>
      ) : (
        <div className="team-attention__confirm" role="group" aria-label={t('work.manager.cancelMission')}>
          <p>{t('work.manager.cancelConfirm')}</p>
          <div>
            <Button busy={busy === 'terminate'} variant="danger" onClick={onTerminate}>{t('work.actions.terminate')}</Button>
            <Button variant="secondary" onClick={() => setConfirmCancel(false)}>{t('work.manager.keepWorking')}</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function ManagerPane({
  attention,
  busy,
  capabilities,
  data,
  error,
  language,
  mission,
  onDecide,
  onInspect,
  onReview,
  onRetry,
  onStart,
  onTerminate,
  projectKey,
  sectionProps,
  timeline,
  t,
}) {
  const managerTone = attention?.kind === 'blocker'
    ? 'danger'
    : attention
      ? 'active'
      : mission
        ? tone(mission.status)
        : 'neutral';
  const managerStatus = attention
    ? t('work.manager.needsYou')
    : mission
      ? statusText(mission.status, t)
      : t('work.manager.ready');
  const timelineHasMissionRequest = (timeline || []).some((item) => item.event === 'mission_requested');
  return (
    <section className="team-pane team-pane--manager" aria-label={t('work.manager.name')} {...sectionProps}>
      <header className="team-pane__header">
        <div className="team-manager-heading">
          <TeamAvatar label={t('work.manager.name')} manager />
          <div>
            <div className="team-manager-heading__title">
              <h2 id="team-manager-title">{t('work.manager.name')}</h2>
              <StatusBadge tone={managerTone}>{managerStatus}</StatusBadge>
            </div>
            <span className="team-pane__eyebrow">{t('work.manager.subtitle')}</span>
          </div>
        </div>
        {mission ? (
          <button className="team-pane__header-action" type="button" onClick={() => onInspect('activity')}>
            <Clock3 aria-hidden="true" size={15} />
            <span>{t('work.manager.history')}</span>
          </button>
        ) : null}
      </header>
      <div className="team-manager-conversation">
        {mission ? (
          <>
            {!timelineHasMissionRequest ? (
              <p className="team-manager-objective">
                <span>{t('work.manager.currentMission')}</span>
                <strong>{mission.objective}</strong>
              </p>
            ) : null}
            {error ? <ProductError error={error} t={t} /> : null}
            <ol className="team-manager-timeline" aria-label={t('work.manager.history')}>
              {(timeline || []).map((item) => <TimelineItem item={item} key={item.id} language={language} t={t} />)}
            </ol>
            <ManagerAttention
              attention={attention}
              busy={busy}
              onDecide={onDecide}
              onReview={onReview}
              onRetry={onRetry}
              projectKey={projectKey}
              t={t}
            />
          </>
        ) : (
          <ManagerMissionEntry
            busy={busy === 'start'}
            data={data}
            error={error}
            onStart={onStart}
            t={t}
          />
        )}
      </div>
      {mission ? (
        <footer className="team-manager-dock">
          <button className="team-manager-composer" type="button" onClick={() => onInspect('messages')}>
            <span>{t('work.manager.composer')}</span>
            <Send aria-hidden="true" size={17} strokeWidth={1.9} />
          </button>
          <ManagerMissionControls
            busy={busy}
            canCancel={(capabilities || []).some((item) => item.action === 'cancel_mission')}
            onTerminate={onTerminate}
            t={t}
          />
        </footer>
      ) : null}
    </section>
  );
}

function ExecutionPane({ actors, hasMission, onInspect, outcomes, reducedMotion, sectionProps, t }) {
  return (
    <section className="team-pane team-pane--execution" aria-label={t('work.team.execution')} {...sectionProps}>
      <header className="team-pane__header">
        <div>
          <h2 id="team-execution-title">{t('work.team.executionBoard')}</h2>
          <span className="team-pane__eyebrow">{t('work.team.liveExecution')}</span>
        </div>
        <span className="team-pane__count">{actors.length}</span>
      </header>
      {actors.length ? (
        <ol className="team-actor-list">
          {actors.map((actor) => {
            const task = matchingOutcome(actor, outcomes);
            const role = translated(t, `work.plan.role.${actor.role}`, actor.role);
            const reason = executionStatusReason(actor, t);
            const progressStatus = task?.status || actor.state;
            return (
              <li key={actor.id} data-state={actor.state}>
                <div className="team-actor-list__summary">
                  <TeamAvatar label={role} />
                  <div className="team-actor-list__copy">
                    <div className="team-actor-list__identity">
                      <strong>{role}</strong>
                      <span className="team-actor-list__state">
                        <ExecutionStatusIndicator actor={actor} reducedMotion={reducedMotion} t={t} />
                        <span>{statusText(actor.state, t)}</span>
                      </span>
                    </div>
                    <p>{actor.responsibility || t('work.team.noResponsibility')}</p>
                  </div>
                </div>
                <div className="team-actor-card__task">
                  <div>
                    <span>{t('work.team.currentTask')}</span>
                    <strong>{task?.objective || actor.responsibility || t('work.team.noResponsibility')}</strong>
                  </div>
                  <TaskProgress status={progressStatus} />
                </div>
                <div className="team-actor-card__signals">
                  {reason ? <p>{reason}</p> : null}
                  {task?.acceptance_criteria?.length ? (
                    <span>{t('work.team.acceptanceCount', { count: task.acceptance_criteria.length })}</span>
                  ) : null}
                  {task?.primary_outcome?.label ? (
                    <span>{t('work.team.primaryOutcome')}: {task.primary_outcome.label}</span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : <p className="team-pane__empty">{t('work.team.executionEmpty')}</p>}
      {hasMission ? (
        <div className="team-pane__inspect-actions">
          <button type="button" onClick={() => onInspect('execution')}>{t('work.team.inspectWorkers')}</button>
        </div>
      ) : null}
    </section>
  );
}

function OutcomesPane({
  activeReviewTaskId,
  attentionTaskId,
  hasMission,
  onInspect,
  onRegisterOutcome,
  onSelectTask,
  outcomes,
  sectionProps,
  t,
}) {
  return (
    <section className="team-pane team-pane--outcomes" aria-label={t('work.team.outcomes')} {...sectionProps}>
      <header className="team-pane__header">
        <div>
          <h2 id="team-outcomes-title">{t('work.team.deliveryBoard')}</h2>
          <span className="team-pane__eyebrow">{t('work.team.convergence')}</span>
        </div>
        <span className="team-pane__count">{outcomes.length}</span>
      </header>
      {hasMission ? (
        <div className="team-outcome-view-switch" aria-label={t('work.team.outcomeViews')} role="group">
          <span data-active="true">{t('work.team.taskView')}</span>
          <button type="button" onClick={() => onInspect('activity')}>{t('work.team.timelineView')}</button>
        </div>
      ) : null}
      {outcomes.length ? (
        <ol className="team-outcome-list">
          {outcomes.map((task, index) => {
            const isAttentionTarget = task.id === attentionTaskId;
            const isActiveReviewTarget = task.id === activeReviewTaskId;
            const owner = translated(t, `work.plan.role.${task.owner}`, task.owner);
            return (
              <li
                key={task.id}
                ref={(node) => onRegisterOutcome(task.id, node)}
                data-active-review={isActiveReviewTarget ? 'true' : undefined}
                data-attention={isAttentionTarget ? 'true' : undefined}
                data-state={task.status}
                data-task-id={task.id}
              >
                <button
                  type="button"
                  aria-pressed={isActiveReviewTarget}
                  onClick={(event) => onSelectTask(task, event.currentTarget)}
                >
                  <span className="team-outcome-card__heading">
                    <span className="team-outcome-card__index">{index + 1}</span>
                    <span className="team-outcome-list__title">
                      <strong>{task.objective}</strong>
                      <small>{task.id}</small>
                    </span>
                    <StatusBadge tone={tone(task.status)}>{statusText(task.status, t)}</StatusBadge>
                  </span>
                  <TaskProgress status={task.status} />
                  <span className="team-outcome-card__owner">
                    <TeamAvatar label={owner} />
                    <span>
                      <small>{t('work.team.owner')}</small>
                      <strong>{owner}</strong>
                    </span>
                  </span>
                  {isAttentionTarget ? (
                    <span className="team-outcome-list__attention">{t('work.team.needsReview')}</span>
                  ) : null}
                  <span className="team-outcome-list__reason">{translated(t, `work.team.reasons.${task.status_reason}`, task.status_reason)}</span>
                  <span className="team-outcome-card__facts">
                    {task.dependencies?.length ? (
                      <span>{t('work.team.dependenciesCount', { count: task.dependencies.length })}</span>
                    ) : <span>{t('work.team.noDependencies')}</span>}
                    {task.acceptance_criteria?.length ? (
                      <span>{t('work.team.acceptanceCount', { count: task.acceptance_criteria.length })}</span>
                    ) : null}
                  </span>
                  {task.primary_outcome ? (
                    <span className="team-outcome-list__deliverable">
                      <FileCheck2 aria-hidden="true" size={14} />
                      <span>{t('work.team.primaryOutcome')}: {task.primary_outcome.label}</span>
                    </span>
                  ) : null}
                  <ChevronRight aria-hidden="true" className="team-outcome-card__chevron" size={16} />
                </button>
                {task.primary_outcome?.href ? (
                  <a
                    className="team-outcome-list__link"
                    href={task.primary_outcome.href}
                    rel="noreferrer"
                    target="_blank"
                  >
                    <span>{t('work.team.openOutcome', { outcome: task.primary_outcome.label })} ↗</span>
                  </a>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : <p className="team-pane__empty">{t('work.team.outcomesEmpty')}</p>}
      {hasMission ? (
        <div className="team-pane__inspect-actions">
          <button type="button" onClick={() => onInspect('plan')}>{t('work.team.inspectWorkflow')}</button>
          <button type="button" onClick={() => onInspect('delivery')}>{t('work.views.delivery')}</button>
        </div>
      ) : null}
    </section>
  );
}

function AdvancedInspector({
  busy,
  data,
  language,
  onClose,
  onDecide,
  onDispatch,
  onRetry,
  onTerminate,
  projectKey,
  reducedMotion,
  t,
  view,
}) {
  if (!view) return null;
  return (
    <section className="team-inspector" aria-labelledby="team-inspector-title">
      <header>
        <div><span>{t('work.team.inspection')}</span><h2 id="team-inspector-title">{t(`work.views.${view}`)}</h2></div>
        <button type="button" onClick={onClose}>{t('work.team.closeInspection')}</button>
      </header>
      {view === 'plan' ? (
        <>
          <WorkflowCanvas
            fallback={reducedMotion}
            onDecided={undefined}
            projectKey={projectKey}
            reviews={data.approval_reviews || []}
            steps={data.workflow_steps || []}
            t={t}
          />
          <PlanView
            approvalReviews={data?.approval_reviews || []}
            board={data.agile_board}
            busyDecision={busy === 'decision' ? 'approved' : ''}
            onDecide={onDecide}
            onRetry={onRetry}
            onTerminate={onTerminate}
            projectKey={projectKey}
            retrying={busy === 'retry'}
            reviews={data?.approval_reviews || []}
            runTitle={data?.team_workspace?.mission?.title || ''}
            steps={data.workflow_steps || []}
            t={t}
            terminating={busy === 'terminate'}
          />
        </>
      ) : null}
      {view === 'activity' ? <ActivityView data={data} language={language} projectKey={projectKey} t={t} /> : null}
      {view === 'execution' ? <ExecutionView defaultAdapter={data?.start_context?.worker_backend?.mode || 'codex'} jobs={data.worker_jobs || []} onDispatch={onDispatch} t={t} /> : null}
      {view === 'messages' ? <MessagesView data={data} projectKey={projectKey} t={t} /> : null}
      {view === 'delivery' ? <DeliveryView data={data} projectKey={projectKey} t={t} /> : null}
    </section>
  );
}

function TeamWorkspace({
  busy,
  data,
  error,
  language,
  onCloseInspector,
  onDecide,
  onDispatch,
  onInspect,
  onRetry,
  onStart,
  onTerminate,
  projectKey,
  reducedMotion,
  t,
  view,
}) {
  const mobileWorkspace = useMobileTeamWorkspace();
  const [mobileTab, setMobileTab] = useState('manager');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [selectedReviewTargetId, setSelectedReviewTargetId] = useState('');
  const [activeReviewTaskId, setActiveReviewTaskId] = useState('');
  const mobileTabRefs = useRef(new Map());
  const outcomeRefs = useRef(new Map());
  const returnFocusRef = useRef(null);
  const workspace = data.team_workspace;
  const mission = workspace.mission;
  const outcomes = workspace.outcome_tasks || [];
  const selectedTask = outcomes.find((task) => task.id === selectedTaskId) || null;
  const attention = workspace.attention_request;
  const attentionReviewTask = attention?.target_type === 'review' && attention?.target_id
    ? outcomes.find((task) => (
      task.id === attention.task_id
      && task.review_target_type === 'review'
      && task.review_target_id === attention.target_id
    )) || null
    : null;
  const activeReviewTargetId = activeReviewTaskId === attentionReviewTask?.id
    ? activeReviewTaskId
    : '';
  const taskForModal = selectedTask ? {
    ...selectedTask,
    title: selectedTask.objective,
    role: selectedTask.owner,
    status: selectedTask.status,
    review_target_id: selectedReviewTargetId || selectedTask.review_target_id || '',
    artifact_refs: [
      selectedTask.primary_outcome?.path,
      ...(selectedTask.supporting_refs || []),
    ].filter(Boolean),
  } : null;
  const registerOutcome = useCallback((taskId, node) => {
    if (node) outcomeRefs.current.set(taskId, node);
    else outcomeRefs.current.delete(taskId);
  }, []);
  const openOutcome = useCallback((task, trigger) => {
    returnFocusRef.current = trigger || document.activeElement;
    setActiveReviewTaskId(task.id === attentionReviewTask?.id ? task.id : '');
    setSelectedReviewTargetId(task.review_target_id || '');
    setSelectedTaskId(task.id);
  }, [attentionReviewTask]);
  const reviewAttentionOutcome = useCallback((trigger) => {
    if (!attentionReviewTask) return;
    returnFocusRef.current = trigger || document.activeElement;
    setActiveReviewTaskId(attentionReviewTask.id);
    if (!mobileWorkspace) {
      outcomeRefs.current.get(attentionReviewTask.id)?.scrollIntoView?.({
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'nearest',
        inline: 'nearest',
      });
    }
    setSelectedReviewTargetId(attention.target_id);
    setSelectedTaskId(attentionReviewTask.id);
  }, [attention, attentionReviewTask, mobileWorkspace, reducedMotion]);
  const closeTaskModal = useCallback(() => {
    const returnFocus = returnFocusRef.current;
    returnFocusRef.current = null;
    setSelectedTaskId('');
    setSelectedReviewTargetId('');
    returnFocus?.focus?.();
  }, []);
  const handleMobileTabKeyDown = useCallback((event, currentTab) => {
    const currentIndex = MOBILE_TEAM_TABS.indexOf(currentTab);
    let nextIndex;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % MOBILE_TEAM_TABS.length;
    else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + MOBILE_TEAM_TABS.length) % MOBILE_TEAM_TABS.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = MOBILE_TEAM_TABS.length - 1;
    else return;
    event.preventDefault();
    const nextTab = MOBILE_TEAM_TABS[nextIndex];
    setMobileTab(nextTab);
    mobileTabRefs.current.get(nextTab)?.focus();
  }, []);
  const sectionProps = (tab) => ({
    id: `team-panel-${tab}`,
    'data-mobile-active': mobileTab === tab ? 'true' : 'false',
    ...(mobileWorkspace ? {
      'aria-labelledby': `team-tab-${tab}`,
      hidden: mobileTab !== tab,
      role: 'tabpanel',
    } : {}),
  });
  useEffect(() => {
    if (!mobileWorkspace || mobileTab !== 'outcomes' || !activeReviewTargetId) return;
    outcomeRefs.current.get(activeReviewTargetId)?.scrollIntoView?.({
      behavior: reducedMotion ? 'auto' : 'smooth',
      block: 'nearest',
      inline: 'nearest',
    });
  }, [activeReviewTargetId, mobileTab, mobileWorkspace, reducedMotion]);
  const mobileTabs = [
    { id: 'execution', label: t('work.team.execution') },
    { id: 'manager', label: t('work.team.manager') },
    { id: 'outcomes', label: t('work.team.outcomes') },
  ];
  return (
    <>
      <div
        aria-label={t('work.team.navigation')}
        aria-orientation="horizontal"
        className="team-mobile-nav"
        role="tablist"
      >
        {mobileTabs.map((tab) => {
          const selected = mobileTab === tab.id;
          const hasAttention = tab.id === 'manager' && Boolean(attention);
          return (
            <button
              aria-controls={`team-panel-${tab.id}`}
              aria-label={hasAttention ? `${tab.label}. ${t('work.manager.needsYou')}` : tab.label}
              aria-selected={selected}
              data-active={selected ? 'true' : 'false'}
              id={`team-tab-${tab.id}`}
              key={tab.id}
              onClick={() => setMobileTab(tab.id)}
              onKeyDown={(event) => handleMobileTabKeyDown(event, tab.id)}
              ref={(node) => {
                if (node) mobileTabRefs.current.set(tab.id, node);
                else mobileTabRefs.current.delete(tab.id);
              }}
              role="tab"
              tabIndex={selected ? 0 : -1}
              type="button"
            >
              <span>{tab.label}</span>
              {hasAttention ? (
                <span className="team-mobile-nav__attention">
                  <span className="sr-only">{t('work.manager.needsYou')}</span>
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <div className="team-workspace" aria-label={t('work.team.workspace')}>
        <ManagerPane
          attention={workspace.attention_request}
          busy={busy}
          capabilities={workspace.capabilities || []}
          data={data}
          error={error}
          language={language}
          mission={mission}
          onDecide={onDecide}
          onInspect={onInspect}
          onReview={attentionReviewTask ? reviewAttentionOutcome : undefined}
          onRetry={onRetry}
          onStart={onStart}
          onTerminate={onTerminate}
          projectKey={projectKey}
          sectionProps={sectionProps('manager')}
          timeline={workspace.manager_timeline || []}
          t={t}
        />
        <ExecutionPane
          actors={workspace.execution_actors || []}
          hasMission={Boolean(mission)}
          onInspect={onInspect}
          outcomes={outcomes}
          reducedMotion={reducedMotion}
          sectionProps={sectionProps('execution')}
          t={t}
        />
        <OutcomesPane
          activeReviewTaskId={activeReviewTargetId}
          attentionTaskId={attentionReviewTask?.id || ''}
          hasMission={Boolean(mission)}
          onInspect={onInspect}
          onRegisterOutcome={registerOutcome}
          onSelectTask={openOutcome}
          outcomes={outcomes}
          sectionProps={sectionProps('outcomes')}
          t={t}
        />
      </div>
      {taskForModal ? (
        <TaskDetailModal
          task={taskForModal}
          reviews={data.approval_reviews || []}
          projectKey={projectKey}
          onDecide={onDecide}
          onClose={closeTaskModal}
          t={t}
        />
      ) : null}
      <AdvancedInspector
        busy={busy}
        data={data}
        language={language}
        onClose={onCloseInspector}
        onDecide={onDecide}
        onDispatch={onDispatch}
        onRetry={onRetry}
        onTerminate={onTerminate}
        projectKey={projectKey}
        reducedMotion={reducedMotion}
        t={t}
        view={mission ? view : ''}
      />
    </>
  );
}

export default function WorkPage() {
  const { projectKey = '', runId = '' } = useParams();
  const { language, t } = useOutletContext();
  const reducedMotion = usePrefersReducedMotion();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const viewParam = search.get('view');
  const inspectorView = VIEWS.includes(viewParam) ? viewParam : '';
  const legacyView = VIEWS.includes(viewParam) ? viewParam : 'plan';
  const { data, error, refresh, status } = useProjectState(projectKey);
  const [mutationError, setMutationError] = useState(null);
  const [busy, setBusy] = useState('');
  const [runHistory, setRunHistory] = useState([]);
  const [historyStatus, setHistoryStatus] = useState('loading');
  const [historyError, setHistoryError] = useState(null);
  const [historicalDetail, setHistoricalDetail] = useState(null);
  const projectedWorkspace = data?.team_workspace?.schema_version === 'team_workspace.v1'
    ? data.team_workspace
    : null;
  const projectedMission = projectedWorkspace?.mission || null;
  const hasProjectedWorkspace = Boolean(projectedWorkspace);
  const hasProjectedMission = Boolean(projectedMission);
  const currentRunId = data?.active_run
    ? (projectedMission?.source === 'workflow' ? projectedMission.run_id : data?.state?.run_id) || ''
    : '';
  const currentRunTitle = projectedMission?.title || runTitle(data?.state, t);
  const selectedSummary = runHistory.find((item) => item.run_id === runId);
  const isHistorical = Boolean(runId && selectedSummary && selectedSummary.status !== 'active');
  const selectedKnown = !runId || historyStatus !== 'ready' || Boolean(selectedSummary);
  const workflowSteps = data?.workflow_steps || [];
  const contextStep = currentWorkflowStep(workflowSteps);
  const completedStepCount = workflowSteps.filter((step) => (
    ['done', 'complete', 'completed'].includes(step.status)
  )).length;
  const currentRunSummary = runHistory.find((item) => item.run_id === currentRunId);
  const contextUpdatedAt = projectedMission?.updated_at || currentRunSummary?.updated_at || '';
  const contextUpdatedLabel = formattedRunUpdate(contextUpdatedAt, language);
  const hasRunContext = Boolean(data?.active_run || hasProjectedMission);
  const showRunSelector = runHistory.length > 0 && (
    !hasRunContext || runHistory.some((item) => item.run_id !== currentRunId)
  );

  const loadRuns = useCallback(async ({ signal } = {}) => {
    setHistoryStatus((current) => (runHistory.length ? current : 'loading'));
    try {
      const payload = await runApi.getProjectRuns(projectKey, { signal });
      setRunHistory(payload.runs || []);
      setHistoryError(null);
      setHistoryStatus('ready');
      return payload;
    } catch (caught) {
      if (caught.name === 'AbortError') return null;
      setHistoryError(caught);
      setHistoryStatus(runHistory.length ? 'stale' : 'error');
      return null;
    }
  }, [projectKey, runHistory.length]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => void loadRuns({ signal: controller.signal }));
    return () => controller.abort();
  }, [projectKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isHistorical || !runId) { queueMicrotask(() => setHistoricalDetail(null)); return undefined; }
    const controller = new AbortController();
    runApi.getProjectRun(projectKey, runId, { signal: controller.signal })
      .then(setHistoricalDetail)
      .catch((caught) => { if (caught.name !== 'AbortError') { setHistoryError(caught); setHistoryStatus('stale'); } });
    return () => controller.abort();
  }, [isHistorical, projectKey, runId]);

  useEffect(() => {
    if (!runId && currentRunId) navigate(routes.projectRun(projectKey, currentRunId), { replace: true });
  }, [currentRunId, navigate, projectKey, runId]);

  const projectName = data?.start_context?.project_name || projectKey;
  const tabs = useMemo(() => VIEWS.map((item) => ({ item, label: t(`work.views.${item}`) })), [t]);
  const toast = useToast();

  const openInspector = (item) => {
    const next = new URLSearchParams(search);
    next.set('view', item);
    setSearch(next);
  };
  const closeInspector = () => {
    const next = new URLSearchParams(search);
    next.delete('view');
    setSearch(next, { replace: true });
  };

  const mutate = async (name, operation) => {
    setBusy(name); setMutationError(null);
    try {
      const result = await operation();
      await refresh();
      await loadRuns();
      return result;
    } catch (caught) {
      setMutationError(caught);
      throw caught;
    } finally {
      setBusy('');
    }
  };

  const handleDecide = async (approval, decision, note = '') => {
    try {
      const result = await mutate('decision', () => reviewApi.decideReview(projectKey, approval, decision, note));
      toast.success(t('toast.decisionSubmitted'));
      return result;
    } catch (caught) {
      toast.error(t('toast.decisionError', { error: caught.message || String(caught) }));
      throw caught;
    }
  };

  const start = (goal) => mutate('start', () => runApi.startRun({
    idea: goal,
    project_key: projectKey,
    model_profile: data?.start_context?.model_profile || 'balanced',
    delivery_mode: data?.start_context?.delivery_mode || 'init_project',
    source_type: 'existing_project',
    project_config: data?.start_context?.project_config || '',
    dry_run: false,
  })).catch(() => {});

  if (status === 'loading') return <main id="main-content" className="product-page"><WorkSkeleton t={t} /></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  if (!selectedKnown) return <main id="main-content" className="product-page"><ProductError error={{ code: 'run_not_found', message: t('work.errors.runNotFound', { runId }) }} t={t} /></main>;

  return (
    <main id="main-content" className="product-page product-work-page">
      <header className="product-page-header">
        <div><p className="product-eyebrow">{projectName}</p><h1>{t('work.title')}</h1><p>{t('work.description')}</p></div>
        <div className="product-page-header__meta">
          {hasRunContext ? (
            <section className="work-run-context" aria-label={t('work.manager.currentMission')}>
              <div className="work-run-context__mission">
                <span>{t('work.manager.currentMission')}</span>
                <strong className="work-run-title" title={currentRunTitle}>{currentRunTitle}</strong>
              </div>
              <div className="work-run-context__facts">
                <StatusBadge tone={tone(contextStep?.status || projectedMission?.status || 'active')}>
                  {contextStep
                    ? statusLabel(contextStep, t)
                    : projectedMission
                      ? statusText(projectedMission.status, t)
                      : t('work.active')}
                </StatusBadge>
                {contextStep ? (
                  <span className="work-run-context__fact">
                    <small>{t('work.context.currentStage')}</small>
                    <strong>{stepTitle(contextStep, t)}</strong>
                  </span>
                ) : null}
                {workflowSteps.length ? (
                  <span className="work-run-context__fact">
                    <small>{t('work.context.progressLabel')}</small>
                    <strong>{t('work.context.progress', { complete: completedStepCount, total: workflowSteps.length })}</strong>
                  </span>
                ) : null}
                {contextUpdatedLabel ? (
                  <time className="work-run-context__updated" dateTime={contextUpdatedAt}>
                    {t('work.context.updated', { time: contextUpdatedLabel })}
                  </time>
                ) : null}
              </div>
            </section>
          ) : <StatusBadge>{t('work.noRun')}</StatusBadge>}
          <div className="work-run-context__actions">
            {showRunSelector ? (
              <label className="work-run-selector"><span>{t('work.history.selector')}</span>
                <select value={runId || currentRunId || ''} onChange={(event) => { if (!event.target.value) return; const next = new URLSearchParams(search); navigate(`${routes.projectRun(projectKey, event.target.value)}${next.toString() ? `?${next.toString()}` : ''}`); }}>
                  {!currentRunId && !runId ? (
                    <option disabled value="">
                      {hasProjectedMission
                        ? t('work.history.currentMissionOption', { title: currentRunTitle })
                        : t('work.history.selectPrevious')}
                    </option>
                  ) : null}
                  {runHistory.map((item) => <option key={item.run_id} value={item.run_id}>{runOptionLabel(item, t)}</option>)}
                </select>
              </label>
            ) : null}
            <Link to={routes.projectHome(projectKey)}>{t('work.backHome')}</Link>
          </div>
        </div>
      </header>
      {status === 'stale' ? <ProductError error={error} onRetry={() => refresh()} t={t} /> : null}
      {historyStatus === 'error' || historyStatus === 'stale' ? <ProductError error={historyError} onRetry={() => loadRuns()} t={t} /> : null}
      {isHistorical ? (historicalDetail ? <HistoricalRunView detail={historicalDetail} projectKey={projectKey} summary={selectedSummary} t={t} /> : <WorkSkeleton t={t} />) : hasProjectedWorkspace ? (
        <>
          <TeamWorkspace
            busy={busy}
            data={data}
            error={mutationError}
            language={language}
            onCloseInspector={closeInspector}
            onDecide={handleDecide}
            onDispatch={(values) => mutate('dispatch', () => runApi.dispatchTrack({ ...values, project_key: projectKey }))}
            onInspect={openInspector}
            onRetry={() => mutate('retry', () => runApi.retryRun(projectKey)).catch(() => {})}
            onStart={start}
            onTerminate={() => mutate('terminate', () => runApi.terminateRun(projectKey)).catch(() => {})}
            projectKey={projectKey}
            reducedMotion={reducedMotion}
            t={t}
            view={inspectorView}
          />
        </>
      ) : !data?.active_run && !hasProjectedMission ? (
        <ManagerMissionEntry
          busy={busy === 'start'}
          data={data}
          embedded={false}
          error={mutationError}
          onStart={start}
          t={t}
        />
      ) : (
        <>
          <WorkflowCanvas
            fallback={reducedMotion}
            onDecided={refresh}
            projectKey={projectKey}
            reviews={data.approval_reviews || []}
            steps={data.workflow_steps || []}
            t={t}
          />
          <nav className="product-subnav" aria-label={t('work.views.label')}>
            {tabs.map(({ item, label }) => <button aria-pressed={legacyView === item} className={legacyView === item ? 'is-active' : ''} key={item} onClick={() => setSearch({ view: item })}>{label}</button>)}
          </nav>
          {mutationError ? <ProductError error={mutationError} t={t} /> : null}
          {legacyView === 'plan' ? (
            <PlanView
              approvalReviews={data?.approval_reviews || []}
              board={data.agile_board}
              busyDecision={busy === 'decision' ? 'approved' : ''}
              onDecide={handleDecide}
              onRetry={() => mutate('retry', () => runApi.retryRun(projectKey)).catch(() => {})}
              onTerminate={() => mutate('terminate', () => runApi.terminateRun(projectKey)).catch(() => {})}
              projectKey={projectKey}
              retrying={busy === 'retry'}
              reviews={data?.approval_reviews || []}
              runTitle={currentRunTitle}
              steps={data.workflow_steps || []}
              t={t}
              terminating={busy === 'terminate'}
            />
          ) : null}
          {legacyView === 'activity' ? (
            <ActivityView
              data={data}
              language={language}
              projectKey={projectKey}
              t={t}
            />
          ) : null}
          {legacyView === 'execution' ? <ExecutionView defaultAdapter={data?.start_context?.worker_backend?.mode || 'codex'} jobs={data.worker_jobs || []} onDispatch={(values) => mutate('dispatch', () => runApi.dispatchTrack({ ...values, project_key: projectKey }))} t={t} /> : null}
          {legacyView === 'messages' ? <MessagesView data={data} projectKey={projectKey} t={t} /> : null}
          {legacyView === 'delivery' ? <DeliveryView data={data} projectKey={projectKey} t={t} /> : null}
        </>
      )}
    </main>
  );
}
