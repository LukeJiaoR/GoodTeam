import { ArrowRight, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../../components/ui/Button';
import StatusBadge from '../../components/ui/StatusBadge';
import { ATTENTION_KINDS, attentionHref, attentionOverview, attentionReason, attentionTitle } from './projectAttention';
import './projectAttention.css';

export default function ProjectAttentionOverview({ projects, status, onRefresh, t }) {
  const { counts, items, unavailable } = attentionOverview(projects);
  const current = status === 'ready';
  const loading = ['loading', 'refreshing'].includes(status);
  return <section className="project-attention" aria-labelledby="project-attention-title" aria-busy={loading}>
    <header className="projects-launcher__heading">
      <div><h2 id="project-attention-title">{t('projects.attention.title')}</h2>
        <p>{t('projects.attention.scope')}</p></div>
      <Button variant="secondary" busy={loading} onClick={onRefresh}>
        <RefreshCw size={15} aria-hidden="true" />{t('projects.attention.refresh')}
      </Button>
    </header>
    <div className="project-attention__body">
      {!current ? <p role="status">{t(`projects.attention.${status}`)}</p> : null}
      {current && unavailable.length ? <p role="status">{t('projects.attention.unavailable', {
        projects: unavailable.map(project => project.display_name).join(', '),
      })}</p> : null}
      {current ? <>
        <dl className="project-attention__counts" aria-label={t('projects.attention.counts')}>
          {ATTENTION_KINDS.map(kind => <div key={kind} data-kind={kind}>
            <dt>{t(`projects.attention.kind.${kind}`)}</dt><dd>{counts[kind]}</dd>
          </div>)}
          <div><dt>{t(unavailable.length ? 'projects.attention.knownTotal' : 'projects.attention.total')}</dt><dd>{counts.total}</dd></div>
        </dl>
        {!items.length && !unavailable.length ? <p role="status">{t('projects.attention.empty')}</p> : null}
        {items.length ? <ol className="project-attention__items">
          {items.map(item => <li key={JSON.stringify([item.project.project_key, item.id])}>
            <Link className="project-attention__link" to={attentionHref(item.project.project_key, item)}>
              <StatusBadge tone={item.kind === 'blocked' ? 'danger' : 'active'}>{t(`projects.attention.kind.${item.kind}`)}</StatusBadge>
              <span className="project-attention__copy"><strong>{item.project.display_name}</strong>
                <span>{attentionTitle(item, t)}</span><small>{attentionReason(item, t)}</small>
                <small>{t(`projects.attention.source.${item.source}`)} · {item.target_id}</small>
              </span><ArrowRight size={17} aria-hidden="true" />
            </Link>
          </li>)}
        </ol> : null}
      </> : null}
    </div>
  </section>;
}
