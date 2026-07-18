import { useCallback, useEffect, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import InlineNotice from '../../components/ui/InlineNotice';
import PageState from '../../components/ui/PageState';
import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';
import { projectApi } from './projectApi';
import NewProjectReadinessForm from './NewProjectReadinessForm';
import ProjectFacts from './ProjectFacts';
import ReadinessCenter from './ReadinessCenter';
import ReadinessInputPanel from './ReadinessInputPanel';
import RepositoryOverview from './RepositoryOverview';
import { errorMessage, recoveryIds, recoveryLabel } from './readinessPresentation';

const POLL_INTERVAL_MS = 1_200;

function isAbortError(error, signal) {
  return signal?.aborted || error?.name === 'AbortError';
}

function projectKeyFromPath(pathname = globalThis.location?.pathname || '') {
  const match = pathname.match(/^\/projects\/([^/]+)\/home\/?$/);
  return match ? decodeURIComponent(match[1]) : '';
}

function capabilityEntries(capabilities = {}) {
  return [
    ['inspect_project_facts', 'readiness.capabilities.inspectProjectFacts'],
    ['edit_project_settings', 'readiness.capabilities.editProjectSettings'],
    ['run_readiness_steps', 'readiness.capabilities.runReadinessSteps'],
    ['start_workflow', 'readiness.capabilities.startWorkflow'],
  ].filter(([key]) => capabilities[key]);
}

function makeIdempotencyKey() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return `readiness-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function homeFromMutation(payload, currentHome) {
  if (payload?.home && typeof payload.home === 'object') return payload.home;
  if (payload?.project_home && typeof payload.project_home === 'object') return payload.project_home;
  if (payload?.readiness && payload?.project) return payload;
  if (payload?.readiness && currentHome) return { ...currentHome, ...payload };
  return null;
}

function readinessStatus(readiness = {}) {
  return readiness.execution?.status || readiness.status || readiness.lifecycle || 'pending';
}

function currentInputRequest(readiness = {}) {
  const steps = Array.isArray(readiness.steps) ? readiness.steps : [];
  const step = steps.find((item) => (
    item?.status === 'needs_input'
    && item?.id !== 'project_mode'
  ));
  if (!step) return null;
  return {
    ...(step.input_requirements || {}),
    id: step.id,
    step_id: step.id,
  };
}

function repositoryArtifact(readiness = {}) {
  const artifacts = readiness.artifacts;
  if (!artifacts || typeof artifacts !== 'object' || Array.isArray(artifacts)) return null;
  for (const ref of ['repository_overview', 'repository_understanding']) {
    if (artifacts[ref]) return artifacts[ref];
  }
  return null;
}

function lifecycleTone(status) {
  if (status === 'complete' || status === 'ready') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'needs_input' || status === 'attention_required') return 'warning';
  if (status === 'running') return 'active';
  return 'pending';
}

function defaultNavigateWorkflow(workflowUrl) {
  const currentOrigin = globalThis.location?.origin;
  const resolved = new URL(workflowUrl, currentOrigin);
  if (!currentOrigin || resolved.origin !== currentOrigin) {
    throw new TypeError('Workflow URL must use the current GoodTeam origin.');
  }
  globalThis.location.assign(`${resolved.pathname}${resolved.search}${resolved.hash}`);
}

export default function ProjectHomePage({
  api: apiOverride,
  language: languageOverride,
  navigateWorkflow = defaultNavigateWorkflow,
  projectKey: projectKeyOverride,
}) {
  const api = apiOverride || projectApi;
  const language = languageOverride || 'en';
  const t = (key, params) => translate(language, key, params);
  const projectKey = projectKeyOverride || projectKeyFromPath();
  const [home, setHome] = useState(null);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const [busyAction, setBusyAction] = useState('');
  const [overviewArtifact, setOverviewArtifact] = useState(null);
  const [selectedArtifact, setSelectedArtifact] = useState(null);
  const [loadingArtifact, setLoadingArtifact] = useState('');
  const idempotencyKey = useRef('');
  const requestedArtifacts = useRef(new Set());
  const actionInFlight = useRef(false);
  const stateGeneration = useRef(0);

  const refreshHome = useCallback(async ({ signal, surfaceError = true } = {}) => {
    const requestGeneration = stateGeneration.current;
    try {
      const payload = await api.getProjectHome(projectKey, { signal });
      if (signal?.aborted || requestGeneration !== stateGeneration.current) return null;
      setHome(payload);
      setOverviewArtifact(repositoryArtifact(payload?.readiness || {}));
      setSelectedArtifact(null);
      setStatus('ready');
      if (surfaceError) setError(null);
      return payload;
    } catch (caught) {
      if (!isAbortError(caught, signal)) {
        if (surfaceError) {
          setError(caught);
          setStatus((current) => (current === 'loading' ? 'error' : current));
        }
      }
      return null;
    }
  }, [api, projectKey]);

  useEffect(() => {
    const controller = new AbortController();
    requestedArtifacts.current = new Set();
    api.getProjectHome(projectKey, { signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setHome(payload);
        setOverviewArtifact(repositoryArtifact(payload?.readiness || {}));
        setSelectedArtifact(null);
        setStatus('ready');
        setError(null);
      })
      .catch((caught) => {
        if (isAbortError(caught, controller.signal)) return;
        setError(caught);
        setStatus('error');
      });
    return () => controller.abort();
  }, [api, projectKey]);

  const readiness = home?.readiness || {};
  const executionStatus = readinessStatus(readiness);

  useEffect(() => {
    if (executionStatus !== 'running') return undefined;
    let timer = 0;
    let controller = null;
    let active = true;
    const poll = async () => {
      controller = new AbortController();
      await refreshHome({ signal: controller.signal, surfaceError: false });
      if (active && !controller.signal.aborted) {
        timer = globalThis.setTimeout(poll, POLL_INTERVAL_MS);
      }
    };
    timer = globalThis.setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      active = false;
      globalThis.clearTimeout(timer);
      controller?.abort();
    };
  }, [executionStatus, refreshHome]);

  const loadStepArtifact = useCallback(async (stepId, artifactRef = '') => {
    setLoadingArtifact(stepId);
    try {
      const artifact = await api.getReadinessStepArtifact(projectKey, stepId);
      setSelectedArtifact({ ref: artifactRef || stepId, payload: artifact });
      if (artifactRef === 'repository_overview' || stepId === 'repository_understanding') {
        setOverviewArtifact(artifact);
      }
      return artifact;
    } catch (caught) {
      setError(caught);
      return null;
    } finally {
      setLoadingArtifact('');
    }
  }, [api, projectKey]);

  const inlineOverview = repositoryArtifact(readiness);
  useEffect(() => {
    if (inlineOverview) return;
    const repositoryStep = readiness.steps?.find?.((step) => step?.id === 'repository_understanding');
    if (repositoryStep?.status !== 'complete' || requestedArtifacts.current.has(repositoryStep.id)) return;
    requestedArtifacts.current.add(repositoryStep.id);
    void loadStepArtifact(repositoryStep.id, 'repository_overview');
  }, [inlineOverview, loadStepArtifact, readiness.steps]);

  const applyMutation = async (action, request) => {
    if (actionInFlight.current) return null;
    actionInFlight.current = true;
    stateGeneration.current += 1;
    setBusyAction(action);
    setError(null);
    try {
      const payload = await request();
      stateGeneration.current += 1;
      const nextHome = homeFromMutation(payload, home);
      if (nextHome) {
        setHome(nextHome);
        setOverviewArtifact(repositoryArtifact(nextHome.readiness || {}));
        setSelectedArtifact(null);
        setStatus('ready');
      } else {
        await refreshHome();
      }
      return payload;
    } catch (caught) {
      stateGeneration.current += 1;
      setError(caught);
      return null;
    } finally {
      actionInFlight.current = false;
      setBusyAction('');
    }
  };

  const startReadiness = (inputs = {}) => {
    if (!idempotencyKey.current || executionStatus === 'cancelled') {
      idempotencyKey.current = makeIdempotencyKey();
    }
    return applyMutation('start', () => api.startReadiness(projectKey, {
      inputs,
      idempotencyKey: idempotencyKey.current,
    }));
  };

  const confirmMode = (mode) => applyMutation(`mode:${mode}`, () => (
    api.confirmProjectMode(projectKey, mode)
  ));

  const submitInput = (input) => applyMutation('input', () => (
    api.submitReadinessInput(projectKey, input)
  ));

  const retryStep = (stepId) => applyMutation(`retry:${stepId}`, () => (
    api.retryReadinessStep(projectKey, stepId)
  ));

  const startWorkflow = async () => {
    if (actionInFlight.current || !home?.capabilities?.start_workflow) return;
    actionInFlight.current = true;
    setBusyAction('workflow');
    setError(null);
    try {
      const payload = await api.startProjectWorkflow(projectKey);
      if (!payload?.workflow_url) throw new TypeError('Missing workflow_url.');
      navigateWorkflow(payload.workflow_url);
      actionInFlight.current = false;
      setBusyAction('');
    } catch (caught) {
      setError(caught);
      actionInFlight.current = false;
      setBusyAction('');
    }
  };

  if (status === 'loading') {
    return <main id="main-content" className="project-home-page"><PageState kind="loading" title={t('projects.home.loading')} /></main>;
  }

  if (status === 'error') {
    return (
      <main id="main-content" className="project-home-page">
        <PageState
          kind="error"
          title={t('projects.home.error')}
          description={errorMessage(language, error)}
          action={(
            <div className="projects-recovery-actions">
              <Button onClick={() => {
                setStatus('loading');
                void refreshHome();
              }} variant="secondary">{t('product.actions.retry')}</Button>
              <a className="product-button product-button--secondary" href="/projects">{t('product.actions.returnToProjects')}</a>
            </div>
          )}
        />
      </main>
    );
  }

  const capabilities = home?.capabilities || {};
  const nextAction = home?.next_action || {};
  const lifecycle = readiness.lifecycle || 'legacy';
  const capabilityList = capabilityEntries(capabilities);
  const inputRequest = currentInputRequest(readiness);
  const errorRecoveries = recoveryIds(error?.recovery);
  const errorStepId = String(error?.details?.step_id || '');
  const errorAllowsStepRetry = errorStepId && errorRecoveries.some((item) => (
    ['retry', 'retry_readiness', 'retry_request'].includes(item)
  ));
  const startReasonKey = capabilities.start_workflow_reason
    ? `readiness.start.reason.${capabilities.start_workflow_reason}`
    : '';
  const startReason = startReasonKey ? t(startReasonKey) : '';
  const hasExecution = Boolean(readiness.execution?.id);
  const showExistingStart = readiness.project_mode === 'existing_project'
    && (!hasExecution || executionStatus === 'cancelled');
  const showNewStart = readiness.project_mode === 'new_project'
    && (!hasExecution || executionStatus === 'cancelled');

  return (
    <main id="main-content" className="project-home-page">
      <header className="project-home-header">
        <div>
          <p className="project-home-eyebrow">{home.project?.project_key}</p>
          <h1>{home.project?.display_name || home.project?.project_key || t('projects.home.title')}</h1>
          <code className="project-home-root">{home.project?.root}</code>
        </div>
        <StatusBadge tone={lifecycleTone(executionStatus === 'pending' ? lifecycle : executionStatus)}>
          {t(`readiness.status.${executionStatus}`)}
        </StatusBadge>
      </header>

      <section className="project-home-grid" aria-label={t('readiness.title')}>
        <section className="project-next-action" aria-labelledby="project-next-action-title">
          <div className="projects-section-heading">
            <h2 id="project-next-action-title">{t('readiness.nextAction')}</h2>
          </div>

          {readiness.project_mode === 'unconfirmed' ? (
            <div className="mode-decision-panel">
              <h3>{t('readiness.modeDecision.title')}</h3>
              <p>{t('readiness.modeDecision.description')}</p>
              <div className="mode-decision-actions">
                <Button busy={busyAction === 'mode:new_project'} onClick={() => confirmMode('new_project')}>
                  {t('readiness.modeDecision.newProject')}
                </Button>
                <Button busy={busyAction === 'mode:existing_project'} onClick={() => confirmMode('existing_project')} variant="secondary">
                  {t('readiness.modeDecision.existingProject')}
                </Button>
                <a className="product-button product-button--secondary" href="/projects">
                  {t('readiness.modeDecision.decideLater')}
                </a>
              </div>
            </div>
          ) : null}

          {showExistingStart ? (
            <div className="readiness-start-panel">
              <div>
                {nextAction.id ? <p className="readiness-input-eyebrow">{t(`readiness.steps.${nextAction.id}`)}</p> : null}
                <h3>{t('readiness.start.existingTitle')}</h3>
                <p>{t('readiness.start.existingDescription')}</p>
              </div>
              <Button busy={busyAction === 'start'} onClick={() => startReadiness({})}>
                {t('readiness.actions.startUnderstanding')}
              </Button>
            </div>
          ) : null}

          {showNewStart ? (
            <NewProjectReadinessForm
              busy={busyAction === 'start'}
              language={language}
              onSubmit={startReadiness}
            />
          ) : null}

          {executionStatus === 'running' ? (
            <InlineNotice title={t('readiness.running.title')} tone="info">
              <span>{t('readiness.running.description')}</span>
              <Button
                busy={busyAction === 'cancel'}
                onClick={() => applyMutation('cancel', () => api.cancelReadiness(projectKey))}
                variant="secondary"
              >
                {t('readiness.actions.cancel')}
              </Button>
            </InlineNotice>
          ) : null}

          {inputRequest ? (
            <ReadinessInputPanel
              busy={busyAction === 'input'}
              inputRequest={inputRequest}
              key={inputRequest.step_id}
              language={language}
              onSubmit={submitInput}
            />
          ) : null}

          {!showExistingStart && !showNewStart && !inputRequest && executionStatus !== 'running' ? (
            <InlineNotice
              title={t(`readiness.steps.${nextAction.id || 'shared_readiness_complete'}`)}
              tone={executionStatus === 'complete' ? 'success' : executionStatus === 'failed' ? 'danger' : 'warning'}
            >
              {t(`readiness.status.${executionStatus}`)}
            </InlineNotice>
          ) : null}

          {error ? (
            <InlineNotice className="project-home-error" title={error.code || t('product.errors.generic')} tone="danger">
              <span>{errorMessage(language, error)}</span>
              {error.requestId ? <code>{t('projects.requestId', { requestId: error.requestId })}</code> : null}
              {errorRecoveries.length > 0 ? (
                <span>{t('readiness.error.recovery')}: {errorRecoveries.map((item) => (
                  recoveryLabel(language, item)
                )).join(', ')}</span>
              ) : null}
              <div className="projects-recovery-actions">
                {errorAllowsStepRetry ? (
                  <Button
                    busy={busyAction === `retry:${errorStepId}`}
                    onClick={() => retryStep(errorStepId)}
                    variant="secondary"
                  >
                    {t('readiness.actions.retryStep')}
                  </Button>
                ) : null}
                <Button onClick={() => refreshHome()} variant="secondary">
                  {t('product.actions.reload')}
                </Button>
                {errorRecoveries.some((item) => [
                  'choose_another_directory',
                  'choose_project',
                  'return_to_projects',
                ].includes(item)) ? (
                  <a className="product-button product-button--secondary" href="/projects">
                    {t('product.actions.returnToProjects')}
                  </a>
                ) : null}
              </div>
            </InlineNotice>
          ) : null}

          <div className="workflow-start-panel">
            <Button
              busy={busyAction === 'workflow'}
              disabled={!capabilities.start_workflow}
              onClick={startWorkflow}
            >
              {t('readiness.start.label')}
            </Button>
            {!capabilities.start_workflow && startReason ? <p>{startReason}</p> : null}
          </div>
        </section>

        <ProjectFacts facts={home.facts} language={language} readiness={readiness} />
        <ReadinessCenter
          language={language}
          loadingArtifact={loadingArtifact}
          onLoadArtifact={loadStepArtifact}
          onRetry={retryStep}
          readiness={readiness}
          retryingStep={busyAction.startsWith('retry:') ? busyAction.slice(6) : ''}
        />

        <section className="project-home-section project-capabilities" aria-labelledby="project-capabilities-title">
          <div className="projects-section-heading">
            <h2 id="project-capabilities-title">{t('readiness.capabilities.title')}</h2>
          </div>
          <ul>{capabilityList.map(([key, labelKey]) => <li key={key}>{t(labelKey)}</li>)}</ul>
        </section>

        <RepositoryOverview artifact={inlineOverview || overviewArtifact} language={language} />
        {selectedArtifact && selectedArtifact.ref !== 'repository_overview' ? (
          <section className="project-home-section readiness-artifact-preview" aria-labelledby="readiness-artifact-preview-title">
            <div className="projects-section-heading">
              <h2 id="readiness-artifact-preview-title">{t('readiness.artifact.title')}</h2>
              <p><code>{selectedArtifact.ref}</code></p>
            </div>
            <pre>{typeof selectedArtifact.payload === 'string'
              ? selectedArtifact.payload
              : JSON.stringify(selectedArtifact.payload?.data || selectedArtifact.payload, null, 2)}</pre>
          </section>
        ) : null}
      </section>
    </main>
  );
}
