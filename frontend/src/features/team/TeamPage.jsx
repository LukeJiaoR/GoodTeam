import { AlertTriangle, Bot, History, Pause, Play, Plus, Trash2, UserRoundCog, X } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import useProjectState from '../runs/useProjectState';
import { settingsApi } from '../settings/settingsApi';
import './team.css';
import { buildTeamModel, capabilityCounts, rosteredWorkerCount, workerCreateCommand, workerEditCommands } from './teamModel';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'select:not([disabled])',
  'input:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function useDialogAccessibility(dialogRef, closeRef, onClose) {
  const closeOnEscape = useCallback(
    (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    },
    [onClose],
  );
  useEffect(() => {
    closeRef.current?.focus();
  }, [closeRef]);
  const handleKeyDown = (event) => {
    closeOnEscape(event);
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR)];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return handleKeyDown;
}

const EMPTY_DRAFT = { name: '', role: '', lifecycle: '', provider: '', model: '', reasoningDepth: 'default', additionalPrompt: '' };

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

function RuntimeFields({ draft, setDraft, backendOptions, agents, models, t }) {
  const backend = agents.find((agent) => agent.backend === draft.provider);
  const modelOptions = [...new Set([draft.model, ...(backend?.models || models || [])].filter(Boolean))];
  return (
    <>
      <label>
        <span>{t('team.dialog.provider')}</span>
        <select aria-label={t('team.dialog.provider')} value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value, model: '' })}>
          <option value="">{t('team.dialog.inheritProvider')}</option>
          {backendOptions.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </label>
      <label>
        <span>{t('team.dialog.model')}</span>
        <select aria-label={t('team.dialog.model')} value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })}>
          <option value="">{t('team.dialog.inheritModel')}</option>
          {modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
        </select>
      </label>
      <label>
        <span>{t('team.dialog.reasoning')}</span>
        <select aria-label={t('team.dialog.reasoning')} value={draft.reasoningDepth} onChange={(event) => setDraft({ ...draft, reasoningDepth: event.target.value })}>
          <option value="default">{t('team.dialog.reasoningDefault')}</option>
          <option value="low">{t('team.dialog.reasoningLow')}</option>
          <option value="medium">{t('team.dialog.reasoningMedium')}</option>
          <option value="high">{t('team.dialog.reasoningHigh')}</option>
        </select>
      </label>
      <label className="team-dialog__prompt">
        <span>{t('team.dialog.additionalPrompt')}</span>
        <textarea
          aria-label={t('team.dialog.additionalPrompt')}
          maxLength="4000"
          rows="2"
          value={draft.additionalPrompt}
          onChange={(event) => setDraft({ ...draft, additionalPrompt: event.target.value })}
          placeholder={t('team.dialog.additionalPromptPlaceholder')}
        />
      </label>
    </>
  );
}

function WorkerDialog({ agents, backendOptions, onClose, onSubmit, roleEntry, roles, manager, managerRuntime, saving, t, worker }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const handleKeyDown = useDialogAccessibility(dialogRef, closeRef, onClose);
  const editing = Boolean(worker);
  const [draft, setDraft] = useState(() => {
    if (worker) return draftFromWorker(worker);
    if (manager) return { ...EMPTY_DRAFT, provider: managerRuntime?.provider || '', model: managerRuntime?.model || '', reasoningDepth: managerRuntime?.reasoningDepth || 'default', additionalPrompt: managerRuntime?.additionalPrompt || '' };
    return { ...EMPTY_DRAFT, role: roleEntry?.role || '' };
  });

  const submit = (event) => {
    event.preventDefault();
    if (manager) {
      onSubmit([{ kind: 'change_manager_runtime', provider: draft.provider, model: draft.model, reasoning_depth: draft.reasoningDepth, additional_prompt: draft.additionalPrompt.trim() }]);
      return;
    }
    if (editing) {
      onSubmit(workerEditCommands(worker, draft));
      return;
    }
    onSubmit([workerCreateCommand(draft.role, draft)]);
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
          <button ref={closeRef} className="team-icon-button" type="button" onClick={onClose} aria-label={t('team.dialog.close')}>
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <div className="team-dialog__fields">
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
                  {(roles || (roleEntry ? [roleEntry] : [])).map((entry) => (
                    <option key={entry.role} value={entry.role}>{entry.label}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>{t('team.dialog.name')}</span>
                <input aria-label={t('team.dialog.name')} maxLength="80" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder={t('team.dialog.namePlaceholder')} />
              </label>
            </>
          ) : null}
          <RuntimeFields draft={draft} setDraft={setDraft} agents={agents} backendOptions={backendOptions} t={t} />
        </div>

        <footer className="team-dialog__footer">
          <Button variant="secondary" onClick={onClose}>{t('team.dialog.cancel')}</Button>
          <Button type="submit" busy={saving}>{manager ? t('team.dialog.saveManager') : editing ? t('team.dialog.saveWorker') : t('team.dialog.createWorker')}</Button>
        </footer>
      </form>
    </div>
  );
}

function HistoryPanel({ onClose, events, t }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const handleKeyDown = useDialogAccessibility(dialogRef, closeRef, onClose);
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
  const [loadError, setLoadError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [mutationError, setMutationError] = useState(null);
  const [dialog, setDialog] = useState(null); // {type: 'worker'|'create'|'manager'|'history', roleEntry?, worker?}
  const [events, setEvents] = useState([]);
  const addWorkerButtonRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    api.getSettingsAgents({ signal: controller.signal })
      .then((payload) => setAgents(Array.isArray(payload?.agents) ? payload.agents : []))
      .catch((caught) => { if (caught?.name !== 'AbortError') setLoadError(caught); });
    return () => controller.abort();
  }, [api]);

  const team = useMemo(() => buildTeamModel(data), [data]);
  const backendOptions = useMemo(() => [...new Set([
    ...(data?.start_context?.worker_backend_options || []),
    ...agents.filter((agent) => agent.available !== false).map((agent) => agent.backend),
  ])], [data, agents]);

  const runCommands = async (commands, { reason = '' } = {}) => {
    if (!commands.length) {
      setDialog(null);
      return;
    }
    setSaving(true);
    setMutationError(null);
    try {
      await api.sendTeamCommands(projectKey, commands, { reason });
      await refresh();
      setDialog(null);
    } catch (caught) {
      setMutationError(caught);
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
    setDialog(null);
    queueMicrotask(() => addWorkerButtonRef.current?.focus());
  }, []);

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
          <button ref={addWorkerButtonRef} type="button" className="product-button product-button--primary team-configure-button" onClick={() => setDialog({ type: 'create' })}>
            <Plus aria-hidden="true" size={16} />
            {t('team.addWorker')}
          </button>
        </div>
      </header>

      <section className="team-summary" aria-label={t('team.summary.label')}>
        <div><strong>{team.roles.length}</strong><span>{t('team.summary.roles')}</span></div>
        <div><strong>{workers}</strong><span>{t('team.summary.workers')}</span></div>
        <div data-tone="active"><strong>{counts.ready}</strong><span>{t('team.capability.ready')}</span></div>
        <div><strong>{counts.unstaffed}</strong><span>{t('team.capability.unstaffed')}</span></div>
        <div data-tone="degraded"><strong>{counts.degraded}</strong><span>{t('team.capability.degraded')}</span></div>
        <div data-tone="disabled"><strong>{counts.disabled}</strong><span>{t('team.capability.disabled')}</span></div>
      </section>

      {mutationError ? <ProductError error={mutationError} onRetry={() => setMutationError(null)} t={t} /> : null}
      {loadError ? <ProductError error={loadError} onRetry={() => setLoadError(null)} t={t} /> : null}

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
          <button type="button" className="product-button product-button--secondary" onClick={() => setDialog({ type: 'manager' })}>
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
          <div className="team-roster__columns" aria-hidden="true">
            <span>{t('team.columns.workers')}</span><span>{t('team.columns.runtime')}</span><span>{t('team.columns.capability')}</span>
          </div>
        </div>
        <div className="team-worker-list">
          {team.roles.map((role) => (
            <article className="team-worker" data-role-state={role.state} key={role.role}>
              <div className="team-worker__overview">
                <div className="team-worker__identity">
                  <span className="team-worker__icon"><Bot aria-hidden="true" size={17} /></span>
                  <div>
                    <h3>{role.label}</h3>
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
                          <span className="team-role-workers__runtime">{worker.runtimeLabel || t('team.modelDefault')}</span>
                          <span className="team-role-workers__actions">
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
                            <button type="button" className="team-icon-button" onClick={() => setDialog({ type: 'worker', worker })}>
                              <span aria-hidden="true">✎</span>
                              <span className="visually-hidden">{t('team.actions.editAria', { name: worker.name })}</span>
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
                    <button type="button" className="product-button product-button--secondary" disabled={saving} onClick={() => setDialog({ type: 'create', roleEntry: role })}>
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
      </section>

      {dialog?.type === 'history' ? <HistoryPanel events={events} onClose={closeDialog} t={t} /> : null}
      {dialog?.type === 'worker' ? (
        <WorkerDialog
          agents={agents}
          backendOptions={backendOptions}
          onClose={closeDialog}
          onSubmit={(commands) => runCommands(commands, { reason: `Update ${dialog.worker.name}` })}
          saving={saving}
          t={t}
          worker={dialog.worker}
        />
      ) : null}
      {dialog?.type === 'create' ? (
        <WorkerDialog
          agents={agents}
          backendOptions={backendOptions}
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
          agents={agents}
          backendOptions={backendOptions}
          manager
          managerRuntime={team.manager}
          onClose={closeDialog}
          onSubmit={(commands) => runCommands(commands, { reason: 'Update Task Assistant runtime' })}
          saving={saving}
          t={t}
        />
      ) : null}
    </main>
  );
}
