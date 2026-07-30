import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import { routes } from '../../lib/routing/routes';
import { runApi } from './runApi';
import useProjectState from './useProjectState';

const VIEWS = ['current', 'plan', 'execution', 'messages', 'delivery'];

function tone(status) {
  if (['complete', 'done', 'approved', 'success'].includes(status)) return 'success';
  if (['blocked', 'failed', 'error'].includes(status)) return 'danger';
  if (['active', 'running', 'doing'].includes(status)) return 'active';
  return 'pending';
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

function reviewPrompt(review, t) {
  if (review?.approval === 'takeover') {
    return {
      action: t('work.review.takeover.action'),
      description: t('work.review.takeover.description'),
      eyebrow: t('work.review.eyebrow'),
      title: t('work.review.takeover.title'),
    };
  }
  return {
    action: t('work.review.generic.action'),
    description: t('work.review.generic.description'),
    eyebrow: t('work.review.eyebrow'),
    title: t('work.review.generic.title'),
  };
}

function WorkSkeleton({ t }) {
  return (
    <div className="product-workspace-skeleton" aria-label={t('product.states.loading')} role="status">
      <span /><span /><span />
    </div>
  );
}

function StartRunPanel({ busy, data, error, onStart, t }) {
  const [goal, setGoal] = useState('');
  const submit = (event) => {
    event.preventDefault();
    if (goal.trim()) onStart(goal.trim());
  };
  return (
    <form className="work-start-panel" onSubmit={submit}>
      <div>
        <p className="product-eyebrow">{t('work.start.eyebrow')}</p>
        <h2>{t('work.start.title')}</h2>
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

function StageRail({ steps, t }) {
  return (
    <ol className="work-stage-rail" aria-label={t('work.stages.label')}>
      {steps.map((step) => (
        <li key={step.key} data-status={step.status}>
          <span className="work-stage-number">{step.number}</span>
          <div><strong>{step.title}</strong><small>{step.status_label}</small></div>
        </li>
      ))}
    </ol>
  );
}

function CurrentView({ data, onRetry, onTerminate, projectKey, retrying, runTitle, t, terminating }) {
  const current = data.workflow_steps?.find((step) => ['active', 'blocked'].includes(step.status))
    || data.workflow_steps?.find((step) => step.status !== 'complete');
  const actionableReview = data.approval_reviews?.find((review) => review.is_actionable);
  const prompt = actionableReview ? reviewPrompt(actionableReview, t) : null;
  return (
    <div className="work-primary-view">
      <section className="work-focus-panel">
        {prompt ? (
          <>
            <p className="product-eyebrow">{prompt.eyebrow}</p>
            <h2>{prompt.title}</h2>
            <p>{prompt.description}</p>
            <Link className="product-button product-button--primary" to={routes.projectReviewItem(projectKey, actionableReview.approval)}>{prompt.action}</Link>
          </>
        ) : (
          <>
            <p className="product-eyebrow">{t('work.current.next')}</p>
            <h2>{current?.title || t('work.current.complete')}</h2>
            <p>{current?.summary || t('work.current.completeDescription')}</p>
            {current?.feedback ? <p className="work-feedback">{current.feedback}</p> : null}
          </>
        )}
        {!actionableReview && data.agile_board?.counts?.blocked > 0 ? (
          <Button busy={retrying} onClick={onRetry}>{t('work.actions.retryBlocked')}</Button>
        ) : null}
        <details className="work-run-controls">
          <summary>{t(actionableReview ? 'work.actions.abandonRun' : 'work.actions.runControls')}</summary>
          <p>{t('work.actions.terminateScope', { runTitle })}</p>
          <Button busy={terminating} variant="danger" onClick={onTerminate}>{t('work.actions.terminate')}</Button>
        </details>
      </section>
      <StageRail steps={data.workflow_steps || []} t={t} />
      <section className="work-actions-panel">
        <h2>{t('work.actions.title')}</h2>
        <p>{t('work.actions.description')}</p>
        {data.action_items?.length ? <ol>{data.action_items.slice(0, 12).map((action, index) => <li key={action.id || `${action.action}-${action.timestamp}-${index}`}><div><strong>{action.title || action.action}</strong><small>{action.node_transition || action.after_node || ''}</small></div><time dateTime={action.timestamp}>{action.timestamp || '—'}</time></li>)}</ol> : <p className="product-empty-copy">{t('work.actions.empty')}</p>}
      </section>
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
      {detail?.workflow_steps?.length ? <StageRail steps={detail.workflow_steps} t={t} /> : null}
      <Link to={routes.projectWork(projectKey)}>{t('work.history.current')}</Link>
    </section>
  );
}

function PlanView({ board, t }) {
  const columns = ['backlog', 'doing', 'blocked', 'done'];
  return (
    <div className="work-board">
      {columns.map((column) => (
        <section key={column}>
          <h2>{t(`work.plan.${column}`)} <span>{board?.counts?.[column] || 0}</span></h2>
          <ul>
            {(board?.columns?.[column] || []).map((task, index) => (
              <li key={task.id || `${column}-${index}`}>
                <strong>{task.title || task.name || task.id}</strong>
                {task.description ? <p>{task.description}</p> : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ExecutionView({ jobs, onDispatch, t }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [values, setValues] = useState({ name: '', role: 'implementation', adapter: 'codex', task: '' });
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try { await onDispatch(values); setOpen(false); setValues({ ...values, name: '', task: '' }); }
    catch (caught) { setError(caught); }
    finally { setBusy(false); }
  };
  return (
    <div className="work-section-stack">
      <header className="work-section-header">
        <div><h2>{t('work.execution.title')}</h2><p>{t('work.execution.description')}</p></div>
        <Button onClick={() => setOpen((value) => !value)}>{t('work.execution.dispatch')}</Button>
      </header>
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
    runApi.getMessages(projectKey, runId, '', { signal: controller.signal })
      .then((payload) => { setMessages(payload.messages || []); setStatus('ready'); })
      .catch((error) => { if (error.name !== 'AbortError') setStatus('error'); });
    return () => controller.abort();
  }, [projectKey, runId]);
  if (status === 'loading') return <WorkSkeleton t={t} />;
  if (!messages.length) return <p className="product-empty-copy">{t('work.messages.empty')}</p>;
  return <ol className="work-message-list">{messages.map((message, index) => (
    <li key={message.id || index}><strong>{message.role || message.actor || t('work.messages.system')}</strong><p>{message.content || message.text}</p></li>
  ))}</ol>;
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
  return (
    <div className="work-section-stack">
      <header className="work-section-header"><div><h2>{t('work.delivery.title')}</h2><p>{t('work.delivery.description')}</p></div>
        <Button busy={smokeBusy} onClick={smoke}>{t('work.delivery.smoke')}</Button></header>
      {error ? <ProductError error={error} t={t} /> : null}
      <div className="work-delivery-grid">
        <section><h3>{t('work.delivery.pullRequests')}</h3><pre>{JSON.stringify(details?.pull_requests || details?.prs || [], null, 2)}</pre></section>
        <section><h3>{t('work.delivery.ci')}</h3><pre>{JSON.stringify(details?.ci || details?.checks || [], null, 2)}</pre></section>
        <section><h3>{t('work.delivery.evidence')}</h3><pre>{JSON.stringify(data?.resources || details?.evidence || [], null, 2)}</pre></section>
        <section><h3>{t('work.delivery.smokeRuns')}</h3><pre>{JSON.stringify(data?.smoke_run_items || [], null, 2)}</pre></section>
      </div>
    </div>
  );
}

export default function WorkPage() {
  const { projectKey = '', runId = '' } = useParams();
  const { t } = useOutletContext();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const view = VIEWS.includes(search.get('view')) ? search.get('view') : 'current';
  const { data, error, refresh, status } = useProjectState(projectKey);
  const [mutationError, setMutationError] = useState(null);
  const [busy, setBusy] = useState('');
  const [runHistory, setRunHistory] = useState([]);
  const [historyStatus, setHistoryStatus] = useState('loading');
  const [historyError, setHistoryError] = useState(null);
  const [historicalDetail, setHistoricalDetail] = useState(null);
  const currentRunId = data?.state?.run_id || '';
  const currentRunTitle = runTitle(data?.state, t);
  const selectedSummary = runHistory.find((item) => item.run_id === runId);
  const isHistorical = Boolean(runId && selectedSummary && selectedSummary.status !== 'active');
  const selectedKnown = !runId || historyStatus !== 'ready' || Boolean(selectedSummary);

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
  const mutate = async (name, operation) => {
    setBusy(name); setMutationError(null);
    try { const result = await operation(); await refresh(); await loadRuns(); return result; }
    catch (caught) { setMutationError(caught); return null; }
    finally { setBusy(''); }
  };
  const start = (goal) => mutate('start', () => runApi.startRun({
    idea: goal,
    project_key: projectKey,
    model_profile: data?.start_context?.model_profile || 'balanced',
    delivery_mode: data?.start_context?.delivery_mode || 'feature_increment',
    source_type: 'existing_project',
    project_config: data?.start_context?.project_config || '',
    dry_run: false,
  }));

  if (status === 'loading') return <main id="main-content" className="product-page"><WorkSkeleton t={t} /></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  if (!selectedKnown) return <main id="main-content" className="product-page"><ProductError error={{ code: 'run_not_found', message: t('work.errors.runNotFound', { runId }) }} t={t} /></main>;

  return (
    <main id="main-content" className="product-page product-work-page">
      <header className="product-page-header">
        <div><p className="product-eyebrow">{projectName}</p><h1>{t('work.title')}</h1><p>{t('work.description')}</p></div>
        <div className="product-page-header__meta">
          {data?.active_run ? <><StatusBadge tone="active">{t('work.active')}</StatusBadge><strong className="work-run-title">{currentRunTitle}</strong></> : <StatusBadge>{t('work.noRun')}</StatusBadge>}
          {runHistory.length ? (
            <label className="work-run-selector"><span className="sr-only">{t('work.history.selector')}</span>
              <select value={runId || currentRunId} onChange={(event) => navigate(routes.projectRun(projectKey, event.target.value))}>
                {runHistory.map((item) => <option key={item.run_id} value={item.run_id}>{runOptionLabel(item, t)}</option>)}
              </select>
            </label>
          ) : null}
          <Link to={routes.projectHome(projectKey)}>{t('work.backHome')}</Link>
        </div>
      </header>
      {status === 'stale' ? <ProductError error={error} onRetry={() => refresh()} t={t} /> : null}
      {historyStatus === 'error' || historyStatus === 'stale' ? <ProductError error={historyError} onRetry={() => loadRuns()} t={t} /> : null}
      {isHistorical ? (historicalDetail ? <HistoricalRunView detail={historicalDetail} projectKey={projectKey} summary={selectedSummary} t={t} /> : <WorkSkeleton t={t} />) : !data?.active_run ? <StartRunPanel busy={busy === 'start'} data={data} error={mutationError} onStart={start} t={t} /> : (
        <>
          <nav className="product-subnav" aria-label={t('work.views.label')}>
            {tabs.map(({ item, label }) => <button className={view === item ? 'is-active' : ''} key={item} onClick={() => setSearch({ view: item })}>{label}</button>)}
          </nav>
          {mutationError ? <ProductError error={mutationError} t={t} /> : null}
          {view === 'current' ? <CurrentView data={data} projectKey={projectKey} retrying={busy === 'retry'} runTitle={currentRunTitle} terminating={busy === 'terminate'} onRetry={() => mutate('retry', () => runApi.retryRun(projectKey))} onTerminate={() => mutate('terminate', () => runApi.terminateRun(projectKey))} t={t} /> : null}
          {view === 'plan' ? <PlanView board={data.agile_board} t={t} /> : null}
          {view === 'execution' ? <ExecutionView jobs={data.worker_jobs || []} onDispatch={(values) => mutate('dispatch', () => runApi.dispatchTrack({ ...values, project_key: projectKey }))} t={t} /> : null}
          {view === 'messages' ? <MessagesView data={data} projectKey={projectKey} t={t} /> : null}
          {view === 'delivery' ? <DeliveryView data={data} projectKey={projectKey} t={t} /> : null}
        </>
      )}
    </main>
  );
}
