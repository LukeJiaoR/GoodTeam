import { ChevronDown, X } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import RuntimeFields from './RuntimeFields';
import { batchWorkerPlan, groupTeamRoles, initialWorkerRuntime } from './teamModel';
import useTeamDialog from './useTeamDialog';

function ResponsibilitySelection({ group, selected, setSelected, t }) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const label = t(`team.groups.${group.key}.title`);
  const keys = group.roles.map((role) => role.role);
  const count = keys.filter((key) => selected.includes(key)).length;
  return (
    <section className="team-bulk-group" aria-label={label}>
      <div className="team-bulk-group__header">
        <label className="team-bulk-role" data-selected={count > 0}>
          <input type="checkbox" checked={count === keys.length} aria-label={t('team.groups.select', { name: label })}
            ref={(element) => { if (element) element.indeterminate = count > 0 && count < keys.length; }}
            onChange={(event) => setSelected(event.target.checked ? [...new Set([...selected, ...keys])] : selected.filter((key) => !keys.includes(key)))} />
          <span><strong>{label}</strong><small>{t('team.groups.selectionCount', { selected: count, count: keys.length })}</small></span>
        </label>
        <button type="button" className="team-icon-button" aria-expanded={expanded} aria-controls={contentId}
          aria-label={t(expanded ? 'team.groups.collapse' : 'team.groups.expand', { name: label })} onClick={() => setExpanded(!expanded)}>
          <ChevronDown aria-hidden="true" size={16} />
        </button>
      </div>
      <div id={contentId} hidden={!expanded} className="team-bulk-group__roles">
        {expanded ? group.roles.map((role) => (
          <label key={role.role} className="team-bulk-role" data-selected={selected.includes(role.role)}>
            <input type="checkbox" checked={selected.includes(role.role)} onChange={(event) => setSelected(event.target.checked ? [...selected, role.role] : selected.filter((key) => key !== role.role))} />
            <span><strong>{role.label}</strong><small>{t(role.workers.length ? 'team.bulk.workerCount' : 'team.bulk.createOne', { count: role.workers.length })}</small></span>
          </label>
        )) : null}
      </div>
    </section>
  );
}

export default function BatchWorkerDialog({ roles, defaultProvider, agents, backendOptions, agentsLoading, onRefresh, saving, error, onClose, onSubmit, t }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const submitting = useRef(false);
  const handleKeyDown = useTeamDialog(dialogRef, closeRef, onClose);
  const eligible = roles.filter((role) => role.state === 'enabled');
  const [selected, setSelected] = useState(() => eligible.map((role) => role.role));
  const [draft, setDraft] = useState(() => initialWorkerRuntime(roles, defaultProvider));
  const plan = batchWorkerPlan(roles, selected, { ...draft, model: draft.model.trim() });
  const selectedCount = eligible.filter((role) => selected.includes(role.role)).length;

  const submit = async (event) => {
    event.preventDefault();
    if (saving || submitting.current || !plan.commands.length) return;
    submitting.current = true;
    try { await onSubmit(plan.commands); } finally { submitting.current = false; }
  };

  return (
    <div className="team-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <form className="team-dialog team-bulk-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={handleKeyDown} onSubmit={submit}>
        <header className="team-dialog__header">
          <div>
            <p className="team-dialog__eyebrow">{t('team.config.eyebrow')}</p>
            <h2 id={titleId}>{t('team.bulk.title')}</h2>
            <p>{t('team.bulk.description')}</p>
          </div>
          <button ref={closeRef} className="team-icon-button" type="button" disabled={saving} onClick={onClose} aria-label={t('team.dialog.close')}><X aria-hidden="true" size={18} /></button>
        </header>
        <fieldset disabled={saving} className="team-bulk-body">
          <section className="team-bulk-roles" aria-label={t('team.bulk.roles')}>
            <div className="team-bulk-roles__heading">
              <h3>{t('team.bulk.roles')}</h3>
              <button type="button" className="team-text-button" onClick={() => setSelected(selectedCount === eligible.length ? [] : eligible.map((role) => role.role))}>
                {t(selectedCount === eligible.length ? 'team.bulk.clearSelection' : 'team.bulk.selectAll')}
              </button>
            </div>
            <p>{t('team.bulk.scopeHint')}</p>
            <div className="team-bulk-roles__list">
              {groupTeamRoles(eligible).map((group) => <ResponsibilitySelection key={group.key} group={group} selected={selected} setSelected={setSelected} t={t} />)}
            </div>
          </section>
          <div className="team-bulk-settings">
            <RuntimeFields draft={draft} setDraft={setDraft} backendOptions={backendOptions} agents={agents} defaultProvider={defaultProvider} agentsLoading={agentsLoading} onRefresh={onRefresh} prompt={false} t={t} />
            <section className="team-bulk-preview" aria-label={t('team.bulk.preview')} aria-live="polite">
              <h3>{t('team.bulk.preview')}</h3>
              <p>{t('team.bulk.selectedCount', { count: selectedCount })}</p>
              <p>{t('team.bulk.changeCount', { updated: plan.updated, created: plan.created })}</p>
              <small>{t('team.bulk.preserveHint')}</small>
              {!selectedCount ? <p>{t('team.bulk.chooseRoles')}</p> : !plan.commands.length ? <p>{t('team.bulk.unchanged')}</p> : null}
            </section>
          </div>
        </fieldset>
        {error ? <div className="team-dialog__error"><ProductError error={error} t={t} /></div> : null}
        <footer className="team-dialog__footer">
          <Button variant="secondary" disabled={saving} onClick={onClose}>{t('team.dialog.cancel')}</Button>
          <Button type="submit" busy={saving} disabled={!plan.commands.length}>{t('team.bulk.save')}</Button>
        </footer>
      </form>
    </div>
  );
}
