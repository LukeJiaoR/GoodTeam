import Button from '../../components/ui/Button';
import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';
import {
  canRetryStep,
  errorMessage,
  recoveryIds,
  recoveryLabel,
} from './readinessPresentation';

function stepLabel(language, id) {
  return translate(language, `readiness.steps.${id}`);
}

function statusLabel(language, status) {
  return translate(language, `readiness.status.${status || 'pending'}`);
}

function statusTone(status) {
  if (status === 'complete') return 'success';
  if (status === 'needs_input') return 'warning';
  if (status === 'failed') return 'danger';
  if (status === 'running') return 'active';
  return 'pending';
}

function errorDetails(error) {
  if (!error) return null;
  if (typeof error === 'string') return { message: error };
  return error;
}

function formatTime(value, language) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat(language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
}

export default function ReadinessCenter({
  language = 'en',
  loadingArtifact = '',
  onLoadArtifact,
  onRetry,
  readiness = {},
  retryingStep = '',
}) {
  const t = (key, params) => translate(language, key, params);
  const steps = Array.isArray(readiness.steps) ? readiness.steps : [];
  const execution = readiness.execution || {};
  const currentStepId = execution.current_step_id || '';
  const completeCount = steps.filter((step) => step?.status === 'complete').length;

  return (
    <section className="project-home-section readiness-center" aria-labelledby="readiness-steps-title">
      <div className="projects-section-heading">
        <h2 id="readiness-steps-title">{t('readiness.steps.title')}</h2>
        <p>{t('readiness.progress.summary', { complete: completeCount, total: steps.length })}</p>
      </div>

      <div className="readiness-live-status" role="status" aria-live="polite" aria-atomic="true">
        <span>{statusLabel(language, execution.status || readiness.lifecycle)}</span>
        {currentStepId ? <span>{t('readiness.progress.current')}: {stepLabel(language, currentStepId)}</span> : null}
      </div>

      <ol className="readiness-step-list">
        {steps.map((step) => {
          const stepId = String(step?.id || 'unknown');
          const status = String(step?.status || 'pending');
          const isCurrent = stepId === currentStepId || status === 'running';
          const error = errorDetails(step?.error);
          const artifactRefs = Array.isArray(step?.artifact_refs) ? step.artifact_refs : [];
          const recoveries = recoveryIds(step?.recovery);
          return (
            <li
              className="readiness-step"
              data-status={status}
              key={stepId}
              aria-current={isCurrent ? 'step' : undefined}
            >
              <div className="readiness-step-main">
                <div className="readiness-step-heading">
                  <div className="readiness-step-copy">
                    <span className="readiness-step-name">{stepLabel(language, stepId)}</span>
                    {step?.required ? <span className="readiness-step-required">{t('readiness.required')}</span> : null}
                  </div>
                  <StatusBadge tone={statusTone(status)}>{statusLabel(language, status)}</StatusBadge>
                </div>

                <dl className="readiness-step-meta">
                  {Number(step?.attempt) > 0 ? (
                    <div><dt>{t('readiness.progress.attempt')}</dt><dd>{step.attempt}</dd></div>
                  ) : null}
                  {step?.started_at ? (
                    <div><dt>{t('readiness.progress.started')}</dt><dd>{formatTime(step.started_at, language)}</dd></div>
                  ) : null}
                  {step?.completed_at ? (
                    <div><dt>{t('readiness.progress.completed')}</dt><dd>{formatTime(step.completed_at, language)}</dd></div>
                  ) : null}
                </dl>

                {step?.summary || step?.current_work ? (
                  <p className="readiness-step-summary">{step.current_work || step.summary}</p>
                ) : null}

                {artifactRefs.length > 0 ? (
                  <div className="readiness-step-artifacts">
                    <span>{t('readiness.progress.discovered')}</span>
                    {artifactRefs.map((ref) => (
                      <Button
                        busy={loadingArtifact === stepId}
                        key={ref}
                        onClick={() => onLoadArtifact?.(stepId, ref)}
                        variant="secondary"
                      >
                        {t('readiness.actions.viewArtifact')}: {ref}
                      </Button>
                    ))}
                  </div>
                ) : null}

                {error ? (
                  <div className="readiness-step-error" role="alert">
                    <strong>{error.code || t('readiness.error.title')}</strong>
                    <span>{errorMessage(language, error)}</span>
                    {error.request_id ? <code>{t('projects.requestId', { requestId: error.request_id })}</code> : null}
                    {recoveries.length > 0 ? (
                      <span>{t('readiness.error.recovery')}: {recoveries.map((item) => (
                        recoveryLabel(language, item)
                      )).join(', ')}</span>
                    ) : null}
                    {canRetryStep(step) ? (
                      <Button
                        busy={retryingStep === stepId}
                        onClick={() => onRetry?.(stepId)}
                        variant="secondary"
                      >
                        {t('readiness.actions.retryStep')}
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
