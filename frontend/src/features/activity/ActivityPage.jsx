import { useEffect, useMemo, useState } from 'react';
import { Link, useOutletContext, useSearchParams } from 'react-router-dom';
import ProductError from '../../components/ui/ProductError';
import { getProjectProjections } from '../../lib/api/globalApi';
import { routes } from '../../lib/routing/routes';

function eventTime(event) { return event.timestamp || event.created_at || event.updated_at || ''; }
function eventType(event) { return event.action || event.type || event.kind || 'system'; }
const EVENT_CATEGORIES = ['operation', 'state', 'approval', 'worker', 'ci', 'system'];
function eventCategory(event) {
  const value = eventType(event).toLowerCase();
  if (/(approval|decision|gate|review)/.test(value)) return 'approval';
  if (/(worker|reviewer|agent|dispatch|job)/.test(value)) return 'worker';
  if (/(ci|smoke|test|qa|deploy|staging|release)/.test(value)) return 'ci';
  if (/(state|node|transition|completed|terminated|archived|restarted)/.test(value)) return 'state';
  if (/(workflow|artifact|message|question|execution)/.test(value)) return 'operation';
  return 'system';
}
function eventTimestamp(event) {
  const timestamp = Date.parse(eventTime(event));
  return Number.isFinite(timestamp) ? timestamp : null;
}

export default function ActivityPage() {
  const { language, t } = useOutletContext();
  const [search, setSearch] = useSearchParams();
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const [partialCount, setPartialCount] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getProjectProjections({ signal: controller.signal }).then((payload) => {
      setEvents(payload.flatMap(({ item, state }) => (state?.action_items || []).map((event, index) => ({ ...event, project_key: item.project.project_key, _id: event.id || `${item.project.project_key}-${index}` }))).sort((a, b) => eventTime(b).localeCompare(eventTime(a))));
      setPartialCount(payload.filter((projection) => projection.error).length);
      setStatus('ready');
    }).catch((caught) => { if (caught.name !== 'AbortError') { setError(caught); setStatus('error'); } });
    return () => controller.abort();
  }, []);
  const project = search.get('project') || '';
  const type = search.get('type') || '';
  const run = search.get('run') || '';
  const after = search.get('after') || '';
  const before = search.get('before') || '';
  const filtered = useMemo(() => {
    const afterTime = after ? Date.parse(`${after}T00:00:00`) : null;
    const beforeTime = before ? Date.parse(`${before}T23:59:59.999`) : null;
    return events.filter((event) => {
      const timestamp = eventTimestamp(event);
      return (!project || event.project_key === project)
        && (!type || eventCategory(event) === type || eventType(event) === type)
        && (!run || event.run_id === run)
        && (!after || (timestamp !== null && timestamp >= afterTime))
        && (!before || (timestamp !== null && timestamp <= beforeTime));
    });
  }, [after, before, events, project, run, type]);
  const update = (key, value) => { const next = new URLSearchParams(search); if (value) next.set(key, value); else next.delete(key); setSearch(next); };
  const projects = [...new Set(events.map((event) => event.project_key))];
  const categories = EVENT_CATEGORIES.filter((category) => events.some((event) => eventCategory(event) === category));
  return (
    <main id="main-content" className="product-page">
      <header className="product-page-header"><div><h1>{t('activity.title')}</h1><p>{t('activity.description')}</p></div></header>
      <form className="activity-filters"><label><span>{t('activity.filters.project')}</span><select value={project} onChange={(event) => update('project', event.target.value)}><option value="">{t('activity.filters.all')}</option>{projects.map((value) => <option key={value}>{value}</option>)}</select></label><label><span>{t('activity.filters.type')}</span><select value={type} onChange={(event) => update('type', event.target.value)}><option value="">{t('activity.filters.all')}</option>{categories.map((value) => <option key={value} value={value}>{t(`activity.categories.${value}`)}</option>)}</select></label><label><span>{t('activity.filters.run')}</span><input value={run} onChange={(event) => update('run', event.target.value)} /></label><label><span>{t('activity.filters.after')}</span><input type="date" value={after} onChange={(event) => update('after', event.target.value)} /></label><label><span>{t('activity.filters.before')}</span><input type="date" value={before} onChange={(event) => update('before', event.target.value)} /></label></form>
      {status === 'loading' ? <div className="product-workspace-skeleton" role="status"><span /><span /><span /></div> : null}
      {status === 'error' ? <ProductError error={error} t={t} /> : null}
      {status === 'ready' && partialCount ? <section className="product-partial-state" role="status"><strong>{t('product.states.partial')}</strong><p>{t('product.states.partialProjects', { count: partialCount })}</p></section> : null}
      {status === 'ready' && !filtered.length ? <p className="product-empty-copy">{t('activity.empty')}</p> : null}
      <ol className="activity-timeline">{filtered.map((event) => <li key={event._id}><time>{eventTimestamp(event) !== null ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(eventTimestamp(event))) : '—'}</time><div><span className="product-eyebrow">{event.project_key} · {t(`activity.categories.${eventCategory(event)}`)} · {eventType(event)}</span><h2>{event.title || event.action || event.type}</h2><p>{event.description || event.note || event.message || event.after_node || ''}</p><Link to={event.approval ? routes.projectReviewItem(event.project_key, event.approval) : routes.projectWork(event.project_key)}>{t('activity.open')}</Link></div></li>)}</ol>
    </main>
  );
}
