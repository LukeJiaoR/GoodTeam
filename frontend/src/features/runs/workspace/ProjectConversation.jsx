import { blockerSummary } from './blockerPresentation';
import { focusWorkContext } from './currentWorkContext';
import { useEffect, useRef } from 'react';
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

const NEAR_BOTTOM_THRESHOLD = 64;

function isNearBottom(element) {
  if (!element) return true;
  const { scrollTop, scrollHeight, clientHeight } = element;
  return scrollHeight - scrollTop - clientHeight <= NEAR_BOTTOM_THRESHOLD;
}

export default function ProjectConversation({
  onConfigureRuntime, canManageTasks = false, executionRunning = false,
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
  const conversationRef = useRef(null);
  const wasNearBottomRef = useRef(true);
  const prevAttentionKeyRef = useRef(null);
  const prevTimelineLengthRef = useRef(0);
  const prevDeliveryKeyRef = useRef('');

  const attentionKey = `${attention?.id || ''}:${attention?.intent_commit?.status || ''}`;

  useEffect(() => {
    const node = conversationRef.current;
    if (!node) return;

    const attentionChanged = prevAttentionKeyRef.current !== attentionKey;
    const timelineChanged = prevTimelineLengthRef.current !== (timeline?.length || 0);
    const deliveryChanged = prevDeliveryKeyRef.current !== deliveryKey;
    prevDeliveryKeyRef.current = deliveryKey;

    prevAttentionKeyRef.current = attentionKey;
    prevTimelineLengthRef.current = timeline?.length || 0;

    if ((attention && attentionChanged) || (deliveryKey && deliveryChanged)) {
      node.scrollTop = node.scrollHeight;
      wasNearBottomRef.current = true;
    } else if (timelineChanged && wasNearBottomRef.current) {
      node.scrollTop = node.scrollHeight;
    }
  }, [attention, attentionKey, deliveryKey, timeline?.length]);

  useEffect(() => {
    const node = conversationRef.current;
    const target = node?.querySelector('.assistant-team-decision, .assistant-request, .assistant-pending-reply');
    if (target) node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - 16;
  }, [assistantState?.pending_team_decision?.id, requestStatus]);

  const handleConversationScroll = (event) => {
    const node = event.currentTarget;
    wasNearBottomRef.current = isNearBottom(node);
  };

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
        {canManageTasks ? <button type="button" onClick={event => focusWorkContext(event.currentTarget.closest('.product-work-page'), 'execution')}>{t('work.manager.manageTasks')}</button> : null}
        <span>{contextTitle ? t('assistant.project.taskContext', { title: contextTitle }) : t('assistant.project.projectContext')}</span>
        {contextTitle && onClearContext ? <button type="button" onClick={onClearContext}>{t('assistant.project.clearContext')}</button> : null}
      </div>
      <div className="team-manager-conversation" onScroll={handleConversationScroll} ref={conversationRef}>
        <>
            {error && !(error.code === 'manager_execution_failed' && requestStatus) ? <ProductError error={error} t={t} /> : null}
            <ol className="team-manager-timeline" aria-label={t('assistant.project.history')}>
              {(timeline || []).map((item) => <TimelineItem item={item} key={item.id || item.message_id} language={language} t={t} />)}
              {Object.entries(assistantState?.assistant_requests || {}).filter(([, request]) => request.status === 'running').map(([id, request]) => (
                <AssistantPendingReply key={`${id}:${request.attempt}`} request={request} t={t} />
              ))}
            </ol>
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
              onStartChangeIntent={onStartChangeIntent}
              projectKey={projectKey}
              t={t}
            /> : null}
        </>
      </div>
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
