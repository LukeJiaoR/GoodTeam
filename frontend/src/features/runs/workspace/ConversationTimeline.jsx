import { Bot } from 'lucide-react';
import MarkdownView from '../../../components/ui/MarkdownView';
import ProjectUnderstandingReply from './ProjectUnderstandingReply';
import { reviewGateName, statusText, translated } from './presentationUtils';
import { deliveryLabel } from './conversationFeedback';

function TeamAvatar({ label, manager = false }) {
  const initial = String(label || '').trim().slice(0, 1).toUpperCase() || 'G';
  return (
    <span aria-hidden="true" className={`team-avatar${manager ? ' team-avatar--manager' : ''}`}>
      {manager ? <Bot size={18} strokeWidth={1.9} /> : initial}
    </span>
  );
}

const SYSTEM_TIMELINE_EVENTS = new Set([
  'deliverable_ready',
  'verification_updated',
  'review_updated',
  'work_progressed',
  'work_dispatched',
  'progress',
  'mission_organized',
  'work_blocked',
  'attention_required',
  'work_retry_converged',
]);

function timelineCopy(item, t) {
  if (item.actor === 'user' && item.event === 'mission_requested') return item.content;
  if (item.content && ['intent_clarification_requested', 'project_question_answered'].includes(item.event)) return item.content;
  const details = item.details || {};
  return translated(
    t,
    `work.manager.events.${item.event}`,
    item.content || translated(t, 'work.manager.events.progress', 'The team state changed.'),
    {
      artifact: details.artifact || t('work.manager.deliverable'),
      decision: details.decision ? statusText(details.decision, t) : '',
      gate: details.gate ? reviewGateName(details.gate, t) : '',
      attemptCount: details.attempt_count || 0,
      taskCount: details.task_count || 0,
    },
  );
}

function TimelineItem({ item, language, t }) {
  const itemTimestamp = item.timestamp || item.created_at || '';
  const timestamp = Date.parse(itemTimestamp);
  const formatted = Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp))
    : '';
  const actorLabel = item.actor === 'user' ? t('work.manager.you') : t('work.manager.name');
  const copy = timelineCopy(item, t);
  const isSystem = item.actor === 'system' || (item.actor !== 'user' && SYSTEM_TIMELINE_EVENTS.has(item.event));

  if (isSystem) {
    return (
      <li className="team-manager-event team-manager-event--system" data-actor={item.actor} data-kind={item.kind} data-system="true">
        <div className="team-manager-system-chip">
          <span className="team-manager-system-chip__dot" aria-hidden="true" />
          <span className="team-manager-system-chip__text">{copy}</span>
          {formatted ? <time dateTime={itemTimestamp}>{formatted}</time> : null}
        </div>
      </li>
    );
  }

  return (
    <li className="team-manager-event" data-actor={item.actor} data-kind={item.kind} data-delivery={item.delivery_status}>
      <TeamAvatar label={actorLabel} manager={item.actor !== 'user'} />
      <div className="team-manager-event__bubble">
        <div className="team-manager-event__meta">
          <strong>{actorLabel}</strong>
          {item.delivery_status ? <span role="status">{deliveryLabel(language, item.delivery_status)}</span> : null}
          {formatted ? <time dateTime={itemTimestamp}>{formatted}</time> : null}
        </div>
        {item.actor === 'user' ? <p>{copy}</p> : <MarkdownView content={copy} />}
        {item.content && item.event !== 'mission_requested' && copy !== item.content ? (
          item.event === 'project_understanding_ready'
            ? <ProjectUnderstandingReply content={item.content} t={t} />
            : <MarkdownView content={item.content} />
        ) : null}
      </div>
    </li>
  );
}


export { TeamAvatar, TimelineItem };
