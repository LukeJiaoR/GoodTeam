import { useState } from 'react';
import { X } from 'lucide-react';
import Button from '../../../components/ui/Button';

function QueuedTask({ task, busy, canSelect, onAction, t }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(task.exact_request);
  const identity = { proposal_id: task.id, expected_revision: task.revision };
  const save = async () => {
    const result = await onAction({ action: 'edit_queued_task', ...identity, text, message_id: crypto.randomUUID() });
    if (result !== false) setEditing(false);
  };
  return <li className="project-queued-task">
    {editing ? <label>{t('assistant.taskDraft')}<textarea value={text} onChange={(event) => setText(event.target.value)} /></label>
      : <p>{task.exact_request}</p>}
    <div className="assistant-control-actions">
      {editing ? <>
        <Button disabled={busy || !text.trim()} onClick={save}>{t('assistant.saveDraft')}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => setEditing(false)}>{t('assistant.cancelEdit')}</Button>
      </> : <>
        <Button disabled={busy || !canSelect} onClick={() => onAction({ action: 'select_queued_task', ...identity })}>{t('assistant.project.selectTask')}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => { setText(task.exact_request); setEditing(true); }}>{t('assistant.editTask')}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => onAction({ action: 'discard_queued_task', ...identity })}>{t('assistant.withdrawDraft')}</Button>
      </>}
    </div>
  </li>;
}

export default function TaskNavigation({ onClose, assistant, busy, onAction, query = '', t }) {
  const queued = (assistant?.queued_tasks || []).filter(task =>
    !query || task.exact_request.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <nav id="project-task-navigation" className="project-task-navigation" tabIndex={-1} aria-label={t('assistant.project.tasks')} onKeyDown={event => {
    if (event.key === 'Escape' && onClose && globalThis.matchMedia?.('(max-width: 767px)').matches) {
      event.preventDefault();
      onClose(event);
    }
  }}>
    <div className="project-task-navigation__heading">
      <strong>{t('assistant.project.tasks')}</strong>
      {onClose ? <button type="button" className="project-task-navigation__close" aria-label={t('assistant.project.closeTasks')} onClick={onClose}><X size={16} aria-hidden="true" /></button> : null}
    </div>
    {query && !queued.length ? <p className="project-navigation-empty">{t('work.navigation.noMatches')}</p> : null}
    {queued.length ? <section className="project-navigation-queued" aria-label={t('assistant.project.queuedTasks')}>
      <h3>{t('assistant.project.queuedTasks')}</h3>
      <p className="product-empty-copy">{t('assistant.project.queueBoundary')}</p>
      <ul>{queued.map(task => <QueuedTask key={`${task.id}:${task.revision}`} task={task} busy={Boolean(busy)}
        canSelect={assistant.can_select_task} onAction={onAction} t={t} />)}</ul>
    </section> : null}
  </nav>;
}
