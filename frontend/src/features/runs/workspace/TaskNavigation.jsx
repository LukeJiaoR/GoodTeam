import { useState } from 'react';
import { X } from 'lucide-react';
import Button from '../../../components/ui/Button';
import { statusText } from './presentationUtils';

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

export default function TaskNavigation({ currentRunId, currentBlocked = false, runs, selectedRunId, onSelect, onClose, assistant, busy, onAction, t }) {
  const queued = assistant?.queued_tasks || [];
  return <nav id="project-task-navigation" className="project-task-navigation" aria-label={t('assistant.project.tasks')}>
    <div className="project-task-navigation__heading">
      <strong>{t('work.manager.manageTasks')}</strong>
      {onClose ? <button type="button" className="project-task-navigation__close" aria-label={t('assistant.project.closeTasks')} onClick={onClose}><X size={16} aria-hidden="true" /></button> : null}
    </div>
    <button type="button" aria-current={!selectedRunId ? 'page' : undefined} onClick={() => onSelect('')}>{t('assistant.project.projectContext')}</button>
    {runs.length ? <ul>{runs.map((run) => <li key={run.run_id}>
      <button type="button" aria-current={selectedRunId === run.run_id ? 'page' : undefined} onClick={() => onSelect(run.run_id)}>
        <span title={run.idea}>{String(run.title || run.idea || run.run_id).split('\n')[0].slice(0, 120)}</span><small>{run.status === 'archived' ? t('assistant.project.archived') : currentBlocked && run.run_id === currentRunId ? t('work.blocker.title') : statusText(run.status, t)}</small>
      </button>
    </li>)}</ul> : null}
    {queued.length ? <section aria-label={t('assistant.project.queuedTasks')}>
      <h3>{t('assistant.project.queuedTasks')}</h3>
      <p className="product-empty-copy">{t('assistant.project.queueBoundary')}</p>
      <ul>{queued.map((task) => <QueuedTask key={`${task.id}:${task.revision}`} task={task} busy={Boolean(busy)}
        canSelect={assistant.can_select_task} onAction={onAction} t={t} />)}</ul>
    </section> : null}
  </nav>;
}
