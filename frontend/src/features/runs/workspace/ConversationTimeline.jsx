import { Bot, Ellipsis } from 'lucide-react';
import { timelineReadingId } from './conversationReading';
import MarkdownView from '../../../components/ui/MarkdownView';
import ProjectUnderstandingReply from './ProjectUnderstandingReply';
import { timelineCopy } from './timelineCopy';
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


function TimelineItem({ item, language, t }) {
  const itemTimestamp = item.timestamp || item.created_at || '';
  const timestamp = Date.parse(itemTimestamp);
  const formatted = Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp))
    : '';
  const actorLabel = item.actor === 'user' ? t('work.manager.you') : t('work.manager.name');
  const copy = timelineCopy(item, t);
  const isSystem = item.actor === 'system' || (item.actor !== 'user' && SYSTEM_TIMELINE_EVENTS.has(item.event));
  const metadata = formatted ? <details className="conversation-message-details">
    <summary aria-label={`${actorLabel} · ${formatted}`} title={formatted}><Ellipsis size={16} aria-hidden="true" /></summary>
    <div><span>{actorLabel}</span><time dateTime={itemTimestamp}>{formatted}</time></div>
  </details> : null;

  if (isSystem) {
    return (
      <li data-reading-id={timelineReadingId(item)} tabIndex={-1} className="team-manager-event team-manager-event--system" data-actor={item.actor} data-kind={item.kind} data-system="true">
        <div className="team-manager-system-chip">
          <span className="team-manager-system-chip__dot" aria-hidden="true" />
          <span className="team-manager-system-chip__text">{copy}</span>
          {metadata}
        </div>
      </li>
    );
  }

  return (
    <li data-reading-id={timelineReadingId(item)} tabIndex={-1} className="team-manager-event" data-actor={item.actor} data-kind={item.kind} data-delivery={item.delivery_status}>
      <TeamAvatar label={actorLabel} manager={item.actor !== 'user'} />
      <div className="team-manager-event__bubble">
        <strong className="sr-only">{actorLabel}</strong>
        {item.actor === 'user' ? <p>{copy}</p> : <MarkdownView content={copy} interactive language={language} />}
        {item.content && item.event !== 'mission_requested' && copy !== item.content ? (
          ['project_understanding_ready', 'project_understanding_incomplete'].includes(item.event)
            ? <ProjectUnderstandingReply content={item.content} scan={item.details?.inventory_scan} t={t} />
            : <MarkdownView content={item.content} interactive language={language} />
        ) : null}
        <div className="team-manager-event__meta">
          {item.delivery_status ? <span role="status">{deliveryLabel(language, item.delivery_status)}</span> : null}
          {metadata}
        </div>
      </div>
    </li>
  );
}


export { TeamAvatar, TimelineItem };
