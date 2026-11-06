import { useState } from 'react';
import { MessageSquare, X } from 'lucide-react';
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

export default function TaskNavigation({ currentRunId, currentTitle, currentStatus, currentBlocked = false, runs, selectedRunId, onSelect, onClose, assistant, busy, onAction, query = '', t }) {
  const [showAll, setShowAll] = useState(false);
  const queued = assistant?.queued_tasks || [];
  const title = run => String(run.title || run.idea || run.run_id).split('\n')[0].slice(0, 120);
  const matches = run => !query || title(run).toLocaleLowerCase().includes(query.toLocaleLowerCase());
  const current = currentRunId ? runs.find(run => run.run_id === currentRunId)
    || { run_id: currentRunId, title: currentTitle, status: currentStatus || 'active' } : null;
  const history = runs.filter(run => run.run_id !== currentRunId && matches(run));
  const visibleHistory = showAll || query ? history : history.slice(0, 3);
  const row = run => <li key={run.run_id} data-state={run.status}>
    <button type="button" aria-current={selectedRunId === run.run_id ? 'page' : undefined} onClick={() => onSelect(run.run_id)} title={title(run)}>
      <span>{title(run)}</span><small>{run.status === 'archived' ? t('assistant.project.archived') : currentBlocked && run.run_id === currentRunId ? t('work.blocker.title') : statusText(run.run_id === currentRunId ? currentStatus || run.status : run.status, t)}</small>
    </button>
  </li>;
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
    <button className="project-conversation-link" type="button" aria-current={!selectedRunId ? 'page' : undefined} onClick={() => onSelect('')}><MessageSquare size={13} aria-hidden="true" />{t('assistant.project.projectContext')}</button>
    {current && matches(current) ? <section aria-label={t('work.navigation.current')}><h3>{t('work.navigation.current')}</h3><ul>{row(current)}</ul></section> : null}
    {history.length ? <section aria-label={t('work.navigation.history')}><h3>{t('work.navigation.history')}</h3><ul>{visibleHistory.map(row)}</ul>
      {history.length > 3 && !query ? <button type="button" className="project-history-more" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>{t(showAll ? 'work.navigation.showLess' : 'work.navigation.showMore')}</button> : null}
    </section> : null}
    {query && !history.length && !(current && matches(current)) ? <p className="project-navigation-empty">{t('work.navigation.noMatches')}</p> : null}
    {queued.length ? <section className="project-navigation-queued" aria-label={t('assistant.project.queuedTasks')}>
      <h3>{t('assistant.project.queuedTasks')}</h3>
      <p className="product-empty-copy">{t('assistant.project.queueBoundary')}</p>
      <ul>{queued.map(task => <QueuedTask key={`${task.id}:${task.revision}`} task={task} busy={Boolean(busy)}
        canSelect={assistant.can_select_task} onAction={onAction} t={t} />)}</ul>
    </section> : null}
  </nav>;
}
