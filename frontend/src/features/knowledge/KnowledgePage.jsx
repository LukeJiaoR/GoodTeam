import { useEffect, useMemo, useState } from 'react';
import { Link, useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import ProductError from '../../components/ui/ProductError';
import { routes } from '../../lib/routing/routes';
import RepositoryOverview from '../projects/RepositoryOverview';
import useProjectState from '../runs/useProjectState';
import { knowledgeApi } from './knowledgeApi';

const VIEWS = ['overview', 'artifacts', 'memory'];

function artifactEntries(data) {
  const artifacts = data?.state?.artifacts || {};
  if (Array.isArray(artifacts)) return artifacts.map((item) => [item.key || item.name, item.path || item.value]);
  return Object.entries(artifacts);
}

function OverviewView({ home, language, overview, t }) {
  const readiness = home?.readiness || {};
  const facts = home?.facts || {};
  return (
    <div className="knowledge-overview">
      <section className="knowledge-intro"><h2>{t('knowledge.overview.title')}</h2><p>{t('knowledge.overview.description')}</p>
        <dl><div><dt>{t('knowledge.overview.mode')}</dt><dd>{readiness.project_mode || '—'}</dd></div><div><dt>{t('knowledge.overview.updated')}</dt><dd>{readiness.updated_at || '—'}</dd></div><div><dt>{t('knowledge.overview.provenance')}</dt><dd>{overview ? t('knowledge.overview.persisted') : t('knowledge.overview.missing')}</dd></div></dl>
      </section>
      <section className="knowledge-confirmed-facts">
        <div><h2>{t('knowledge.overview.confirmedTitle')}</h2><p>{t('knowledge.overview.confirmedDescription')}</p></div>
        <div className="knowledge-confirmed-grid">
          <section><h3>{t('knowledge.overview.confirmedCommands')}</h3>{facts.confirmed_commands?.length ? <ul>{facts.confirmed_commands.map((command) => <li key={command}><code>{command}</code></li>)}</ul> : <p className="product-empty-copy">{t('knowledge.overview.noConfirmedCommands')}</p>}</section>
          <section><h3>{t('knowledge.overview.confirmedConstraints')}</h3>{facts.constraints?.length ? <ul>{facts.constraints.map((constraint) => <li key={constraint}>{constraint}</li>)}</ul> : <p className="product-empty-copy">{t('knowledge.overview.noConfirmedConstraints')}</p>}{facts.prohibited_operations ? <p><strong>{t('knowledge.overview.prohibited')}</strong> {facts.prohibited_operations}</p> : null}</section>
        </div>
      </section>
      {overview ? <RepositoryOverview artifact={overview} language={language} /> : <p className="product-empty-copy">{t('knowledge.overview.empty')}</p>}
    </div>
  );
}

function ArtifactsView({ data, projectKey, search, setSearch, t }) {
  const entries = artifactEntries(data);
  const selectedKey = search.get('artifact') || entries[0]?.[0] || '';
  const [content, setContent] = useState('');
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!selectedKey) { queueMicrotask(() => setContent('')); return undefined; }
    const controller = new AbortController();
    knowledgeApi.getKnowledgeArtifact(projectKey, selectedKey, { signal: controller.signal })
      .then((payload) => { setContent(payload.content || ''); setError(null); })
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught); });
    return () => controller.abort();
  }, [projectKey, selectedKey]);
  if (!entries.length) return <p className="product-empty-copy">{t('knowledge.artifacts.empty')}</p>;
  return (
    <div className="knowledge-artifacts">
      <aside><h2>{t('knowledge.artifacts.title')}</h2><ol>{entries.map(([key, path]) => <li key={key}><button className={key === selectedKey ? 'is-active' : ''} onClick={() => setSearch({ view: 'artifacts', artifact: key })}><strong>{key}</strong><small>{path}</small></button></li>)}</ol></aside>
      <article><h2>{selectedKey}</h2>{error ? <ProductError error={error} t={t} /> : <pre>{content}</pre>}</article>
    </div>
  );
}

function MemoryView({ projectKey, search, setSearch, t }) {
  const [memory, setMemory] = useState(null);
  const [events, setEvents] = useState([]);
  const [projects, setProjects] = useState([]);
  const [eventDetail, setEventDetail] = useState(null);
  const [crossResults, setCrossResults] = useState([]);
  const [crossBusy, setCrossBusy] = useState(false);
  const [error, setError] = useState(null);
  const selected = search.get('role') || memory?.roles?.[0]?.role || '';
  const query = search.get('memory_q') || '';
  const eventId = search.get('event') || '';
  const crossQuery = search.get('cross_q') || '';
  const scope = (search.get('scope') || '').split(',').filter(Boolean);
  const updateSearch = (updates) => {
    const next = new URLSearchParams(search);
    next.set('view', 'memory');
    Object.entries(updates).forEach(([key, value]) => {
      if (value) next.set(key, value); else next.delete(key);
    });
    setSearch(next);
  };
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      knowledgeApi.getMemory(projectKey, { signal: controller.signal }),
      knowledgeApi.getKnowledgeProjects({ signal: controller.signal }),
    ]).then(([payload, projectPayload]) => {
      setMemory(payload);
      setProjects((projectPayload.projects || []).map((item) => item.project?.project_key).filter(Boolean));
    }).catch((caught) => { if (caught.name !== 'AbortError') setError(caught); });
    return () => controller.abort();
  }, [projectKey]);
  useEffect(() => {
    if (!selected) return undefined;
    const controller = new AbortController();
    knowledgeApi.getMemoryEvents(projectKey, selected, query, { signal: controller.signal }).then((payload) => setEvents(payload.events || []))
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught); });
    return () => controller.abort();
  }, [projectKey, query, selected]);
  useEffect(() => {
    if (!eventId) { queueMicrotask(() => setEventDetail(null)); return undefined; }
    const controller = new AbortController();
    knowledgeApi.getMemoryEvent(projectKey, eventId, { signal: controller.signal }).then(setEventDetail)
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught); });
    return () => controller.abort();
  }, [eventId, projectKey]);
  const runCrossSearch = async (event) => {
    event.preventDefault(); setCrossBusy(true); setError(null);
    try { const payload = await knowledgeApi.getCrossProjectMemory(selected, crossQuery, scope); setCrossResults(payload.results || []); }
    catch (caught) { setError(caught); }
    finally { setCrossBusy(false); }
  };
  const toggleScope = (key) => updateSearch({ scope: (scope.includes(key) ? scope.filter((item) => item !== key) : [...scope, key]).join(',') });
  if (error) return <ProductError error={error} t={t} />;
  if (!memory?.roles?.length) return <p className="product-empty-copy">{t('knowledge.memory.empty')}</p>;
  return (
    <div className="knowledge-memory">
      <aside><h2>{t('knowledge.memory.roles')}</h2>{memory.roles.map((role) => <button className={selected === role.role ? 'is-active' : ''} key={role.role} onClick={() => updateSearch({ role: role.role, event: '' })}><strong>{role.role}</strong><small>{t('knowledge.memory.events', { count: role.event_count })}</small></button>)}</aside>
      <section><label><span>{t('knowledge.memory.search')}</span><input type="search" value={query} onChange={(event) => updateSearch({ memory_q: event.target.value, event: '' })} /></label>
        <ol>{events.map((memoryEvent, index) => <li key={memoryEvent.id || index}><button className={eventId === memoryEvent.id ? 'is-active' : ''} onClick={() => updateSearch({ event: memoryEvent.id })}><strong>{memoryEvent.action || memoryEvent.horizon || selected}</strong><span>{memoryEvent.text || memoryEvent.summary}</span><code>{memoryEvent.id}</code></button></li>)}</ol>
        {eventDetail ? <aside className="knowledge-memory-inspector"><h3>{t('knowledge.memory.inspector')}</h3><pre>{JSON.stringify(eventDetail, null, 2)}</pre></aside> : null}
        <form className="knowledge-cross-memory" onSubmit={runCrossSearch}><h2>{t('knowledge.memory.crossTitle')}</h2><p>{t('knowledge.memory.crossDescription')}</p><fieldset><legend>{t('knowledge.memory.scope')}</legend>{projects.filter((key) => key !== projectKey).map((key) => <label key={key}><input checked={scope.includes(key)} onChange={() => toggleScope(key)} type="checkbox" />{key}</label>)}</fieldset><label><span>{t('knowledge.memory.crossQuery')}</span><input required type="search" value={crossQuery} onChange={(event) => updateSearch({ cross_q: event.target.value })} /></label><button className="product-button" disabled={!selected || !crossQuery.trim() || !scope.length || crossBusy} type="submit">{crossBusy ? t('product.states.loading') : t('knowledge.memory.crossSearch')}</button>{crossResults.length ? <ol>{crossResults.map((result, index) => <li key={result.id || index}><strong>{result.project_key || result.project || '—'}</strong><p>{result.text || result.summary || result.content}</p></li>)}</ol> : null}</form>
      </section>
    </div>
  );
}

export default function KnowledgePage() {
  const { projectKey = '' } = useParams();
  const { language, t } = useOutletContext();
  const [search, setSearch] = useSearchParams();
  const view = VIEWS.includes(search.get('view')) ? search.get('view') : 'overview';
  const api = useMemo(() => ({ getProjectState: knowledgeApi.getKnowledgeState }), []);
  const { data, error, refresh, status } = useProjectState(projectKey, { api, poll: false });
  const [home, setHome] = useState(null);
  const [overview, setOverview] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    knowledgeApi.getKnowledgeHome(projectKey, { signal: controller.signal }).then((payload) => {
      setHome(payload);
      const artifact = payload?.readiness?.artifacts?.repository_overview || payload?.readiness?.artifacts?.repository_understanding;
      if (artifact) setOverview(artifact);
      else if (payload?.readiness?.steps?.some((step) => step.id === 'repository_understanding' && step.status === 'complete')) {
        knowledgeApi.getRepositoryUnderstanding(projectKey, { signal: controller.signal }).then(setOverview).catch(() => {});
      }
    }).catch(() => {});
    return () => controller.abort();
  }, [projectKey]);
  if (status === 'loading') return <main id="main-content" className="product-page"><div className="product-workspace-skeleton" role="status"><span /><span /><span /></div></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  return (
    <main id="main-content" className="product-page product-knowledge-page">
      <header className="product-page-header"><div><p className="product-eyebrow">{data?.start_context?.project_name || projectKey}</p><h1>{t('knowledge.title')}</h1><p>{t('knowledge.description')}</p></div><Link to={routes.projectHome(projectKey)}>{t('knowledge.backHome')}</Link></header>
      <nav className="product-subnav" aria-label={t('knowledge.views.label')}>{VIEWS.map((item) => <button className={view === item ? 'is-active' : ''} key={item} onClick={() => setSearch({ view: item })}>{t(`knowledge.views.${item}`)}</button>)}</nav>
      {view === 'overview' ? <OverviewView home={home} language={language} overview={overview} t={t} /> : null}
      {view === 'artifacts' ? <ArtifactsView data={data} projectKey={projectKey} search={search} setSearch={setSearch} t={t} /> : null}
      {view === 'memory' ? <MemoryView projectKey={projectKey} search={search} setSearch={setSearch} t={t} /> : null}
    </main>
  );
}
