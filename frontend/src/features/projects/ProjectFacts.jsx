import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';

function factValue(value, language) {
  if (value === null || value === undefined || value === '') {
    return translate(language, 'readiness.facts.none');
  }
  return String(value);
}

export default function ProjectFacts({
  facts = {},
  language = 'en',
  readiness = {},
}) {
  const t = (key, params) => translate(language, key, params);
  const activeRun = facts.active_run || null;
  const workerStatus = facts.worker_status || readiness.worker_status || null;
  const detectedMode = facts.detected_mode || readiness.project_mode || 'unconfirmed';

  return (
    <section className="project-home-section project-facts" aria-labelledby="project-facts-title">
      <div className="projects-section-heading">
        <h2 id="project-facts-title">{t('readiness.facts.title')}</h2>
      </div>
      <dl className="project-fact-list">
        <div className="project-fact">
          <dt>{t('readiness.facts.root')}</dt>
          <dd><code>{factValue(facts.root, language)}</code></dd>
        </div>
        <div className="project-fact">
          <dt>{t('readiness.facts.detectedMode')}</dt>
          <dd>{t(`readiness.mode.${detectedMode}`)}</dd>
        </div>
        <div className="project-fact">
          <dt>{t('readiness.facts.activeRun')}</dt>
          <dd>
            {activeRun ? (
              <span className="project-fact-run">
                <StatusBadge tone="active">{t('product.status.active')}</StatusBadge>
                <code>{activeRun.run_id}</code>
                {activeRun.next_node ? <span>{t('readiness.facts.nextNode')}: {activeRun.next_node}</span> : null}
              </span>
            ) : factValue(null, language)}
          </dd>
        </div>
        <div className="project-fact">
          <dt>{t('readiness.facts.workerStatus')}</dt>
          <dd>{factValue(workerStatus, language)}</dd>
        </div>
      </dl>
    </section>
  );
}
