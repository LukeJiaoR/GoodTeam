/* eslint-disable react-refresh/only-export-components -- helper functions are exported for unit tests. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import WorkflowCanvas from '../../components/WorkflowCanvas';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import { useToast } from '../../components/ui/ToastProvider';
import { routes } from '../../lib/routing/routes';
import { statusLabel, stepTitle } from '../../utils/workflowHelpers';
import { reviewApi } from '../reviews/reviewApi';
import { runApi } from './runApi';
import useProjectState from './useProjectState';
import CurrentWorkContext from './workspace/CurrentWorkContext.jsx';
import { createConversationDelivery, trackConversationDelivery } from './workspace/conversationFeedback';
import TeamWorkspace, { ManagerMissionEntry } from './workspace/TeamWorkspace';
import {
  recommendationReasonText,
  reviewGateName,
  statusText,
  tone,
  WORK_VIEWS,
} from './workspace/presentationUtils';
import WorkTaskDetailDialog, { findMatchingReview } from './workspace/WorkTaskDetailDialog';

const VIEWS = WORK_VIEWS;
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

function approvalTargetForReview(review, runId = '') {
  if (!review) return null;
  return {
    run_id: runId,
    target_type: 'review',
    target_id: review.approval || '',
    artifact_key: review.artifact || '',
    artifact_version: review.artifact_version || '',
  };
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

function HistoricalRunView({ detail, language, projectKey, reducedMotion, summary, t }) {
  const state = detail?.state || {};
  const title = runTitle({ ...state, ...summary }, t);
  const workspace = detail?.team_workspace?.schema_version === 'team_workspace.v1'
    ? detail.team_workspace
    : null;
  return (
    <section className="work-historical-view">
      <p className="product-eyebrow">{t('work.history.title')}</p>
      <h2>{title}</h2>
      <p>{t('work.history.readOnly')}</p>
      {workspace ? (
        <TeamWorkspace
          busy=""
          data={{ ...detail, team_workspace: workspace }}
          error={null}
          inspectorContent={null}
          language={language}
          projectKey={projectKey}
          reducedMotion={reducedMotion}
          t={t}
          view=""
        />
      ) : null}
      <details className="work-historical-view__details">
        <summary>{t('work.history.details')}</summary>
        <dl className="settings-data-list">
          <div><dt>{t('work.history.goal')}</dt><dd>{summary.idea || state.idea || '—'}</dd></div>
          <div><dt>{t('work.history.source')}</dt><dd>{summary.source_type || state.source_type || '—'}</dd></div>
          <div><dt>{t('work.history.status')}</dt><dd>{summary.status || '—'}</dd></div>
          <div><dt>{t('work.history.currentNode')}</dt><dd>{state.current_node || summary.current_node || '—'}</dd></div>
          <div><dt>{t('work.history.nextNode')}</dt><dd>{state.next_node || summary.next_node || '—'}</dd></div>
          <div><dt>{t('work.history.updated')}</dt><dd><time dateTime={summary.updated_at}>{summary.updated_at || '—'}</time></dd></div>
        </dl>
      </details>
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
  language,
  onDecide,
  onRequestChanges,
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
  const boardTasks = useMemo(() => (
    ['backlog', 'doing', 'blocked', 'done'].flatMap((column) => board?.columns?.[column] || [])
  ), [board]);

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
          <WorkTaskDetailDialog
            language={language}
            task={selectedTask}
            tasks={boardTasks}
            reviews={resolvedReviews}
            projectKey={projectKey}
            onDecide={onDecide}
            onRequestChanges={onRequestChanges}
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

export default function WorkPage() {
  const { projectKey = '', runId = '' } = useParams();
  const { language, t, workDrawer = null } = useOutletContext();
  const reducedMotion = usePrefersReducedMotion();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const viewParam = search.get('view');
  const inspectorView = VIEWS.includes(viewParam) ? viewParam : '';
  const legacyView = VIEWS.includes(viewParam) ? viewParam : 'plan';
  const { data, error, refresh, status } = useProjectState(projectKey);
  const [mutationError, setMutationError] = useState(null);
  const [busy, setBusy] = useState('');
  const [conversationDelivery, setConversationDelivery] = useState(null);
  const [runHistory, setRunHistory] = useState([]);
  const [historyStatus, setHistoryStatus] = useState('loading');
  const [historyError, setHistoryError] = useState(null);
  const [historicalSnapshot, setHistoricalSnapshot] = useState(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const historicalDetail = historicalSnapshot?.projectKey === projectKey && historicalSnapshot?.runId === runId
    ? historicalSnapshot.detail : null;
  const projectedWorkspace = data?.team_workspace?.schema_version === 'team_workspace.v1'
    ? data.team_workspace
    : null;
  const projectedMission = projectedWorkspace?.mission || null;
  const hasProjectedWorkspace = Boolean(projectedWorkspace);
  const hasProjectedMission = Boolean(projectedMission);
  const isTaskAssistantConversation = projectedMission?.source === 'task_assistant';
  const currentRunId = data?.active_run
    ? (projectedMission?.source === 'workflow' ? projectedMission.run_id : data?.state?.run_id) || ''
    : '';
  const currentRunTitle = projectedMission?.title || runTitle(data?.state, t);
  const selectedSummary = runHistory.find((item) => item.run_id === runId);
  const isHistorical = Boolean(runId && selectedSummary && selectedSummary.status !== 'active');
  const selectedKnown = !runId || historyStatus !== 'ready' || Boolean(selectedSummary);
  const workflowSteps = (isHistorical ? historicalDetail?.workflow_steps : data?.workflow_steps) || [];
  const contextMission = isHistorical ? historicalDetail?.team_workspace?.mission : projectedMission;
  const contextTitle = isHistorical ? runTitle(selectedSummary, t) : currentRunTitle;
  const contextIsLoading = (isHistorical && (!historicalDetail || historyStatus !== 'ready'))
    || Boolean(runId && runId !== currentRunId && !isHistorical);
  const contextFreshness = status !== 'ready' ? status : contextIsLoading ? 'loading' : 'ready';
  const visibleTaskTitle = isHistorical ? contextTitle
    : projectedWorkspace?.proposed_task?.run_id ? currentRunTitle
      : projectedWorkspace?.proposed_task?.exact_request || (hasProjectedMission && !isTaskAssistantConversation ? currentRunTitle : '');
  const contextStep = currentWorkflowStep(workflowSteps);
  const completedStepCount = workflowSteps.filter((step) => (
    ['done', 'complete', 'completed'].includes(step.status)
  )).length;
  const currentRunSummary = runHistory.find((item) => item.run_id === currentRunId);
  const contextUpdatedAt = contextMission?.updated_at || (isHistorical ? selectedSummary?.updated_at : currentRunSummary?.updated_at) || '';
  const contextUpdatedLabel = formattedRunUpdate(contextUpdatedAt, language);
  const hasRunContext = Boolean(data?.active_run || (hasProjectedMission && !isTaskAssistantConversation));
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
    if (!isHistorical || !runId) return undefined;
    const controller = new AbortController();
    runApi.getProjectRun(projectKey, runId, { signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) return;
        const detailRunId = detail?.team_workspace?.mission?.run_id || detail?.state?.run_id;
        const detailProjectKey = detail?.team_workspace?.mission?.project_key || detail?.state?.project_key;
        if (detailRunId !== runId || (detailProjectKey && detailProjectKey !== projectKey)) {
          setHistoryError({ code: 'run_not_found', message: 'The requested historical run could not be verified.' });
          setHistoryStatus('stale');
          return;
        }
        setHistoricalSnapshot({ projectKey, runId, detail });
      })
      .catch((caught) => { if (caught.name !== 'AbortError') { setHistoryError(caught); setHistoryStatus('stale'); } });
    return () => controller.abort();
  }, [isHistorical, projectKey, runId, historyRefresh]);

  useEffect(() => {
    if (!runId && currentRunId) navigate(routes.projectRun(projectKey, currentRunId), { replace: true });
  }, [currentRunId, navigate, projectKey, runId]);

  const projectName = data?.start_context?.project_name || projectKey;
  const tabs = useMemo(() => VIEWS.map((item) => ({ item, label: t(`work.views.${item}`) })), [t]);
  const toast = useToast();

  const openInspector = (item) => {
    if (item === inspectorView) {
      const inspector = globalThis.document?.getElementById('team-inspector');
      inspector?.scrollIntoView?.({
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'start',
      });
      inspector?.focus?.({ preventScroll: true });
      return;
    }
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
      if (['message', 'retry_message', 'check_runtime', 'confirm_team_decision', 'dismiss_team_decision', 'edit_task', 'resolve_draft', 'confirm_project_mode', 'task-confirm'].includes(name)) {
        await refresh().catch(() => {});
      }
      throw caught;
    } finally {
      setBusy('');
    }
  };

  const handleDecide = async (approval, decision, note = '', approvalTarget = null) => {
    const currentReview = (data?.approval_reviews || []).find(
      (review) => review.approval === approval && review.is_actionable,
    );
    const expectedApproval = approvalTarget
      || approvalTargetForReview(currentReview, data?.state?.run_id || '');
    try {
      const result = await mutate('decision', () => (
        decision === 'approved'
          ? reviewApi.decideReview(projectKey, approval, decision, note, { expectedApproval })
          : reviewApi.decideReview(projectKey, approval, decision, note)
      ));
      toast.success(t('toast.decisionSubmitted'));
      return result;
    } catch (caught) {
      if (caught?.code === 'stale_approval') {
        await Promise.allSettled([refresh(), loadRuns()]);
      }
      toast.error(t('toast.decisionError', { error: caught.message || String(caught) }));
      throw caught;
    }
  };

  const refreshStaleIntent = async (caught) => {
    if (caught?.code === 'stale_intent_target') {
      await Promise.allSettled([refresh(), loadRuns()]);
    }
    toast.error(t('toast.decisionError', { error: caught.message || String(caught) }));
    throw caught;
  };

  const handleStartChangeIntent = async (approval, approvalTarget = null, initialText) => {
    const currentReview = (data?.approval_reviews || []).find(
      (review) => review.approval === approval && review.is_actionable,
    );
    const expectedTarget = approvalTarget
      || approvalTargetForReview(currentReview, data?.state?.run_id || '');
    try {
      return await mutate('intent-start', () => (
        initialText === undefined
          ? runApi.startChangeIntent(projectKey, approval, expectedTarget)
          : runApi.startChangeIntent(projectKey, approval, expectedTarget, { initialText })
      ));
    } catch (caught) {
      return refreshStaleIntent(caught);
    }
  };

  const handleRequestChanges = async (approval, approvalTarget = null, initialText) => {
    const result = await handleStartChangeIntent(approval, approvalTarget, initialText);
    closeInspector();
    return result;
  };

  // Track API acknowledgement separately from refreshing the view. A failed
  // refresh after a successful POST must not invite submitting the request twice.
  const deliverConversation = async (name, text, operation, intentId = '') => {
    const delivery = createConversationDelivery({
      projectKey, runId: data?.state?.run_id || '', text, intentId,
      previous: conversationDelivery,
    });
    let acknowledged;
    try {
      return await mutate(name, () => trackConversationDelivery({
        delivery,
        operation: async () => {
          const result = await operation(delivery.id);
          if (result !== false) acknowledged = result;
          return result;
        },
        onUpdate: (next) => setConversationDelivery((current) => (
          next.status === 'sending' || current?.id === next.id ? next : current
        )),
      }));
    } catch (caught) {
      if (acknowledged !== undefined) return acknowledged;
      if (intentId) return refreshStaleIntent(caught);
      return false;
    }
  };

  const handleDiscussChangeIntent = (intentId, text) => deliverConversation(
    'intent-discuss', text,
    () => runApi.discussChangeIntent(projectKey, intentId, text), intentId,
  );

  const handleKeepDiscussingChangeIntent = async (intentId) => {
    try {
      return await mutate('intent-discuss', () => (
        runApi.keepDiscussingChangeIntent(projectKey, intentId)
      ));
    } catch (caught) {
      return refreshStaleIntent(caught);
    }
  };

  const handleConfirmChangeIntent = async (intentId) => {
    try {
      const result = await mutate('intent-confirm', () => (
        runApi.confirmChangeIntent(projectKey, intentId)
      ));
      toast.success(t('toast.decisionSubmitted'));
      return result;
    } catch (caught) {
      return refreshStaleIntent(caught);
    }
  };

  const sendTaskAssistantMessage = (text) => deliverConversation(
    'message', text,
    (messageId) => runApi.sendTaskAssistantMessage(projectKey, { message_id: messageId, text }),
  );

  const handleAssistantAction = (request) => mutate(request.action, () => (
    runApi.taskAssistantAction(projectKey, request)
  )).catch(() => false);

  const confirmTaskAssistantProposal = (proposalId, revision) => mutate('task-confirm', () => (
    runApi.taskAssistantAction(projectKey, { action: 'confirm_task', proposal_id: proposalId, expected_revision: revision })
  )).catch(() => {});

  const continueProjectPreparation = (stepId, values) => mutate('preparation', () => (
    runApi.taskAssistantAction(projectKey, { action: 'continue_preparation', step_id: stepId, values })
  )).catch(() => {});

  if (status === 'loading') return <main id="main-content" className="product-page"><WorkSkeleton t={t} /></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  if (!selectedKnown) return <main id="main-content" className="product-page"><ProductError error={{ code: 'run_not_found', message: t('work.errors.runNotFound', { runId }) }} t={t} /></main>;

  const inspectorContent = inspectorView ? (
    <>
      {inspectorView === 'plan' ? (
        <>
          <WorkflowCanvas
            fallback={reducedMotion}
            onDecided={undefined}
            onRequestChanges={handleRequestChanges}
            projectKey={projectKey}
            reviews={data.approval_reviews || []}
            runId={data?.state?.run_id || ''}
            steps={data.workflow_steps || []}
            t={t}
          />
          <PlanView
            approvalReviews={data?.approval_reviews || []}
            board={data.agile_board}
            busyDecision={busy === 'decision' ? 'approved' : ''}
            language={language}
            onDecide={handleDecide}
            onRequestChanges={handleRequestChanges}
            onRetry={() => mutate('retry', () => runApi.retryRun(projectKey)).catch(() => {})}
            onTerminate={() => mutate('terminate', () => runApi.terminateRun(projectKey)).catch(() => {})}
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
      {inspectorView === 'activity' ? <ActivityView data={data} language={language} projectKey={projectKey} t={t} /> : null}
      {inspectorView === 'execution' ? (
        <ExecutionView
          defaultAdapter={data?.start_context?.worker_backend?.mode || 'codex'}
          jobs={data.worker_jobs || []}
          onDispatch={(values) => mutate('dispatch', () => runApi.dispatchTrack({ ...values, project_key: projectKey }))}
          t={t}
        />
      ) : null}
      {inspectorView === 'messages' ? <MessagesView data={data} projectKey={projectKey} t={t} /> : null}
      {inspectorView === 'delivery' ? <DeliveryView data={data} projectKey={projectKey} t={t} /> : null}
    </>
  ) : null;

  const runSelector = showRunSelector ? (
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
  ) : null;

  return (
    <main id="main-content" className="product-page product-work-page" data-context-scope={`${projectKey}:${runId}`}>
      <CurrentWorkContext
        projectKey={projectKey}
        projectName={projectName}
        title={visibleTaskTitle}
        workspace={isHistorical ? historicalDetail?.team_workspace : projectedWorkspace}
        freshness={contextFreshness}
        historical={isHistorical}
        onRefresh={() => { void refresh(); void loadRuns(); setHistoryRefresh((value) => value + 1); }}
        reducedMotion={reducedMotion}
        t={t}
      />
      <header
        aria-hidden={workDrawer ? !workDrawer.open : undefined}
        className="product-page-header work-context-drawer"
        data-open={workDrawer ? String(workDrawer.open) : 'true'}
        id="work-context-drawer"
        inert={workDrawer && !workDrawer.open ? '' : undefined}
        onBlurCapture={workDrawer?.onLeave}
        onFocusCapture={workDrawer?.onEnter}
        onPointerEnter={workDrawer?.onEnter}
        onPointerLeave={workDrawer?.onLeave}
      >
        <div className="product-work-page__identity"><p className="product-eyebrow">{projectName}</p><h1>{t('work.title')}</h1><p>{t('work.description')}</p></div>
        <div className="product-page-header__meta">
          {hasRunContext ? (
            <section className={`work-run-context${showRunSelector ? ' work-run-context--selectable' : ''}`} aria-label={t('work.manager.currentMission')}>
              <div className="work-run-context__mission">
                <span>{t('work.manager.currentMission')}</span>
                <strong className="work-run-title" title={contextTitle}>{contextTitle}</strong>
              </div>
              <div className="work-run-context__facts">
                <StatusBadge tone={tone(contextStep?.status || contextMission?.status || 'active')}>
                  {contextStep
                    ? statusLabel(contextStep, t)
                    : contextMission
                      ? statusText(contextMission.status, t)
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
              {runSelector}
            </section>
          ) : <StatusBadge>{t('work.noRun')}</StatusBadge>}
          <div className="work-run-context__actions">
            {!hasRunContext ? runSelector : null}
          </div>
        </div>
      </header>
      {status === 'stale' ? <ProductError error={error} onRetry={() => refresh()} t={t} /> : null}
      {historyStatus === 'error' || historyStatus === 'stale' ? <ProductError error={historyError} onRetry={() => loadRuns()} t={t} /> : null}
      {isHistorical ? (historicalDetail ? <HistoricalRunView detail={historicalDetail} language={language} projectKey={projectKey} reducedMotion={reducedMotion} summary={selectedSummary} t={t} /> : <WorkSkeleton t={t} />) : hasProjectedWorkspace ? (
        <>
          <TeamWorkspace
            conversationDelivery={conversationDelivery}
            busy={busy}
            data={data}
            error={mutationError}
            inspectorContent={inspectorContent}
            language={language}
            onCloseInspector={closeInspector}
            onConfirmChangeIntent={handleConfirmChangeIntent}
            onDecide={handleDecide}
            onDiscussChangeIntent={handleDiscussChangeIntent}
            onInspect={openInspector}
            onKeepDiscussingChangeIntent={handleKeepDiscussingChangeIntent}
            onRetry={() => mutate('retry', () => runApi.retryRun(projectKey)).catch(() => {})}
            onStart={sendTaskAssistantMessage}
            onAssistantAction={handleAssistantAction}
            onConfirmTask={confirmTaskAssistantProposal}
            onContinuePreparation={continueProjectPreparation}
            onStartChangeIntent={handleStartChangeIntent}
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
          onStart={sendTaskAssistantMessage}
          t={t}
        />
      ) : (
        <>
          <WorkflowCanvas
            fallback={reducedMotion}
            onDecided={refresh}
            onRequestChanges={handleRequestChanges}
            projectKey={projectKey}
            reviews={data.approval_reviews || []}
            runId={data?.state?.run_id || ''}
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
              language={language}
              onDecide={handleDecide}
              onRequestChanges={handleRequestChanges}
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
