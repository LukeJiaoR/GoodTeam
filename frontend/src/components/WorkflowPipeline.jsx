import { Link } from 'react-router-dom';
import { routes } from '../lib/routing/routes';

const STATUS_ICONS = {
  done: '✓',
  active: '●',
  'review-needed': '▶',
  blocked: '⛔',
  ready: '▫',
  'not-started': '○',
};

function statusLabel(step, t) {
  const key = `work.pipeline.status.${step.status}`;
  const translated = t(key);
  return translated === key ? (step.status_label || step.status) : translated;
}

function stepHref(step, projectKey) {
  if (step.status === 'review-needed' && step.approval) {
    return routes.projectReviewItem(projectKey, step.approval);
  }
  if (step.artifact && step.artifact_path) {
    return `${routes.projectKnowledge(projectKey)}?view=artifacts&artifact=${encodeURIComponent(step.artifact)}`;
  }
  return routes.projectWork(projectKey);
}

export default function WorkflowPipeline({ projectKey, steps, t }) {
  if (!Array.isArray(steps) || steps.length === 0) return null;
  const takeoverIndex = steps.findIndex((step) => step.approval === 'takeover');
  return (
    <nav className="workflow-pipeline" aria-label={t('work.pipeline.label')}>
      <ol className="workflow-pipeline__list">
        {steps.map((step, index) => {
          const isTakeover = step.approval === 'takeover';
          const boundary = takeoverIndex === 0 && index === 1;
          const href = stepHref(step, projectKey);
          const content = (
            <>
              <span className="workflow-pipeline__icon" aria-hidden="true">{STATUS_ICONS[step.status] || '·'}</span>
              <span className="workflow-pipeline__copy">
                <strong>{isTakeover ? t('work.pipeline.takeover') : step.title}</strong>
                <small>{statusLabel(step, t)}</small>
                {step.status === 'review-needed' && step.approval
                  ? <em>{t('work.pipeline.review', { gate: step.approval.toUpperCase() })}</em>
                  : null}
              </span>
              <span className="workflow-pipeline__number" aria-hidden="true">{step.number}</span>
            </>
          );
          return (
            <li
              className={`workflow-pipeline__stage workflow-pipeline__stage--${step.status}${isTakeover ? ' workflow-pipeline__stage--takeover' : ''}${boundary ? ' workflow-pipeline__stage--boundary' : ''}`}
              data-status={step.status}
              key={step.key || index}
            >
              {href
                ? <Link to={href} className="workflow-pipeline__link">{content}</Link>
                : <span className="workflow-pipeline__static">{content}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
