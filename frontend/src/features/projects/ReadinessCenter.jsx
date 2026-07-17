import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';

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
  return 'pending';
}

export default function ReadinessCenter({ language = 'en', readiness = {} }) {
  const t = (key, params) => translate(language, key, params);
  const steps = Array.isArray(readiness.steps) ? readiness.steps : [];

  return (
    <section className="project-home-section readiness-center" aria-labelledby="readiness-steps-title">
      <div className="projects-section-heading">
        <h2 id="readiness-steps-title">{t('readiness.steps.title')}</h2>
        <p>{t('readiness.description')}</p>
      </div>
      <ol className="readiness-step-list">
        {steps.map((step) => {
          const stepId = String(step?.id || 'unknown');
          const status = String(step?.status || 'pending');
          return (
            <li className="readiness-step" data-status={status} key={stepId}>
              <div className="readiness-step-copy">
                <span className="readiness-step-name">{stepLabel(language, stepId)}</span>
                {step?.required ? <span className="readiness-step-required">{t('readiness.required')}</span> : null}
              </div>
              <StatusBadge tone={statusTone(status)}>{statusLabel(language, status)}</StatusBadge>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
