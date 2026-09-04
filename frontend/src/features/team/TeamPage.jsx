import { AlertTriangle, Bot, Check, ChevronRight, Settings2, X } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import useProjectState from '../runs/useProjectState';
import { settingsApi } from '../settings/settingsApi';
import './team.css';
import { buildWorkerTypes } from './teamModel';
const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'select:not([disabled])',
  'input:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function ConfigurationDialog({ agents, data, onClose, onSave, saving, t }) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const selectAllRef = useRef(null);
  const context = data?.start_context || {};
  const globalBackend = context.worker_backend?.mode || 'codex';
  const initialRows = useMemo(() => buildWorkerTypes(data, agents), [agents, data]);
  const [draft, setDraft] = useState(() => Object.fromEntries(initialRows.map((row) => [row.role, {
    backend: row.backend,
    model: row.modelExplicit ? row.model : '',
    reasoningDepth: row.reasoningDepth,
    additionalPrompt: row.additionalPrompt,
    desiredInstances: row.desiredInstances,
  }])));
  const [selectedRoles, setSelectedRoles] = useState(() => new Set());
  const [batchDraft, setBatchDraft] = useState({
    backend: globalBackend,
    model: '',
    reasoningDepth: 'default',
    additionalPrompt: '',
    desiredInstances: 1,
  });
  const availableAgents = agents.filter((agent) => agent.available !== false);
  const agentByBackend = Object.fromEntries(agents.map((agent) => [agent.backend, agent]));
  const backendOptions = [...new Set([
    ...(context.worker_backend_options || []),
    ...availableAgents.map((agent) => agent.backend),
  ])];
  const batchBackend = agentByBackend[batchDraft.backend];
  const batchModels = [...new Set([batchDraft.model, ...(batchBackend?.models || [])].filter(Boolean))];
  const allSelected = selectedRoles.size === initialRows.length;

  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selectedRoles.size > 0 && !allSelected;
  }, [allSelected, selectedRoles]);

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
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

  const setBackend = (role, backend) => {
    setDraft((current) => ({ ...current, [role]: { ...current[role], backend, model: '' } }));
  };
  const setModel = (role, model) => {
    setDraft((current) => ({ ...current, [role]: { ...current[role], model } }));
  };
  const setTypeSetting = (role, key, value) => {
    setDraft((current) => ({ ...current, [role]: { ...current[role], [key]: value } }));
  };
  const toggleRole = (role) => {
    setSelectedRoles((current) => {
      const next = new Set(current);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });
  };
  const toggleAll = () => {
    setSelectedRoles(allSelected ? new Set() : new Set(initialRows.map((row) => row.role)));
  };
  const setBatchSetting = (key, value) => {
    setBatchDraft((current) => ({ ...current, [key]: value }));
  };
  const setBatchBackend = (backend) => {
    setBatchDraft((current) => ({ ...current, backend, model: '' }));
  };
  const applyBatch = () => {
    setDraft((current) => {
      const next = { ...current };
      for (const role of selectedRoles) {
        next[role] = { ...batchDraft };
      }
      return next;
    });
  };
  const submit = (event) => {
    event.preventDefault();
    const workers = {};
    const roleModels = {};
    const workerTypeSettings = {};
    for (const row of initialRows) {
      const value = draft[row.role];
      if (value.backend !== globalBackend) workers[row.role] = value.backend;
      if (value.model) roleModels[row.role] = `${value.backend}:${value.model}`;
      workerTypeSettings[row.role] = {
        reasoning_depth: value.reasoningDepth,
        additional_prompt: value.additionalPrompt.trim(),
        desired_instances: Number(value.desiredInstances),
      };
    }
    onSave({ globalBackend, workers, roleModels, workerTypeSettings });
  };

  return (
    <div className="team-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <form
        ref={dialogRef}
        className="team-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={handleKeyDown}
        onSubmit={submit}
      >
        <header className="team-dialog__header">
          <div>
            <p className="team-dialog__eyebrow">{t('team.configuration.eyebrow')}</p>
            <h2 id={titleId}>{t('team.configure')}</h2>
            <p id={descriptionId}>{t('team.configuration.description')}</p>
          </div>
          <button ref={closeRef} className="team-icon-button" type="button" onClick={onClose} aria-label={t('team.configuration.close')}>
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <div className="team-configuration-list">
          <section className="team-batch-configuration" aria-labelledby="team-batch-title">
            <header className="team-batch-configuration__header">
              <div>
                <h3 id="team-batch-title">{t('team.configuration.batchTitle')}</h3>
                <p>{t('team.configuration.batchDescription')}</p>
              </div>
              <label className="team-selection-control">
                <input ref={selectAllRef} type="checkbox" checked={allSelected} onChange={toggleAll} />
                <span>{t('team.configuration.selectAll')}</span>
              </label>
            </header>
            <div className="team-batch-configuration__fields">
              <label>
                <span>{t('team.configuration.runtime')}</span>
                <select aria-label={t('team.configuration.batchRuntime')} value={batchDraft.backend} onChange={(event) => setBatchBackend(event.target.value)}>
                  {backendOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              </label>
              <label>
                <span>{t('team.configuration.model')}</span>
                <select aria-label={t('team.configuration.batchModel')} value={batchDraft.model} onChange={(event) => setBatchSetting('model', event.target.value)}>
                  <option value="">{t('team.configuration.inheritModel')}</option>
                  {batchModels.map((model) => <option key={model} value={model}>{model}</option>)}
                </select>
              </label>
              <label>
                <span>{t('team.configuration.reasoning')}</span>
                <select aria-label={t('team.configuration.batchReasoning')} value={batchDraft.reasoningDepth} onChange={(event) => setBatchSetting('reasoningDepth', event.target.value)}>
                  <option value="default">{t('team.configuration.reasoningDefault')}</option>
                  <option value="low">{t('team.configuration.reasoningLow')}</option>
                  <option value="medium">{t('team.configuration.reasoningMedium')}</option>
                  <option value="high">{t('team.configuration.reasoningHigh')}</option>
                </select>
              </label>
              <label>
                <span>{t('team.configuration.desiredInstances')}</span>
                <input aria-label={t('team.configuration.batchDesiredInstances')} type="number" min="1" max="8" step="1" required value={batchDraft.desiredInstances} onChange={(event) => setBatchSetting('desiredInstances', event.target.value)} />
              </label>
              <label className="team-batch-configuration__prompt">
                <span>{t('team.configuration.additionalPrompt')}</span>
                <textarea aria-label={t('team.configuration.batchAdditionalPrompt')} maxLength="4000" rows="2" value={batchDraft.additionalPrompt} onChange={(event) => setBatchSetting('additionalPrompt', event.target.value)} placeholder={t('team.configuration.additionalPromptPlaceholder')} />
              </label>
              <Button type="button" variant="secondary" disabled={!selectedRoles.size} onClick={applyBatch}>
                {t('team.configuration.applyBatch', { count: selectedRoles.size })}
              </Button>
            </div>
          </section>
          {initialRows.map((row) => {
            const value = draft[row.role];
            const backend = agentByBackend[value.backend];
            const models = [...new Set([value.model, ...(backend?.models || [])].filter(Boolean))];
            return (
              <section className="team-configuration-row" key={row.role}>
                <div className="team-configuration-row__identity">
                  <label className="team-selection-control">
                    <input type="checkbox" checked={selectedRoles.has(row.role)} onChange={() => toggleRole(row.role)} aria-label={t('team.configuration.selectType', { type: row.label })} />
                    <span><strong>{row.label}</strong><code>{row.role}</code></span>
                  </label>
                </div>
                <label>
                  <span>{t('team.configuration.runtime')}</span>
                  <select aria-label={t('team.configuration.runtime')} value={value.backend} onChange={(event) => setBackend(row.role, event.target.value)}>
                    {backendOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </label>
                <label>
                  <span>{t('team.configuration.model')}</span>
                  <select aria-label={t('team.configuration.model')} value={value.model} onChange={(event) => setModel(row.role, event.target.value)}>
                    <option value="">{t('team.configuration.inheritModel')}</option>
                    {models.map((model) => <option key={model} value={model}>{model}</option>)}
                  </select>
                </label>
                <label>
                  <span>{t('team.configuration.reasoning')}</span>
                  <select aria-label={t('team.configuration.reasoning')} value={value.reasoningDepth} onChange={(event) => setTypeSetting(row.role, 'reasoningDepth', event.target.value)}>
                    <option value="default">{t('team.configuration.reasoningDefault')}</option>
                    <option value="low">{t('team.configuration.reasoningLow')}</option>
                    <option value="medium">{t('team.configuration.reasoningMedium')}</option>
                    <option value="high">{t('team.configuration.reasoningHigh')}</option>
                  </select>
                </label>
                <label>
                  <span>{t('team.configuration.desiredInstances')}</span>
                  <input aria-label={t('team.configuration.desiredInstances')} type="number" min="1" max="8" step="1" required value={value.desiredInstances} onChange={(event) => setTypeSetting(row.role, 'desiredInstances', event.target.value)} />
                </label>
                <div className="team-configuration-row__readiness" data-ready={backend?.ready !== false}>
                  {backend?.ready === false ? <AlertTriangle aria-hidden="true" size={14} /> : <Check aria-hidden="true" size={14} />}
                  <span>{backend?.ready === false ? t('team.configuration.needsAttention') : t('team.configuration.available')}</span>
                </div>
                <label className="team-configuration-row__prompt">
                  <span>{t('team.configuration.additionalPrompt')}</span>
                  <textarea aria-label={t('team.configuration.additionalPrompt')} maxLength="4000" rows="2" value={value.additionalPrompt} onChange={(event) => setTypeSetting(row.role, 'additionalPrompt', event.target.value)} placeholder={t('team.configuration.additionalPromptPlaceholder')} />
                </label>
              </section>
            );
          })}
        </div>

        <footer className="team-dialog__footer">
          <Button variant="secondary" onClick={onClose}>{t('team.configuration.cancel')}</Button>
          <Button type="submit" busy={saving}>{t('team.configuration.save')}</Button>
        </footer>
      </form>
    </div>
  );
}

export default function TeamPage({ api = settingsApi }) {
  const { projectKey = '' } = useParams();
  const { t } = useOutletContext();
  const stateApi = useMemo(() => ({ getProjectState: api.getProjectSettings }), [api]);
  const { data, error, refresh, status } = useProjectState(projectKey, { api: stateApi, poll: true });
  const [agents, setAgents] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [mutationError, setMutationError] = useState(null);
  const configureButtonRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    api.getSettingsAgents({ signal: controller.signal })
      .then((payload) => setAgents(Array.isArray(payload?.agents) ? payload.agents : []))
      .catch((caught) => { if (caught?.name !== 'AbortError') setMutationError(caught); });
    return () => controller.abort();
  }, [api]);

  const closeDialog = useCallback(() => {
    setDialogOpen(false);
    queueMicrotask(() => configureButtonRef.current?.focus());
  }, []);
  const save = async ({ globalBackend, workers, roleModels, workerTypeSettings }) => {
    setSaving(true);
    setMutationError(null);
    try {
      await api.saveWorkerDefaults(projectKey, globalBackend, workers, roleModels, workerTypeSettings);
      await refresh();
      closeDialog();
    } catch (caught) {
      setMutationError(caught);
    } finally {
      setSaving(false);
    }
  };

  if (status === 'loading') return <main id="main-content" className="team-page"><div className="team-page-skeleton" role="status"><span /><span /><span /></div></main>;
  if (status === 'error' && !data) return <main id="main-content" className="team-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;

  const rows = buildWorkerTypes(data, agents);
  const counts = rows.reduce((result, row) => ({
    ...result,
    [row.status]: (result[row.status] || 0) + 1,
    instances: result.instances + row.instances.length,
  }), { active: 0, idle: 0, unavailable: 0, instances: 0 });

  return (
    <main id="main-content" className="team-page">
      <header className="team-page__header">
        <div>
          <p className="team-page__eyebrow">{data?.start_context?.project_name || projectKey}</p>
          <h1>{t('team.title')}</h1>
          <p>{t('team.description')}</p>
        </div>
        <button ref={configureButtonRef} type="button" className="product-button product-button--primary team-configure-button" onClick={() => setDialogOpen(true)}>
          <Settings2 aria-hidden="true" size={16} />
          {t('team.configure')}
        </button>
      </header>

      <section className="team-summary" aria-label={t('team.summary.label')}>
        <div><strong>{rows.length}</strong><span>{t('team.summary.types')}</span></div>
        <div><strong>{counts.instances}</strong><span>{t('team.summary.instances')}</span></div>
        <div data-tone="active"><strong>{counts.active}</strong><span>{t('team.status.active')}</span></div>
        <div><strong>{counts.idle}</strong><span>{t('team.status.idle')}</span></div>
        <div data-tone="unavailable"><strong>{counts.unavailable}</strong><span>{t('team.status.unavailable')}</span></div>
      </section>

      {mutationError ? <ProductError error={mutationError} onRetry={() => setMutationError(null)} t={t} /> : null}

      <section className="team-roster" aria-labelledby="team-roster-title">
        <div className="team-roster__heading">
          <div>
            <h2 id="team-roster-title">{t('team.workerTypes')}</h2>
            <p>{t('team.workerTypesDescription')}</p>
          </div>
          <div className="team-roster__columns" aria-hidden="true">
            <span>{t('team.columns.runtime')}</span><span>{t('team.columns.model')}</span><span>{t('team.columns.status')}</span>
          </div>
        </div>
        <div className="team-worker-list">
          {rows.map((row) => (
            <article className="team-worker" key={row.role}>
              <div className="team-worker__overview">
                <div className="team-worker__identity">
                  <span className="team-worker__icon"><Bot aria-hidden="true" size={17} /></span>
                  <div>
                    <h3>{row.label}</h3>
                    <p>{t('team.instanceCount', { count: row.instances.length })}</p>
                    {row.reasoningDepth !== 'default' || row.desiredInstances !== 1 || row.additionalPrompt ? (
                      <p className="team-worker__settings">
                        {row.reasoningDepth !== 'default' ? <span>{t('team.reasoningSummary', { depth: t(`team.configuration.reasoning${row.reasoningDepth[0].toUpperCase()}${row.reasoningDepth.slice(1)}`) })}</span> : null}
                        {row.desiredInstances !== 1 ? <span>{t('team.desiredSummary', { count: row.desiredInstances })}</span> : null}
                        {row.additionalPrompt ? <span>{t('team.additionalPromptSet')}</span> : null}
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="team-worker__configuration">
                  <div className="team-worker__runtime"><span>{t('team.columns.runtime')}</span><code>{row.backend}</code></div>
                  <div className="team-worker__model"><span>{t('team.columns.model')}</span><code>{row.model || data?.start_context?.model_profile || t('team.modelDefault')}</code></div>
                  <div className="team-worker__status"><span>{t('team.columns.status')}</span><StatusBadge tone={row.status === 'active' ? 'active' : row.status === 'unavailable' ? 'danger' : 'neutral'}>{t(`team.status.${row.status}`)}</StatusBadge></div>
                </div>
              </div>
              {row.instances.length ? (
                <ul className="team-instance-list">
                  {row.instances.map((instance) => (
                    <li key={instance.id}>
                      <span className="team-instance-state" data-state={instance.state} />
                      <div><strong>{instance.responsibility || t('team.instanceUnassigned')}</strong><span>{instance.task_id || instance.id}</span></div>
                      <span>{t(`work.team.status.${instance.state}`)}</span>
                      <ChevronRight aria-hidden="true" size={15} />
                    </li>
                  ))}
                </ul>
              ) : <p className="team-worker__empty">{t('team.noInstances')}</p>}
            </article>
          ))}
        </div>
      </section>

      {dialogOpen ? <ConfigurationDialog agents={agents} data={data} onClose={closeDialog} onSave={save} saving={saving} t={t} /> : null}
    </main>
  );
}
