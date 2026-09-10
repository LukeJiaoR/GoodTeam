import { useState } from 'react';
import { Check, FilePlus, FolderOpen, Pencil, Play, Plus, Replace, Save, X } from 'lucide-react';
import Button from '../../../components/ui/Button';
import './assistantControls.css';

export function ProjectModeControl({ busy, onAction, t }) {
  const [mode, setMode] = useState('');
  return <section className="assistant-project-mode" aria-label={t('assistant.projectType')}>
    <fieldset disabled={Boolean(busy)}>
      <legend>{t('assistant.projectType')}</legend>
      <label><input type="radio" name="project-mode" checked={mode === 'new_project'} onChange={() => setMode('new_project')} />
        <FilePlus size={16} aria-hidden="true" />{t('assistant.newProject')}</label>
      <label><input type="radio" name="project-mode" checked={mode === 'existing_project'} onChange={() => setMode('existing_project')} />
        <FolderOpen size={16} aria-hidden="true" />{t('assistant.existingProject')}</label>
    </fieldset>
    <Button disabled={!mode || Boolean(busy)} onClick={() => onAction?.({ action: 'confirm_project_mode', mode })}>
      <Check size={14} aria-hidden="true" />{t('assistant.confirmMode')}
    </Button>
  </section>;
}

function PendingChange({ change, busy, resolve, t }) {
  if (!change) return null;
  return <div className="assistant-draft-change">
    <strong>{t(change.kind === 'withdraw' ? 'assistant.withdrawRequested' : 'assistant.changeRequested')}</strong>
    <blockquote>{change.text}</blockquote>
    <div className="assistant-control-actions">
      {change.kind !== 'withdraw' ? <>
        <Button disabled={Boolean(busy)} onClick={() => resolve('append')}><Plus size={14} aria-hidden="true" />{t('assistant.appendTask')}</Button>
        <Button disabled={Boolean(busy)} onClick={() => resolve('replace')}><Replace size={14} aria-hidden="true" />{t('assistant.replaceTask')}</Button>
      </> : null}
      <Button variant="secondary" disabled={Boolean(busy)} onClick={() => resolve('discard')}><X size={14} aria-hidden="true" />{t('assistant.keepDraft')}</Button>
    </div>
  </div>;
}

export default function TaskDraftControls({ draft, busy, modeRequired, resumeReady, readOnly, onAction, onConfirm, t }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft?.exact_request || '');
  if (!draft || draft.run_id || draft.status === 'cancelled') return null;
  const identity = { proposal_id: draft.id, expected_revision: draft.revision };
  const resolve = (choice) => onAction?.({ action: 'resolve_draft', ...identity, choice });
  const save = async () => {
    const result = await onAction?.({ action: 'edit_task', ...identity, text, message_id: globalThis.crypto.randomUUID() });
    if (result !== false) setEditing(false);
  };
  return <section className="team-task-proposal" aria-label={t('work.taskAssistant.proposal')}>
    <strong>{t('work.taskAssistant.proposal')} <small>{t('assistant.revision')} {draft.revision}</small></strong>
    {editing ? <label className="assistant-draft-editor">{t('assistant.taskDraft')}
      <textarea value={text} rows={5} disabled={Boolean(busy)} onChange={(event) => setText(event.target.value)} />
    </label> : <p className="assistant-draft-text">{draft.exact_request}</p>}
    {!readOnly ? <>
      <PendingChange change={draft.pending_change} busy={busy} resolve={resolve} t={t} />
      <div className="assistant-control-actions">
        {editing ? <>
          <Button disabled={Boolean(busy) || !text.trim()} onClick={save}><Save size={14} aria-hidden="true" />{t('assistant.saveDraft')}</Button>
          <Button variant="secondary" disabled={Boolean(busy)} onClick={() => setEditing(false)}><X size={14} aria-hidden="true" />{t('assistant.cancelEdit')}</Button>
        </> : <>
          <Button busy={busy === 'task-confirm'} disabled={Boolean(busy || (draft.confirmed && !resumeReady) || draft.pending_change || modeRequired)} onClick={() => onConfirm?.(draft.id, draft.revision)}>
            <Play size={14} aria-hidden="true" />{t(resumeReady ? 'assistant.resumeTask' : draft.confirmed ? 'work.taskAssistant.preparing' : 'work.taskAssistant.startTask')}
          </Button>
          {!draft.confirmed ? <>
            <Button variant="secondary" disabled={Boolean(busy)} onClick={() => { setText(draft.exact_request); setEditing(true); }}><Pencil size={14} aria-hidden="true" />{t('assistant.editTask')}</Button>
            <Button variant="secondary" disabled={Boolean(busy)} onClick={() => resolve('withdraw')}><X size={14} aria-hidden="true" />{t('assistant.withdrawDraft')}</Button>
          </> : null}
        </>}
      </div>
    </> : null}
  </section>;
}
