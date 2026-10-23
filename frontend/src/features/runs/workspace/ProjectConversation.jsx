import { blockerSummary } from './blockerPresentation';
import { focusWorkContext } from './currentWorkContext.js';
import { useRef } from 'react';
import { readingChangeSummary, readingCopy, readingScope } from './conversationReading';
import useConversationReading from './useConversationReading';
import { timelineCopy } from './timelineCopy';
import { Settings } from 'lucide-react';
import ProductError from '../../../components/ui/ProductError';
import StatusBadge from '../../../components/ui/StatusBadge';
import { availableChangeTarget } from './conversationChangeContext';
import { TeamAvatar, TimelineItem } from './ConversationTimeline';
import { TaskAttention, ProjectPreparationAttention } from './TaskAttention';
import AssistantControls from './AssistantControls';
import AssistantPendingReply from './AssistantPendingReply';
import ConversationComposer from './ConversationComposer';
import TaskDraftControls, { ProjectModeControl } from './TaskDraftControls';

function ConversationReadingFrame({ scope, timeline, attentionKey, deliveryKey, assistantFocusKey, visible, language, hasPendingItems, t, children }) {
  const conversationRef = useRef(null);
  const reading = useConversationReading({ scope, conversationRef, timeline, attentionKey, deliveryKey, assistantFocusKey, visible });
  const copy = readingCopy(language);
  const sourceDescription = item => {
    const text = String(timelineCopy(item, t) || '').replace(/\s+/g, ' ').trim();
    return text.length > 140 ? `${text.slice(0, 139)}…` : text;
  };
  const viewPendingItems = () => {
    if (hasPendingItems) reading.inspect(conversationRef.current?.querySelector('.project-conversation-attention'));
  };
  return <>
      {reading.paused ? <div className="project-conversation-follow" role="region" aria-label={copy.label}>
        <div className="project-conversation-follow__summary">
          <span>{t(hasPendingItems ? 'assistant.project.readingPending' : 'assistant.project.readingHistory')}</span>
          {reading.unread.items.length ? <span className="project-conversation-follow__changes">
            <strong>{copy.unread(reading.unread.items.length)}</strong>
            <span>{readingChangeSummary(reading.unread.items, language)}</span>
          </span> : null}
          {reading.unread.items.length ? <ol className="project-conversation-follow__events">
            {reading.unread.items.slice(0, 3).map(item => <li key={item.id || item.message_id}>
              <button type="button" onClick={() => reading.inspectEvent(item)}>{sourceDescription(item)}</button>
            </li>)}
          </ol> : null}
          {hasPendingItems && reading.attentionKey !== attentionKey ? <span>{copy.changed}</span> : null}
          {reading.stale || reading.unread.unavailable ? <span>{reading.stale ? copy.stale : copy.unavailable}</span> : null}
        </div>
        {reading.unread.items.length ? <button type="button" onClick={reading.readNewUpdates}>{copy.open}</button> : null}
        {hasPendingItems ? <button type="button" onClick={viewPendingItems}>{t('assistant.project.viewPending')}</button> : null}
        <button type="button" onClick={reading.returnToLatest}>{t('assistant.project.returnLatest')}</button>
      </div> : null}
      <div className="team-manager-conversation" onScroll={reading.handleScroll} ref={conversationRef} tabIndex={-1}>
        {children}
      </div>
  </>;
}

export default function ProjectConversation({
  onConfigureRuntime, canManageTasks = false, executionRunning = false,
  taskManagementOpen = false, onManageTasks,
  assistantState,
  onAssistantAction,
  attention, acceptedMessage,
  busy,
  assistantBusy = busy,
  error,
  language,
  mission,
  onConfirmChangeIntent,
  onDecide,
  onDiscussChangeIntent,
  onKeepDiscussingChangeIntent,
  onReview,
  onRetry,
  retry,
  onConfirmTask,
  onContinuePreparation,
  onStart,
  onStartChangeIntent,
  projectKey,
  sectionProps,
  timeline,
  t,
  suggestions,
  proposedTask,
  onQueueTask,
  contextTitle,
  onClearContext,
}) {
  const requestStatus = Object.values(assistantState?.assistant_requests || {}).find((request) => request.status !== 'succeeded')?.status;
  const responding = ['message', 'retry_message'].includes(assistantBusy) || assistantState?.status === 'running' || requestStatus === 'running';
  const needsAttention = ['failed', 'interrupted'].includes(requestStatus)
    || ['pending', 'stale', 'failed'].includes(assistantState?.pending_team_decision?.status);
  const managerTone = responding ? 'active' : needsAttention ? 'warning' : 'neutral';
  const managerStatus = t(responding ? 'assistant.project.responding' : needsAttention ? 'assistant.project.needsAttention' : 'assistant.project.available');
  const deliveryKey = (timeline || []).find((item) => item.delivery_status === 'sending')?.id || '';
  const scope = readingScope(projectKey, mission);
  const hasPendingItems = Boolean((attention && !(attention.kind === 'blocker' && executionRunning))
    || needsAttention || Object.values(assistantState?.assistant_requests || {}).some(request => ['failed', 'interrupted'].includes(request.status))
    || (proposedTask?.needs_clarification && !proposedTask.run_id && proposedTask.status !== 'cancelled'));
  const attentionKey = JSON.stringify([
    hasPendingItems ? [attention?.kind || '', attention?.id || '', attention?.intent_commit?.status || ''] : [],
    assistantState?.pending_team_decision ? [assistantState.pending_team_decision.id, assistantState.pending_team_decision.status] : [],
    Object.entries(assistantState?.assistant_requests || {}).filter(([, request]) => ['failed', 'interrupted'].includes(request.status))
      .map(([id, request]) => [id, request.status, request.attempt]).sort(),
    proposedTask?.needs_clarification ? [proposedTask.id, proposedTask.revision] : [],
  ]);

  return (
    <section className="team-pane team-pane--manager" aria-label={t('work.manager.name')} {...sectionProps}>
      <header className="team-pane__header">
        <div className="team-manager-heading">
          <TeamAvatar label={t('work.manager.name')} manager />
          <div>
            <div className="team-manager-heading__title">
              <h2 id="team-manager-title">{t('work.manager.name')}</h2>
              <StatusBadge tone={managerTone}>{managerStatus}</StatusBadge>
            </div>
            <span className="team-pane__eyebrow">{t('work.manager.subtitle')}</span>
          </div>
        </div>
        {onConfigureRuntime ? <button type="button" className="project-assistant-settings"
          aria-label={t('assistant.configure')} title={t('assistant.configure')} onClick={onConfigureRuntime}>
          <Settings size={18} aria-hidden="true" />
        </button> : null}
      </header>
      <div className="project-conversation-scope">
        {canManageTasks ? <button type="button" aria-expanded={taskManagementOpen} aria-controls="project-task-navigation" onClick={event => {
          onManageTasks?.();
          focusWorkContext(event.currentTarget.closest('.product-work-page'), onManageTasks ? 'tasks' : 'execution');
        }}>{t('work.manager.manageTasks')}</button> : null}
        <span>{contextTitle ? t('assistant.project.taskContext', { title: contextTitle }) : t('assistant.project.projectContext')}</span>
        {contextTitle && onClearContext ? <button type="button" onClick={onClearContext}>{t('assistant.project.clearContext')}</button> : null}
      </div>
      <ConversationReadingFrame key={scope} scope={scope} timeline={timeline || []} attentionKey={attentionKey}
        deliveryKey={deliveryKey} assistantFocusKey={`${assistantState?.pending_team_decision?.id || ''}:${requestStatus || ''}`}
        visible={!sectionProps?.hidden} language={language} hasPendingItems={hasPendingItems} t={t}>
        <>
            {error && !(error.code === 'manager_execution_failed' && requestStatus) ? <ProductError error={error} t={t} /> : null}
            <ol className="team-manager-timeline" aria-label={t('assistant.project.history')}>
              {(timeline || []).map((item) => <TimelineItem item={item} key={item.id || item.message_id} language={language} t={t} />)}
              {Object.entries(assistantState?.assistant_requests || {}).filter(([, request]) => request.status === 'running').map(([id, request]) => (
                <AssistantPendingReply key={`${id}:${request.attempt}`} request={request} t={t} />
              ))}
            </ol>
            <div className="project-conversation-attention" tabIndex={-1} role="region" aria-label={t('assistant.project.viewPending')}>
            <AssistantControls
              showRuntime={false}
              runtime={assistantState?.runtime}
              requests={assistantState?.assistant_requests}
              pending={assistantState?.pending_team_decision}
              busy={assistantBusy}
              readOnly={false}
              onAction={onAssistantAction}
              projectKey={projectKey}
              t={t}
            />
            {attention?.kind === 'project_mode' && !mission?.read_only ? (
              <ProjectModeControl busy={busy} onAction={onAssistantAction} t={t} />
            ) : null}
            <TaskDraftControls
              key={`${proposedTask?.id}:${proposedTask?.revision}`}
              draft={proposedTask}
              busy={busy || assistantBusy || Object.values(assistantState?.assistant_requests || {}).some((request) => request.status === 'running')}
              modeRequired={attention?.kind === 'project_mode'}
              resumeReady={attention?.kind === 'task_start_resume'}
              readOnly={mission?.read_only}
              onAction={onAssistantAction}
              onConfirm={onConfirmTask}
              t={t}
            />
            {attention?.kind === 'approval' ? (
              <div className="conversation-blocker-notice">
                <span>{t('work.approvalCard.pending')}</span>
                <button type="button" onClick={event => focusWorkContext(event.currentTarget.closest('.product-work-page'), 'approval')}>{t('work.approvalCard.view')}</button>
              </div>
            ) : attention?.kind === 'blocker' ? (
              <div className="conversation-blocker-notice" role="status">
                <span>{executionRunning ? t('work.blocker.workerActive') : blockerSummary(attention, t)}</span>
                <button type="button" onClick={(event) => focusWorkContext(event.currentTarget.closest('.product-work-page'), 'blocker')}>{t('work.blocker.view')}</button>
              </div>
            ) : attention?.kind === 'project_preparation' ? (
              <ProjectPreparationAttention
                attention={attention}
                busy={busy}
                key={attention.id}
                onContinue={onContinuePreparation}
                t={t}
              />
            ) : !['project_mode', 'task_start_resume'].includes(attention?.kind) ? <TaskAttention
              attention={attention}
              busy={busy}
              key={`${attention?.id || 'none'}:${attention?.intent_commit?.id || 'direct'}:${attention?.intent_commit?.status || 'idle'}`}
              onConfirmChangeIntent={onConfirmChangeIntent}
              onDecide={onDecide}
              onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent}
              onReview={onReview}
              onRetry={onRetry}
              retry={retry}
              onStartChangeIntent={onStartChangeIntent}
              projectKey={projectKey}
              t={t}
            /> : null}
            </div>
        </>
      </ConversationReadingFrame>
      {!mission?.read_only || onStart ? (
        <footer className="team-manager-dock">
          <ConversationComposer
            acceptedMessage={acceptedMessage}
            awaitingReply={Object.values(assistantState?.assistant_requests || {}).some(request => request.status === 'running')}
            key={projectKey}
            draftKey={JSON.stringify([projectKey, 'project-conversation'])}
            busy={Boolean(assistantBusy) || Object.values(assistantState?.assistant_requests || {}).some((request) => request.status === 'running')}
            onStart={onStart}
            onQueueTask={onQueueTask}
            changeIntent={mission?.read_only ? null : attention?.intent_commit}
            activeTask={!mission?.read_only && mission?.source === 'workflow' && Boolean(mission.run_id)}
            changeTarget={availableChangeTarget({ mission, attention, projectKey })}
            recordedChange={(timeline || []).filter((item) => item.event === 'change_request_committed'
              && (!item.run_id || item.run_id === mission?.run_id)
              && item.details?.intent_status === 'committed').at(-1)}
            onStartChangeIntent={onStartChangeIntent}
            onDiscussChangeIntent={onDiscussChangeIntent}
            onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent}
            suggestions={suggestions}
            t={t}
          />
        </footer>
      ) : null}
    </section>
  );
}
