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
import WorkerDialog from './WorkerDialog';
import { saveAssistantRuntime } from './saveAssistantRuntime';
import ResponsibilityGroup from './ResponsibilityGroup';
import RoleSkillsDialog from './RoleSkillsDialog';
import SkillLibraryPanel from './SkillLibraryPanel';
import useTeamDialog from './useTeamDialog';
import { buildTeamModel, capabilityCounts, groupTeamRoles, rosteredWorkerCount } from './teamModel';

function newCommandKey() {
  return globalThis.crypto?.randomUUID?.() || `team-${Date.now()}-${Math.random().toString(36).slice(2)}`;
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

  const runCommands = async (commands, { reason = '', expectedTeamRevision = null } = {}) => {
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
      await api.sendTeamCommands(projectKey, commands, {
        reason,
        commandKey: commandAttempt.current.key,
        expectedTeamRevision,
      });
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
    try {
      return await saveAssistantRuntime({ api, projectKey, commands, refresh });
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
                                  <button type="button" className="product-button product-button--secondary team-edit-button" aria-label={t('team.actions.editAria', { name: `${role.label} / ${worker.name} (${worker.id})` })} disabled={saving} onClick={() => openDialog({ type: 'worker', worker })}>
                                    <Pencil aria-hidden="true" size={14} />{t('team.config.edit')}
                                  </button>
                                  {worker.lifecycle === 'active' ? (
                                    <button type="button" className="team-icon-button" title={t('team.actions.pause')} aria-label={t('team.actions.pauseAria', { name: `${role.label} / ${worker.name} (${worker.id})` })} disabled={saving} onClick={() => runCommands([{ kind: 'inactivate_worker', worker_id: worker.id }], { reason: `Pause ${worker.name}` })}>
                                      <Pause aria-hidden="true" size={14} />
                                    </button>
                                  ) : (
                                    <button type="button" className="team-icon-button" title={t('team.actions.activate')} aria-label={t('team.actions.activateAria', { name: `${role.label} / ${worker.name} (${worker.id})` })} disabled={saving} onClick={() => runCommands([{ kind: 'activate_worker', worker_id: worker.id }], { reason: `Activate ${worker.name}` })}>
                                      <Play aria-hidden="true" size={14} />
                                    </button>
                                  )}
                                  <button type="button" className="team-icon-button" title={t('team.actions.delete')} aria-label={t('team.actions.deleteAria', { name: `${role.label} / ${worker.name} (${worker.id})` })} disabled={saving} onClick={() => runCommands([{ kind: 'delete_worker', worker_id: worker.id }], { reason: `Delete ${worker.name}` })}>
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
                          <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => openDialog({ type: 'role-skills', roleKey: role.role })}>
                            <PackageOpen aria-hidden="true" size={14} />
                            {t('team.roleSkills.button')}
                          </button>
                          <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => openDialog({ type: 'create', roleEntry: role })}>
                            <Plus aria-hidden="true" size={14} />
                            {t('team.actions.addWorker')}
                          </button>
                        </span>
                        <p className="team-role-method-status" data-status={role.skillBinding.status}>
                          <span>{t(`team.roleSkills.status.${role.skillBinding.status}`)}</span>
                          {role.skillBinding.executionStatus !== 'connected'
                            ? <small>{t('team.roleSkills.executionPending')}</small>
                            : null}
                        </p>
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
      {dialog?.type === 'role-skills' ? (() => {
        const currentRole = team.roles.find((role) => role.role === dialog.roleKey);
        if (!currentRole) return null;
        return (
          <RoleSkillsDialog
            api={api}
            currentTeamRevision={team.revision}
            error={mutationError}
            onClose={closeDialog}
            onRefresh={refresh}
            projectKey={projectKey}
            onSubmit={(command, options) => runCommands(
              [command],
              {
                reason: `Configure Skills for ${currentRole.label}`,
                expectedTeamRevision: options.expectedTeamRevision,
              },
            )}
            role={currentRole}
            saving={saving}
            t={t}
          />
        );
      })() : null}
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
