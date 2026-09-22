import { X } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import RuntimeFields from './RuntimeFields';
import useTeamDialog from './useTeamDialog';
import { groupTeamRoles, newWorkerRuntime, workerCreateCommand, workerEditCommands } from './teamModel';
import './team.css';

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

export default function WorkerDialog({ presentation = 'dialog', children, agents, backendOptions, agentsLoading, onRefresh, defaultProvider, error, onClose, onSubmit, roleEntry, roles, manager, managerRuntime, assistantSession, saving, t, worker }) {
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
    <div className={`team-dialog-backdrop${presentation === 'sheet' ? ' team-dialog-backdrop--sheet' : ''}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
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

        {children}

        <footer className="team-dialog__footer">
          <Button variant="secondary" disabled={saving} onClick={onClose}>{t(manager && verification?.saved ? 'team.dialog.close' : 'team.dialog.cancel')}</Button>
          <Button type="submit" busy={saving} disabled={locked}>{manager ? t('team.dialog.saveAndCheck') : editing ? t('team.dialog.saveWorker') : t('team.dialog.createWorker')}</Button>
        </footer>
      </form>
    </div>
  );
}
