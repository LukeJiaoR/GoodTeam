import { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpenCheck, BrainCircuit, Files, Search, ShieldCheck, TerminalSquare } from 'lucide-react';
import { useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import KnowledgeArtifacts from './KnowledgeArtifacts';
import MemoryEventInspector from './MemoryEventInspector';
import { isMemoryEventId } from './memoryProvenance';
import ProductError from '../../components/ui/ProductError';
import RepositoryOverview from '../projects/RepositoryOverview';
import useProjectState from '../runs/useProjectState';
import { knowledgeApi } from './knowledgeApi';

function updatedLabel(value, language) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

const VIEWS = [
  ['overview', BookOpenCheck],
  ['artifacts', Files],
  ['memory', BrainCircuit],
];

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
      <section className="knowledge-intro">
        <header><div><p className="product-eyebrow">{t('knowledge.workspace.context')}</p><h2>{t('knowledge.overview.title')}</h2><p>{t('knowledge.overview.description')}</p></div><span aria-hidden="true"><BookOpenCheck size={21} /></span></header>
        <dl><div><dt>{t('knowledge.overview.mode')}</dt><dd>{readiness.project_mode === 'existing_project' ? t('assistant.existingProject') : readiness.project_mode === 'new_project' ? t('assistant.newProject') : readiness.project_mode || '—'}</dd></div><div><dt>{t('knowledge.overview.updated')}</dt><dd>{updatedLabel(readiness.updated_at, language)}</dd></div><div><dt>{t('knowledge.overview.provenance')}</dt><dd>{overview ? t('knowledge.overview.persisted') : t('knowledge.overview.missing')}</dd></div></dl>
      </section>
      <section className="knowledge-confirmed-facts">
        <header><span aria-hidden="true"><ShieldCheck size={19} /></span><div><h2>{t('knowledge.overview.confirmedTitle')}</h2><p>{t('knowledge.overview.confirmedDescription')}</p></div></header>
        <div className="knowledge-confirmed-grid">
          <section><h3><TerminalSquare aria-hidden="true" size={16} />{t('knowledge.overview.confirmedCommands')}</h3>{facts.confirmed_commands?.length ? <ul>{facts.confirmed_commands.map((command) => <li key={command}><code>{command}</code></li>)}</ul> : <p className="product-empty-copy">{t('knowledge.overview.noConfirmedCommands')}</p>}</section>
          <section><h3>{t('knowledge.overview.confirmedConstraints')}</h3>{facts.constraints?.length ? <ul>{facts.constraints.map((constraint) => <li key={constraint}>{constraint}</li>)}</ul> : <p className="product-empty-copy">{t('knowledge.overview.noConfirmedConstraints')}</p>}{facts.prohibited_operations ? <p><strong>{t('knowledge.overview.prohibited')}</strong> {facts.prohibited_operations}</p> : null}</section>
        </div>
      </section>
      {overview ? <RepositoryOverview artifact={overview} language={language} /> : <p className="product-empty-copy">{t('knowledge.overview.empty')}</p>}
    </div>
  );
}

function MemoryView({ projectKey, search, setSearch, t }) {
  const [memory, setMemory] = useState(null);
  const [eventResponse, setEventResponse] = useState(null);
  const [projects, setProjects] = useState([]);
  const [detailResponse, setDetailResponse] = useState(null);
  const [crossResponse, setCrossResponse] = useState(null);
  const crossRequest = useRef(null);
  const [queryDraft, setQueryDraft] = useState({ base: search.get('memory_q') || '', text: search.get('memory_q') || '' });
  const queryTimer = useRef(null);
  const inspectorRef = useRef(null);
  const [readErrors, setReadErrors] = useState({ overview: null, events: null, detail: null, cross: null });
  const [readRevision, setReadRevision] = useState(0);
  const returnFocus = useRef(null);
  const searchInput = useRef(null);
  const selected = search.get('role') || memory?.roles?.[0]?.role || '';
  const query = search.get('memory_q') || '';
  const draftQuery = queryDraft.base === query ? queryDraft.text : query;
  const eventId = search.get('event') || '';
  const crossQuery = search.get('cross_q') || '';
  const scope = (search.get('scope') || '').split(',').filter(Boolean);
  const eventProject = search.get('event_project') || projectKey;
  const sourceAllowed = eventProject === projectKey || (scope.includes(eventProject) && projects.includes(eventProject));
  const validEvent = isMemoryEventId(eventId);
  const eventsKey = JSON.stringify(['events', projectKey, selected, query, readRevision]);
  const detailKey = JSON.stringify(['detail', projectKey, eventProject, selected, eventId, readRevision]);
  const crossKey = JSON.stringify(['cross', projectKey, selected, crossQuery, scope]);
  const eventsReady = eventResponse?.key === eventsKey;
  const events = eventsReady ? eventResponse.events : [];
  const eventDetail = eventId && sourceAllowed && detailResponse?.key === detailKey ? detailResponse.event : null;
  const currentCross = crossResponse?.key === crossKey ? crossResponse : null;
  const crossResults = currentCross?.results || [];
  const crossBusy = currentCross?.status === 'loading' && !currentCross.signal.aborted;
  const crossSearched = currentCross?.status === 'complete';
  const error = Object.values(readErrors).find(item => item && !item.signal.aborted
    && [projectKey, eventsKey, detailKey, crossKey].includes(item.key));
  const currentError = error?.value || null;
  const visibleError = currentError?.code === 'memory_identity_mismatch'
    ? { ...currentError, message: t('knowledge.memory.identityMismatch') } : currentError;
  const roleContext = memory?.roles?.find(item => item.role === selected);
  const updateSearch = (updates) => {
    const next = new URLSearchParams(search);
    next.set('view', 'memory');
    Object.entries(updates).forEach(([key, value]) => {
      if (value) next.set(key, value); else next.delete(key);
    });
    setSearch(next);
  };
  const openEvent = (id, source = projectKey, trigger = null) => {
    if (!isMemoryEventId(id) || (source !== projectKey && (!scope.includes(source) || !projects.includes(source)))) return;
    if (trigger) returnFocus.current = trigger;
    globalThis.clearTimeout(queryTimer.current);
    setQueryDraft({ base: draftQuery, text: draftQuery });
    updateSearch({ memory_q: draftQuery, event: id, event_project: source === projectKey ? '' : source });
  };
  const closeSource = () => {
    updateSearch({ event: '', event_project: '' });
    requestAnimationFrame(() => {
      if (returnFocus.current?.isConnected) returnFocus.current.focus();
      else searchInput.current?.focus();
    });
  };
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      knowledgeApi.getMemory(projectKey, { signal: controller.signal }),
      knowledgeApi.getKnowledgeProjects({ signal: controller.signal }),
    ]).then(([payload, projectPayload]) => {
      if (controller.signal.aborted) return;
      setMemory(payload);
      setReadErrors(current => ({ ...current, overview: null }));
      setProjects((projectPayload.projects || []).map((item) => item.project?.project_key).filter(Boolean));
    }).catch((caught) => { if (!controller.signal.aborted && caught.name !== 'AbortError') setReadErrors(current => ({ ...current, overview: { key: projectKey, value: caught, signal: controller.signal } })); });
    return () => controller.abort();
  }, [projectKey, readRevision]);
  useEffect(() => {
    queryTimer.current = globalThis.setTimeout(() => {
      if (draftQuery !== query) {
        setQueryDraft({ base: draftQuery, text: draftQuery });
        updateSearch({ memory_q: draftQuery, event: '', event_project: '' });
      }
    }, 250);
    return () => globalThis.clearTimeout(queryTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftQuery, query, search]);
  useEffect(() => {
    if (!selected) return undefined;
    const controller = new AbortController();
    knowledgeApi.getMemoryEvents(projectKey, selected, query, { signal: controller.signal }).then((payload) => {
      if (controller.signal.aborted) return;
      setEventResponse({ key: eventsKey, events: payload.events || [] });
      setReadErrors(current => ({ ...current, events: null }));
    }).catch((caught) => { if (!controller.signal.aborted && caught.name !== 'AbortError') setReadErrors(current => ({ ...current, events: { key: eventsKey, value: caught, signal: controller.signal } })); });
    return () => controller.abort();
  }, [projectKey, query, selected, eventsKey]);
  useEffect(() => {
    if (!eventId || !validEvent || !selected || !sourceAllowed) return undefined;
    const controller = new AbortController();
    knowledgeApi.getMemoryEvent(eventProject, eventId, { signal: controller.signal }).then((event) => {
      if (controller.signal.aborted) return;
      if (event?.id !== eventId || event.role !== selected
          || (event.project_key && event.project_key !== eventProject)) {
        throw Object.assign(new Error('Memory source does not match the selected event and role.'), { code: 'memory_identity_mismatch' });
      }
      setDetailResponse({ key: detailKey, event });
      setReadErrors(current => ({ ...current, detail: null }));
    }).catch((caught) => { if (!controller.signal.aborted && caught.name !== 'AbortError') setReadErrors(current => ({ ...current, detail: { key: detailKey, value: caught, signal: controller.signal } })); });
    return () => controller.abort();
  }, [eventId, eventProject, selected, validEvent, sourceAllowed, detailKey]);
  useEffect(() => () => {
    if (crossRequest.current?.key === crossKey) crossRequest.current.controller.abort();
  }, [crossKey]);
  const runCrossSearch = async (event) => {
    event?.preventDefault();
    if (!selected || !crossQuery.trim() || !scope.length) return;
    crossRequest.current?.controller.abort();
    const request = { key: crossKey, controller: new AbortController() };
    crossRequest.current = request;
    setCrossResponse({ key: crossKey, status: 'loading', results: [], signal: request.controller.signal });
    setReadErrors(current => ({ ...current, cross: null }));
    try {
      const payload = await knowledgeApi.getCrossProjectMemory(selected, crossQuery, scope, { signal: request.controller.signal });
      if (!request.controller.signal.aborted && crossRequest.current === request) {
        setCrossResponse({ key: crossKey, status: 'complete', results: payload.results || [] });
      }
    } catch (caught) {
      if (!request.controller.signal.aborted && crossRequest.current === request) {
        setCrossResponse({ key: crossKey, status: 'error', results: [] });
        setReadErrors(current => ({ ...current, cross: { key: crossKey, value: caught, signal: request.controller.signal } }));
      }
    }
  };
  useEffect(() => {
    if (!eventDetail) return;
    inspectorRef.current?.scrollIntoView?.({ block: 'nearest' });
    inspectorRef.current?.focus?.({ preventScroll: true });
  }, [eventDetail]);
  const retryRead = () => {
    if (error?.key === crossKey) void runCrossSearch();
    else setReadRevision(value => value + 1);
  };
  const toggleScope = (key) => updateSearch({ scope: (scope.includes(key) ? scope.filter((item) => item !== key) : [...scope, key]).join(',') });
  if (!memory) return visibleError ? <ProductError error={visibleError} onRetry={retryRead} t={t} />
    : <p role="status">{t('product.states.loading')}</p>;
  if (!memory.roles?.length) return visibleError ? <ProductError error={visibleError} onRetry={retryRead} t={t} />
    : <p className="product-empty-copy">{t('knowledge.memory.empty')}</p>;
  return (
    <div className="knowledge-memory">
      <aside><header><span aria-hidden="true"><BrainCircuit size={17} /></span><h2>{t('knowledge.memory.roles')}</h2></header>{memory.roles.map((role) => <button aria-pressed={selected === role.role} className={selected === role.role ? 'is-active' : ''} key={role.role} onClick={() => updateSearch({ role: role.role, event: '', event_project: '' })}><strong>{role.role}</strong><small>{t('knowledge.memory.events', { count: role.event_count })}</small></button>)}</aside>
      <section className="knowledge-memory-workspace"><label className="knowledge-memory-search"><span>{t('knowledge.memory.search')}</span><div><Search aria-hidden="true" size={16} /><input ref={searchInput} type="search" value={draftQuery} onChange={(event) => setQueryDraft({ base: query, text: event.target.value })} /></div></label>
        {visibleError ? <ProductError error={visibleError} onRetry={retryRead} t={t} /> : null}
        <p className="memory-provenance__notice">{t('knowledge.memory.storageBoundary')}</p>
        {roleContext?.context_status && roleContext.context_status !== 'available' ? <p role="status" className="memory-provenance__notice">{t('knowledge.memory.contextUnavailable')} <code>{roleContext.context_status}</code>{(roleContext.context_reasons || []).map(reason => <code key={reason}> · {reason}</code>)}</p> : null}
        {eventsReady && !events.length ? <p className="product-empty-copy">{t('knowledge.memory.emptyResults')}</p> : null}
        <ol>{events.map((memoryEvent, index) => <li key={memoryEvent.id || index}><button aria-pressed={eventProject === projectKey && eventId === memoryEvent.id} className={eventProject === projectKey && eventId === memoryEvent.id ? 'is-active' : ''} onClick={(click) => openEvent(memoryEvent.id, projectKey, click.currentTarget)}><strong>{memoryEvent.action || memoryEvent.horizon || selected}</strong><span>{memoryEvent.body ?? memoryEvent.text ?? memoryEvent.summary ?? ''}</span>{memoryEvent.is_superseded ? <small>{t('knowledge.memory.superseded')}</small> : null}<code>{memoryEvent.id}</code></button></li>)}</ol>
        {eventId && (!validEvent || !sourceAllowed) ? <p role="status">{t('knowledge.memory.sourceUnavailable')} <button type="button" onClick={closeSource}>{t('knowledge.memory.closeSource')}</button></p> : null}
        {eventId && validEvent && sourceAllowed && !eventDetail && !visibleError ? <p role="status">{t('product.states.loading')}</p> : null}
        {eventDetail ? <MemoryEventInspector elementRef={inspectorRef} event={eventDetail} projectKey={eventProject} onOpenRelated={id => openEvent(id, eventProject)} onClose={closeSource} t={t} /> : null}
        <form className="knowledge-cross-memory" onSubmit={runCrossSearch}><h2>{t('knowledge.memory.crossTitle')}</h2><p>{t('knowledge.memory.crossDescription')}</p><p className="memory-provenance__notice">{t('knowledge.memory.retrievalBoundary')}</p><fieldset><legend>{t('knowledge.memory.scope')}</legend>{projects.filter((key) => key !== projectKey).map((key) => <label key={key}><input checked={scope.includes(key)} onChange={() => toggleScope(key)} type="checkbox" />{key}</label>)}</fieldset><label><span>{t('knowledge.memory.crossQuery')}</span><input required type="search" value={crossQuery} onChange={(event) => updateSearch({ cross_q: event.target.value })} /></label>{crossQuery.trim() && !scope.length ? <p className="knowledge-scope-hint" id="knowledge-cross-scope-hint">{t('knowledge.memory.scopeHint')}</p> : null}<button className="product-button" aria-describedby={crossQuery.trim() && !scope.length ? 'knowledge-cross-scope-hint' : undefined} disabled={!selected || !crossQuery.trim() || !scope.length || crossBusy} type="submit">{crossBusy ? t('product.states.loading') : t('knowledge.memory.crossSearch')}</button>{crossResults.length ? <ol>{crossResults.map((result, index) => <li key={result.id || index}><strong>{result.project_key || result.project || '—'}</strong><p>{result.text || result.summary || result.content}</p><div className="memory-provenance__hit-source"><span>{result.source || t('knowledge.memory.unknown')}</span>{result.timestamp ? <span>{result.timestamp}</span> : null}{isMemoryEventId(result.mem_id) && result.id === `${result.project_key}:${result.mem_id}` && scope.includes(result.project_key) && projects.includes(result.project_key) ? <button type="button" className="product-button product-button--secondary" onClick={click => openEvent(result.mem_id, result.project_key, click.currentTarget)}>{t('knowledge.memory.openSource')}</button> : <span>{t('knowledge.memory.sourceUnavailable')}</span>}</div></li>)}</ol> : crossSearched ? <p className="product-empty-copy">{t('knowledge.memory.crossEmpty')}</p> : null}</form>
      </section>
    </div>
  );
}

export default function KnowledgePage() {
  const { projectKey = '' } = useParams();
  const { language, t } = useOutletContext();
  const [search, setSearch] = useSearchParams();
  const view = VIEWS.some(([key]) => key === search.get('view')) ? search.get('view') : 'overview';
  const api = useMemo(() => ({ getProjectState: knowledgeApi.getKnowledgeState }), []);
  const { data, error, refresh, status } = useProjectState(projectKey, { api, poll: false });
  const [home, setHome] = useState(null);
  const [overview, setOverview] = useState(null);
  const [overviewError, setOverviewError] = useState(null);
  const [overviewRequest, setOverviewRequest] = useState(0);
  const artifactCount = artifactEntries(data).length;
  useEffect(() => {
    const controller = new AbortController();
    async function loadOverview() {
      try {
        const payload = await knowledgeApi.getKnowledgeHome(projectKey, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setHome(payload);
        const artifact = payload?.readiness?.artifacts?.repository_overview || payload?.readiness?.artifacts?.repository_understanding;
        const content = artifact || (payload?.readiness?.steps?.some((step) => step.id === 'repository_understanding' && step.status === 'complete')
          ? await knowledgeApi.getRepositoryUnderstanding(projectKey, { signal: controller.signal }) : null);
        if (controller.signal.aborted) return;
        setOverview(content);
        setOverviewError(null);
      } catch (caught) {
        if (!controller.signal.aborted && caught.name !== 'AbortError') setOverviewError(caught);
      }
    }
    void loadOverview();
    return () => controller.abort();
  }, [projectKey, overviewRequest]);
  if (status === 'loading') return <main id="main-content" className="product-page"><div className="product-workspace-skeleton" role="status"><span /><span /><span /></div></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  return (
    <main id="main-content" className="product-page product-knowledge-page">
      <header className="knowledge-page-header"><div><p className="product-eyebrow">{data?.start_context?.project_name || projectKey}</p><h1>{t('knowledge.title')}</h1><p>{t('knowledge.description')}</p></div><div className="knowledge-page-count"><strong>{artifactCount}</strong><span>{t('knowledge.workspace.artifacts')}</span></div></header>
      <nav className="knowledge-view-switcher" aria-label={t('knowledge.views.label')}>{VIEWS.map(([item, Icon]) => <button aria-pressed={view === item} className={view === item ? 'is-active' : ''} key={item} onClick={() => { const next = new URLSearchParams(search); next.set('view', item); setSearch(next); }}><Icon aria-hidden="true" size={17} /><span>{t(`knowledge.views.${item}`)}</span></button>)}</nav>
      {status === 'stale' ? <ProductError error={error} onRetry={() => refresh()} t={t} /> : null}
      {view === 'overview' && overviewError ? <ProductError error={overviewError} onRetry={() => setOverviewRequest((value) => value + 1)} t={t} /> : null}
      {view === 'overview' && !overviewError ? <OverviewView home={home} language={language} overview={overview} t={t} /> : null}
      {view === 'artifacts' ? <KnowledgeArtifacts entries={artifactEntries(data)} projectKey={projectKey} search={search} setSearch={setSearch} t={t} /> : null}
      {view === 'memory' ? <MemoryView key={projectKey} projectKey={projectKey} search={search} setSearch={setSearch} t={t} /> : null}
    </main>
  );
}
