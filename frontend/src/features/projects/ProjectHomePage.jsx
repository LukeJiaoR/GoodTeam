import { useEffect, useState } from 'react';
import Button from '../../components/ui/Button';
import InlineNotice from '../../components/ui/InlineNotice';
import PageState from '../../components/ui/PageState';
import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';
import { projectApi } from './projectApi';
import ProjectFacts from './ProjectFacts';
import ReadinessCenter from './ReadinessCenter';

function isAbortError(error, signal) {
  return signal.aborted || error?.name === 'AbortError';
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

export default function ProjectHomePage({
  api: apiOverride,
  language: languageOverride,
  projectKey: projectKeyOverride,
}) {
  const api = apiOverride || projectApi;
  const language = languageOverride || 'en';
  const t = (key, params) => translate(language, key, params);
  const projectKey = projectKeyOverride || projectKeyFromPath();
  const [home, setHome] = useState(null);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const [confirmingMode, setConfirmingMode] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    api.getProjectHome(projectKey, { signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setHome(payload);
        setStatus('ready');
      })
      .catch((caught) => {
        if (!isAbortError(caught, controller.signal)) {
          setError(caught);
          setStatus('error');
        }
      });
    return () => controller.abort();
  }, [api, projectKey]);

  const readiness = home?.readiness || {};
  const capabilities = home?.capabilities || {};
  const nextAction = home?.next_action || {};
  const lifecycle = readiness.lifecycle || 'legacy';
  const capabilityList = capabilityEntries(capabilities);
  const startReason = capabilities.start_workflow_reason
    ? t(`readiness.start.reason.${capabilities.start_workflow_reason}`)
    : '';

  const confirmMode = async (mode) => {
    setConfirmingMode(mode);
    setError(null);
    try {
      const payload = await api.confirmProjectMode(projectKey, mode);
      setHome(payload);
      setStatus('ready');
    } catch (caught) {
      setError(caught);
    } finally {
      setConfirmingMode('');
    }
  };

  if (status === 'loading') {
    return (
      <main className="project-home-page">
        <PageState kind="loading" title={t('projects.home.loading')} />
      </main>
    );
  }

  if (status === 'error') {
    return (
      <main className="project-home-page">
        <PageState
          kind="error"
          title={t('projects.home.error')}
          description={error?.code ? t(`projects.errors.${error.code}`) : t('product.errors.generic')}
        />
      </main>
    );
  }

  return (
    <main className="project-home-page">
      <header className="project-home-header">
        <div>
          <p className="project-home-eyebrow">{home.project?.project_key}</p>
          <h1>{home.project?.display_name || home.project?.project_key || t('projects.home.title')}</h1>
          <code className="project-home-root">{home.project?.root}</code>
        </div>
        <StatusBadge tone={lifecycle === 'attention_required' ? 'warning' : 'pending'}>
          {t(`readiness.lifecycle.${lifecycle}`)}
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
                <Button
                  busy={confirmingMode === 'new_project'}
                  onClick={() => confirmMode('new_project')}
                  variant="primary"
                >
                  {t('readiness.modeDecision.newProject')}
                </Button>
                <Button
                  busy={confirmingMode === 'existing_project'}
                  onClick={() => confirmMode('existing_project')}
                  variant="secondary"
                >
                  {t('readiness.modeDecision.existingProject')}
                </Button>
                <Button variant="secondary">{t('readiness.modeDecision.decideLater')}</Button>
              </div>
            </div>
          ) : (
            <InlineNotice title={t(`readiness.steps.${nextAction.id}`)} tone={nextAction.status === 'complete' ? 'success' : 'warning'}>
              {t(`readiness.status.${nextAction.status || 'pending'}`)}
            </InlineNotice>
          )}
          {error ? (
            <InlineNotice className="project-home-error" title={t('product.errors.generic')} tone="danger">
              {error?.code ? t(`projects.errors.${error.code}`) : error.message}
            </InlineNotice>
          ) : null}
          <div className="workflow-start-panel">
            <Button disabled={!capabilities.start_workflow} variant="primary">
              {t('readiness.start.label')}
            </Button>
            {!capabilities.start_workflow && startReason ? <p>{startReason}</p> : null}
          </div>
        </section>

        <ProjectFacts facts={home.facts} language={language} readiness={readiness} />
        <ReadinessCenter language={language} readiness={readiness} />

        <section className="project-home-section project-capabilities" aria-labelledby="project-capabilities-title">
          <div className="projects-section-heading">
            <h2 id="project-capabilities-title">{t('readiness.capabilities.title')}</h2>
          </div>
          <ul>
            {capabilityList.map(([key, labelKey]) => (
              <li key={key}>{t(labelKey)}</li>
            ))}
          </ul>
        </section>
      </section>
    </main>
  );
}
