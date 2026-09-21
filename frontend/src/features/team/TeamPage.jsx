import { AlertTriangle, Bot, History, PackageOpen, Pause, Pencil, Play, Plus, Settings2, Trash2, UserRoundCog, X } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import CollectionToolbar from '../../components/ui/CollectionToolbar';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import useProjectState from '../runs/useProjectState';
import { settingsApi } from '../settings/settingsApi';
import './team.css';
import BatchWorkerDialog from './BatchWorkerDialog';
import RuntimeFields from './RuntimeFields';
import ResponsibilityGroup from './ResponsibilityGroup';
import SkillLibraryPanel from './SkillLibraryPanel';
import useTeamDialog from './useTeamDialog';
import { buildTeamModel, capabilityCounts, groupTeamRoles, newWorkerRuntime, rosteredWorkerCount, workerCreateCommand, workerEditCommands } from './teamModel';

const EMPTY_DRAFT = { name: '', role: '', lifecycle: '', provider: '', model: '', reasoningDepth: 'default', additionalPrompt: '' };

function newCommandKey() {
  return globalThis.crypto?.randomUUID?.() || `team-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function draftFromWorker(worker) {
  return {
    name: worker.name,
    role: worker.role,
    lifecycle: worker.lifecycle,
    provider: worker.provider,
    model: worker.model,
    reasoningDepth: worker.reasoningDepth || 'default',
    additionalPrompt: worker.additionalPrompt || '',
  };
}

function WorkerDialog({ agents, backendOptions, agentsLoading, onRefresh, defaultProvider, error, onClose, onSubmit, roleEntry, roles, manager, managerRuntime, assistantSession, saving, t, worker }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const handleKeyDown = useTeamDialog(dialogRef, closeRef, onClose);
  const editing = Boolean(worker);
  const submitting = useRef(false);
  const [verification, setVerification] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [draft, setDraft] = useState(() => {
    if (worker) return draftFromWorker(worker);
    if (manager) return { ...EMPTY_DRAFT, provider: managerRuntime?.provider || '', model: managerRuntime?.model || '', reasoningDepth: managerRuntime?.reasoningDepth || 'default', additionalPrompt: managerRuntime?.additionalPrompt || '' };
    return { ...EMPTY_DRAFT, ...newWorkerRuntime(defaultProvider), role: roleEntry?.role || '' };
  });

  const runtimeBusy = manager && (assistantSession?.runtime?.status === 'checking'
    || Object.values(assistantSession?.assistant_requests || {}).some((request) => request.status === 'running'));
  const locked = saving || Boolean(runtimeBusy);
  const runtimeMatches = manager && draft.provider === (managerRuntime?.provider || '')
    && draft.model === (managerRuntime?.model || '')
    && draft.reasoningDepth === (managerRuntime?.reasoningDepth || 'default')
    && draft.additionalPrompt.trim() === (managerRuntime?.additionalPrompt || '');
  const verificationStatus = saving ? 'checking'
    : verification?.error || submitError ? 'failed'
      : verification?.runtime?.status || (runtimeMatches ? assistantSession?.runtime?.status : 'unverified') || 'unverified';
  const runtimeError = verification?.runtime?.error || (runtimeMatches ? assistantSession?.runtime?.error : null);
  const changeDraft = (value) => {
    setDraft(value);
    setVerification(null);
    setSubmitError(null);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (locked || submitting.current) return;
    if (manager) {
      submitting.current = true;
      setVerification(null);
      setSubmitError(null);
      try {
        const result = await onSubmit([{ kind: 'change_manager_runtime', provider: draft.provider || managerRuntime?.provider || 'codex', model: draft.model.trim(), reasoning_depth: draft.reasoningDepth, additional_prompt: draft.additionalPrompt.trim() }]);
        setVerification(result);
      } catch (caught) {
        setSubmitError(caught);
      } finally {
        submitting.current = false;
      }
      return;
    }
    submitting.current = true;
    const normalized = { ...draft, model: draft.model.trim() };
    try {
      await onSubmit(editing ? workerEditCommands(worker, normalized) : [workerCreateCommand(draft.role, normalized)]);
    } finally {
      submitting.current = false;
    }
  };

  return (
    <div className="team-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <form
        ref={dialogRef}
        className="team-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={handleKeyDown}
        onSubmit={submit}
      >
        <header className="team-dialog__header">
          <div>
            <p className="team-dialog__eyebrow">{manager ? t('team.dialog.managerEyebrow') : t('team.dialog.workerEyebrow')}</p>
            <h2 id={titleId}>{manager ? t('team.dialog.managerTitle') : editing ? t('team.dialog.editTitle', { name: worker.name }) : t('team.dialog.createTitle')}</h2>
            <p>{manager ? t('team.dialog.managerDescription') : t('team.dialog.workerDescription')}</p>
          </div>
          <button ref={closeRef} className="team-icon-button" type="button" disabled={saving} onClick={onClose} aria-label={t('team.dialog.close')}>
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <fieldset className="team-dialog__fields" disabled={locked}>
          {!manager && editing ? (
            <label>
              <span>{t('team.dialog.name')}</span>
              <input aria-label={t('team.dialog.name')} maxLength="80" required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </label>
          ) : null}
          {!manager && !editing ? (
            <>
              <label>
                <span>{t('team.dialog.role')}</span>
                <select aria-label={t('team.dialog.role')} required value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })}>
                  <option value="" disabled>{t('team.dialog.selectRole')}</option>
                  {groupTeamRoles(roles || (roleEntry ? [roleEntry] : [])).map((group) => (
                    <optgroup key={group.key} label={t(`team.groups.${group.key}.title`)}>
                      {group.roles.map((entry) => <option key={entry.role} value={entry.role}>{entry.label}</option>)}
                    </optgroup>
                  ))}
                </select>
              </label>
              <label>
                <span>{t('team.dialog.name')}</span>
                <input aria-label={t('team.dialog.name')} maxLength="80" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder={t('team.dialog.namePlaceholder')} />
              </label>
            </>
          ) : null}
          <RuntimeFields draft={draft} setDraft={changeDraft} agents={agents} backendOptions={backendOptions} defaultProvider={worker?.provider || managerRuntime?.provider || defaultProvider} agentsLoading={agentsLoading} onRefresh={onRefresh} t={t} />
        </fieldset>

        {error ? <div className="team-dialog__error"><ProductError error={error} t={t} /></div> : null}

        {manager ? <section className="team-runtime-check" aria-label={t('assistant.runtime')}>
          <span role="status">{t(`assistant.status.${verificationStatus}`)}</span>
          <p>{t('team.dialog.saveAndCheckHint')}</p>
          {verification?.saved && verification?.error ? <p>{t('team.dialog.savedCheckFailed')}</p> : null}
          {runtimeError ? <p role="alert">{t(`assistant.error.${runtimeError.code}`)}</p>
            : verification?.error || submitError ? <ProductError error={verification?.error || submitError} t={t} /> : null}
        </section> : null}

        <footer className="team-dialog__footer">
          <Button variant="secondary" disabled={saving} onClick={onClose}>{t(manager && verification?.saved ? 'team.dialog.close' : 'team.dialog.cancel')}</Button>
          <Button type="submit" busy={saving} disabled={locked}>{manager ? t('team.dialog.saveAndCheck') : editing ? t('team.dialog.saveWorker') : t('team.dialog.createWorker')}</Button>
        </footer>
      </form>
    </div>
  );
}

function HistoryPanel({ onClose, events, t }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const handleKeyDown = useTeamDialog(dialogRef, closeRef, onClose);
  const actorLabels = {
    user: t('team.actor.user'),
    manager: t('team.actor.manager'),
    system: t('team.actor.system'),
    migration: t('team.actor.migration'),
  };
  const surfaceLabels = {
    manager_conversation: t('team.surface.chat'),
    team_ui: t('team.surface.teamUi'),
    api: t('team.surface.api'),
    migration: t('team.surface.migration'),
  };
  const decisions = useMemo(() => {
    const grouped = new Map();
    for (const event of events || []) {
      const key = event.decision_id || event.id;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(event);
    }
    return [...grouped.values()];
  }, [events]);

  return (
    <div className="team-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        ref={dialogRef}
        className="team-dialog team-history"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={handleKeyDown}
      >
        <header className="team-dialog__header">
          <div>
            <p className="team-dialog__eyebrow">{t('team.history.eyebrow')}</p>
            <h2 id={titleId}>{t('team.history.title')}</h2>
            <p>{t('team.history.description')}</p>
          </div>
          <button ref={closeRef} className="team-icon-button" type="button" onClick={onClose} aria-label={t('team.history.close')}>
            <X aria-hidden="true" size={18} />
          </button>
        </header>
        <div className="team-history__list">
          {decisions.length === 0 ? <p className="team-history__empty">{t('team.history.empty')}</p> : null}
          {decisions.map((group) => (
            <article className="team-history__decision" key={group[0].decision_id || group[0].id}>
              <header>
                <code>{group[0].decision_id}</code>
                <time>{group[0].timestamp}</time>
              </header>
              <ul>
                {group.map((event) => (
                  <li key={event.id}>
                    <strong>{event.event}</strong>
                    <span>{event.target?.id ? ` · ${event.target.id}` : ''}</span>
                    <em>
                      {actorLabels[event.actor] || event.actor} · {surfaceLabels[event.surface] || event.surface}
                      {event.cause === 'cascade' ? ` · ${t('team.history.cascade')}` : ''}
                    </em>
                    {event.reason ? <p>{event.reason}</p> : null}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
        <footer className="team-dialog__footer">
          <Button variant="secondary" onClick={onClose}>{t('team.history.close')}</Button>
        </footer>
      </section>
    </div>
  );
}

export default function TeamPage({ api = settingsApi }) {
  const { projectKey = '' } = useParams();
  const { t } = useOutletContext();
  const { data, error, refresh, status } = useProjectState(projectKey, { api: { getProjectState: api.getProjectSettings }, poll: true });
  const [agents, setAgents] = useState([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [agentsRevision, setAgentsRevision] = useState(0);
  const [loadError, setLoadError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [mutationError, setMutationError] = useState(null);
  const [savedNotice, setSavedNotice] = useState(false);
  const [dialog, setDialog] = useState(null); // {type, roleEntry?, worker?, roleKeys?}
  const [events, setEvents] = useState([]);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [skillsOpen, setSkillsOpen] = useState(false);
  const commandPending = useRef(false);
  const commandAttempt = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    api.getSettingsAgents({ signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted) setAgents(Array.isArray(payload?.agents) ? payload.agents : []); })
      .catch((caught) => { if (!controller.signal.aborted) { setAgents([]); setLoadError(caught); } })
      .finally(() => { if (!controller.signal.aborted) setAgentsLoading(false); });
    return () => controller.abort();
  }, [api, agentsRevision]);

  const refreshAgents = () => {
    setAgentsLoading(true);
    setAgents([]);
    setLoadError(null);
    setAgentsRevision((revision) => revision + 1);
  };

  const team = useMemo(() => buildTeamModel(data), [data]);
  const backendOptions = useMemo(() => [...new Set([
    ...(data?.start_context?.worker_backend_options || []),
    ...agents.filter((agent) => agent.available !== false).map((agent) => agent.backend),
  ])], [data, agents]);

  const runCommands = async (commands, { reason = '' } = {}) => {
    if (commandPending.current) return;
    if (!commands.length) {
      setDialog(null);
      return;
    }
    setSaving(true);
    commandPending.current = true;
    setMutationError(null);
    setSavedNotice(false);
    const signature = JSON.stringify({ projectKey, commands, reason });
    if (commandAttempt.current?.signature !== signature) {
      commandAttempt.current = { signature, key: newCommandKey() };
    }
    try {
      await api.sendTeamCommands(projectKey, commands, { reason, commandKey: commandAttempt.current.key });
      await refresh();
      setDialog(null);
      setSavedNotice(true);
      commandAttempt.current = null;
    } catch (caught) {
      setMutationError(caught);
    } finally {
      setSaving(false);
      commandPending.current = false;
    }
  };

  const saveManagerRuntime = async (commands) => {
    setSaving(true);
    setMutationError(null);
    let saved = false;
    try {
      await api.sendTeamCommands(projectKey, commands, { reason: 'Update Task Assistant runtime' });
      saved = true;
      const result = await api.taskAssistantAction(projectKey, {
        action: 'check_runtime', confirmed: true,
        message_id: globalThis.crypto?.randomUUID?.() || `check-${Date.now()}`,
      });
      await refresh();
      return { saved, runtime: result.task_assistant_session?.runtime };
    } catch (caught) {
      const latest = await refresh().catch(() => null);
      return { saved, error: caught, runtime: saved ? latest?.task_assistant_session?.runtime : null };
    } finally {
      setSaving(false);
    }
  };

  const openHistory = async () => {
    setMutationError(null);
    try {
      const payload = await api.getTeamEvents(projectKey);
      setEvents(Array.isArray(payload?.events) ? payload.events : []);
      setDialog({ type: 'history' });
    } catch (caught) {
      setMutationError(caught);
    }
  };
  const closeDialog = useCallback(() => {
    if (commandPending.current || saving) return;
    setDialog(null);
    setMutationError(null);
    commandAttempt.current = null;
  }, [saving]);
  const openDialog = (next) => {
    setMutationError(null);
    setSavedNotice(false);
    setDialog(next);
  };

  if (status === 'loading') return <main id="main-content" className="team-page"><div className="team-page-skeleton" role="status"><span /><span /><span /></div></main>;
  if (status === 'error' && !data) return <main id="main-content" className="team-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  if (!team) {
    return (
      <main id="main-content" className="team-page">
        <header className="team-page__header"><div><h1>{t('team.title')}</h1><p>{t('team.unavailable')}</p></div></header>
      </main>
    );
  }

  const counts = capabilityCounts(team.roles);
  const workers = rosteredWorkerCount(team.roles);
  const responsibilities = groupTeamRoles(team.roles);
  const responsibilityNames = Object.fromEntries(responsibilities.flatMap((group) => group.roles.map((role) => [role.role, t(`team.groups.${group.key}.title`)])));
  const defaultProvider = data?.start_context?.worker_backend?.mode || 'codex';
  const runtimeProps = { agents, backendOptions, agentsLoading, onRefresh: refreshAgents, defaultProvider, error: mutationError };
  const visibleRoles = team.roles.filter((role) => {
    const matches = [responsibilityNames[role.role], role.role, role.label, ...role.workers.flatMap((worker) => [worker.name, worker.runtimeLabel])]
      .join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
    const ready = role.state === 'enabled' && role.capability === 'ready';
    const needsSetup = role.state === 'enabled' && role.capability !== 'ready';
    return matches && (filter === 'all' || (filter === 'ready' ? ready : filter === 'disabled' ? role.state === 'disabled' : needsSetup));
  });
  const visibleGroups = groupTeamRoles(visibleRoles);

  return (
    <main id="main-content" className="team-page">
      <header className="team-page__header">
        <div>
          <p className="team-page__eyebrow">{data?.start_context?.project_name || projectKey}</p>
          <h1>{t('team.title')}</h1>
          <p>{t('team.description')}</p>
        </div>
        <div className="team-page__actions">
          <button type="button" className="product-button product-button--secondary team-configure-button" onClick={openHistory}>
            <History aria-hidden="true" size={16} />
            {t('team.history.button')}
          </button>
          <button type="button" className="product-button product-button--secondary team-configure-button" onClick={() => setSkillsOpen(true)}>
            <PackageOpen aria-hidden="true" size={16} />
            {t('team.skills.button')}
          </button>
          <button type="button" className="product-button product-button--secondary team-configure-button" disabled={saving} onClick={() => openDialog({ type: 'create' })}>
            <Plus aria-hidden="true" size={16} />
            {t('team.addWorker')}
          </button>
          <button type="button" className="product-button product-button--primary team-configure-button" disabled={saving || !team.roles.some((role) => role.state === 'enabled')} onClick={() => openDialog({ type: 'batch' })}>
            <Settings2 aria-hidden="true" size={16} />{t('team.bulk.button')}
          </button>
        </div>
      </header>

      <section className="team-summary" aria-label={t('team.summary.label')}>
        <div><strong>{responsibilities.length}</strong><span>{t('team.groups.summary')}</span></div>
        <div><strong>{workers}</strong><span>{t('team.summary.workers')}</span></div>
        <div data-tone="active"><strong>{counts.ready}</strong><span>{t('team.capability.ready')}</span></div>
        <div><strong>{counts.unstaffed}</strong><span>{t('team.capability.unstaffed')}</span></div>
        <div data-tone="degraded"><strong>{counts.degraded}</strong><span>{t('team.capability.degraded')}</span></div>
        <div data-tone="disabled"><strong>{counts.disabled}</strong><span>{t('team.capability.disabled')}</span></div>
      </section>

      {savedNotice ? <p className="team-save-notice" role="status">{t('team.config.saved')}</p> : null}
      {mutationError && !dialog ? <ProductError error={mutationError} onRetry={() => setMutationError(null)} t={t} /> : null}
      {loadError ? <ProductError error={loadError} onRetry={refreshAgents} t={t} /> : null}

      <section className="team-manager" aria-labelledby="team-manager-title">
        <div className="team-manager__row">
          <div className="team-worker__identity">
            <span className="team-worker__icon"><UserRoundCog aria-hidden="true" size={17} /></span>
            <div>
              <h2 id="team-manager-title">{team.manager.label}</h2>
              <p>{t('team.manager.hint')}</p>
            </div>
          </div>
          <div className="team-manager__runtime">
            <code>{team.manager.runtimeLabel || t('team.modelDefault')}</code>
            <StatusBadge tone="active">{t('team.manager.alwaysOn')}</StatusBadge>
          </div>
          <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => openDialog({ type: 'manager' })}>
            <Bot aria-hidden="true" size={15} />
            {t('team.manager.edit')}
          </button>
        </div>
      </section>

      <section className="team-roster" aria-labelledby="team-roster-title">
        <div className="team-roster__heading">
          <div>
            <h2 id="team-roster-title">{t('team.rolesTitle')}</h2>
            <p>{t('team.rolesDescription')}</p>
          </div>

        </div>
        <CollectionToolbar label={t('team.search')} query={query} onQueryChange={setQuery}
          filters={['all', 'attention', 'ready', 'disabled'].map((key) => [key, t(key === 'all' ? 'collection.all' : key === 'disabled' ? 'team.state.disabled' : `team.filter.${key}`)])}
          selected={filter} onSelect={setFilter} count={visibleGroups.length} t={t} />
        {!visibleRoles.length ? <div className="collection-empty"><p>{t('collection.noResults')}</p><Button variant="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>{t('collection.reset')}</Button></div> : null}
        <div className="team-responsibility-list">
          {visibleGroups.map((group) => (
            <ResponsibilityGroup key={group.key} group={group} t={t} saving={saving} forceOpen={Boolean(query.trim()) || filter !== 'all'}
              expansionContext={`${query.trim()}:${filter}`}
              onConfigure={() => openDialog({ type: 'batch', roleKeys: group.roles.map((role) => role.role) })}>
              <div className="team-worker-list">
                {group.roles.map((role) => (
                  <article className="team-worker" data-role-state={role.state} key={role.role} aria-labelledby={`team-role-${role.role}`}>
                    <div className="team-worker__overview">
                      <div className="team-worker__identity">
                        <span className="team-worker__icon"><Bot aria-hidden="true" size={17} /></span>
                        <div>
                          <h3 id={`team-role-${role.role}`}>{role.label}</h3>
                          <p><code>{role.role}</code></p>
                        </div>
                      </div>
                      <div className="team-worker__workers">
                        {role.workers.length ? (
                          <ul className="team-role-workers">
                            {role.workers.map((worker) => (
                              <li key={worker.id}>
                                <StatusBadge tone={worker.lifecycle === 'active' ? 'active' : 'neutral'}>{t(`team.lifecycle.${worker.lifecycle}`)}</StatusBadge>
                                <strong>{worker.name}</strong>
                                <span className="team-role-workers__runtime">{worker.provider} · {worker.model || t('team.dialog.inheritModel')}</span>
                                <span className="team-role-workers__actions">
                                  <button type="button" className="product-button product-button--secondary team-edit-button" aria-label={t('team.actions.editAria', { name: worker.name })} disabled={saving} onClick={() => openDialog({ type: 'worker', worker })}>
                                    <Pencil aria-hidden="true" size={14} />{t('team.config.edit')}
                                  </button>
                                  {worker.lifecycle === 'active' ? (
                                    <button type="button" className="team-icon-button" title={t('team.actions.pause')} aria-label={t('team.actions.pauseAria', { name: worker.name })} disabled={saving} onClick={() => runCommands([{ kind: 'inactivate_worker', worker_id: worker.id }], { reason: `Pause ${worker.name}` })}>
                                      <Pause aria-hidden="true" size={14} />
                                    </button>
                                  ) : (
                                    <button type="button" className="team-icon-button" title={t('team.actions.activate')} aria-label={t('team.actions.activateAria', { name: worker.name })} disabled={saving} onClick={() => runCommands([{ kind: 'activate_worker', worker_id: worker.id }], { reason: `Activate ${worker.name}` })}>
                                      <Play aria-hidden="true" size={14} />
                                    </button>
                                  )}
                                  <button type="button" className="team-icon-button" title={t('team.actions.delete')} aria-label={t('team.actions.deleteAria', { name: worker.name })} disabled={saving} onClick={() => runCommands([{ kind: 'delete_worker', worker_id: worker.id }], { reason: `Delete ${worker.name}` })}>
                                    <Trash2 aria-hidden="true" size={14} />
                                  </button>
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="team-worker__empty">{t('team.noWorkers')}</p>
                        )}
                      </div>
                      <div className="team-worker__capability">
                        <StatusBadge tone={role.state === 'disabled' ? 'neutral' : role.capabilityTone}>
                          {role.state === 'disabled' ? t('team.state.disabled') : t(`team.capability.${role.capability}`)}
                        </StatusBadge>
                        <span className="team-role-actions">
                          {role.state === 'enabled' ? (
                            <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => runCommands([{ kind: 'disable_role', role: role.role }], { reason: `Disable ${role.label}` })}>
                              {t('team.actions.disableRole')}
                            </button>
                          ) : (
                            <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => runCommands([{ kind: 'enable_role', role: role.role }], { reason: `Enable ${role.label}` })}>
                              {t('team.actions.enableRole')}
                            </button>
                          )}
                          <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => openDialog({ type: 'create', roleEntry: role })}>
                            <Plus aria-hidden="true" size={14} />
                            {t('team.actions.addWorker')}
                          </button>
                        </span>
                        {role.capability === 'degraded' ? (
                          <p className="team-role-warning"><AlertTriangle aria-hidden="true" size={13} />{t('team.degradedHint')}</p>
                        ) : null}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </ResponsibilityGroup>
          ))}
        </div>
      </section>

      {skillsOpen ? <SkillLibraryPanel api={api} onClose={() => setSkillsOpen(false)} t={t} /> : null}
      {dialog?.type === 'history' ? <HistoryPanel events={events} onClose={closeDialog} t={t} /> : null}
      {dialog?.type === 'batch' ? <BatchWorkerDialog {...runtimeProps} roles={dialog.roleKeys ? team.roles.filter((role) => dialog.roleKeys.includes(role.role)) : team.roles} saving={saving} onClose={closeDialog} onSubmit={(commands) => runCommands(commands, { reason: 'Configure workers in selected roles' })} t={t} /> : null}
      {dialog?.type === 'worker' ? (
        <WorkerDialog
          {...runtimeProps}
          onClose={closeDialog}
          onSubmit={(commands) => runCommands(commands, { reason: `Update ${dialog.worker.name}` })}
          saving={saving}
          t={t}
          worker={dialog.worker}
        />
      ) : null}
      {dialog?.type === 'create' ? (
        <WorkerDialog
          {...runtimeProps}
          onClose={closeDialog}
          onSubmit={(commands) => runCommands(commands, { reason: 'Add worker' })}
          roleEntry={dialog.roleEntry}
          roles={team.roles}
          saving={saving}
          t={t}
        />
      ) : null}
      {dialog?.type === 'manager' ? (
        <WorkerDialog
          key={projectKey}
          {...runtimeProps}
          manager
          managerRuntime={team.manager}
          assistantSession={data?.task_assistant_session}
          onClose={closeDialog}
          onSubmit={saveManagerRuntime}
          saving={saving}
          t={t}
        />
      ) : null}
    </main>
  );
}
