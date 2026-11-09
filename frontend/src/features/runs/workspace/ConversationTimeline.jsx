import {
  AlertCircle,
  AlertTriangle,
  Bot,
  ChevronDown,
  FileCheck2,
  GitPullRequest,
  Layers,
  RotateCcw,
  ShieldCheck,
  Terminal,
} from 'lucide-react';
import { timelineReadingId } from './conversationReading';
import MarkdownView from '../../../components/ui/MarkdownView';
import ProjectUnderstandingReply from './ProjectUnderstandingReply';
import { timelineCopy } from './timelineCopy';
import { deliveryLabel } from './conversationFeedback';
import { focusWorkContext } from './currentWorkContext';
import { AttachmentList } from './ConversationAttachments';

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
  'work_retry_blocked',
  'work_retry_ambiguous',
  'team_decision_failed',
]);

function getSystemEventMeta(event, kind) {
  switch (event) {
    case 'work_dispatched':
    case 'work_progressed':
    case 'mission_organized':
      return { Icon: Terminal, tone: 'info', category: 'dispatch' };
    case 'verification_updated':
      return { Icon: ShieldCheck, tone: 'success', category: 'verification' };
    case 'review_updated':
      return { Icon: FileCheck2, tone: 'purple', category: 'review' };
    case 'deliverable_ready':
    case 'implementation_completed':
      return { Icon: GitPullRequest, tone: 'success', category: 'deliverable' };
    case 'work_retried':
    case 'work_retry_converged':
    case 'work_retry_in_progress':
      return { Icon: RotateCcw, tone: 'accent', category: 'retry' };
    case 'work_blocked':
    case 'work_retry_blocked':
    case 'work_retry_ambiguous':
    case 'attention_required':
    case 'team_decision_failed':
      return { Icon: AlertTriangle, tone: 'warning', category: 'blocked' };
    default:
      if (kind === 'outcome') return { Icon: GitPullRequest, tone: 'success', category: 'deliverable' };
      if (kind === 'attention') return { Icon: AlertCircle, tone: 'warning', category: 'blocked' };
      return { Icon: Layers, tone: 'neutral', category: 'progress' };
  }
}

function extractSystemDetails(item, copy) {
  const details = item.details || {};
  const entries = [];
  if (details.phase) entries.push({ label: 'Phase', value: String(details.phase) });
  if (details.transition) entries.push({ label: 'Transition', value: String(details.transition) });
  if (details.pr_number) entries.push({ label: 'PR', value: `#${details.pr_number}` });
  if (details.decision) entries.push({ label: 'Decision', value: String(details.decision) });
  if (details.gate) entries.push({ label: 'Gate', value: String(details.gate) });
  if (details.ci_state) entries.push({ label: 'CI', value: String(details.ci_state) });
  if (details.artifact) entries.push({ label: 'Artifact', value: String(details.artifact) });
  if (Array.isArray(details.task_ids) && details.task_ids.length > 0) {
    entries.push({ label: 'Tasks', value: details.task_ids.join(', ') });
  }
  if (Array.isArray(item.refs) && item.refs.length > 0) {
    entries.push({ label: 'Refs', value: item.refs.join(', ') });
  }
  if (details.attempt_count) entries.push({ label: 'Attempts', value: String(details.attempt_count) });
  if (details.retry_count) entries.push({ label: 'Retries', value: String(details.retry_count) });
  const extraContent = (item.content && item.content.trim() && item.content.trim() !== String(copy || '').trim())
    ? item.content : null;
  return { entries, extraContent, hasDetails: entries.length > 0 || Boolean(extraContent) };
}

function SystemTimelineItem({ item, copy, metadata }) {
  const meta = getSystemEventMeta(item.event, item.kind);
  const Icon = meta.Icon;
  const { entries, extraContent, hasDetails } = extractSystemDetails(item, copy);

  const chipContent = (
    <div className="team-manager-system-chip" data-tone={meta.tone} data-category={meta.category}>
      <span className="team-manager-system-chip__icon" aria-hidden="true">
        <Icon size={12} strokeWidth={2.2} />
      </span>
      <span className="team-manager-system-chip__text">{copy}</span>
      {item.details?.pr_number ? (
        <span className="team-system-tag">#{item.details.pr_number}</span>
      ) : null}
      {metadata}
      {hasDetails ? (
        <span className="team-system-card__toggle" aria-hidden="true">
          <ChevronDown size={12} strokeWidth={2.2} />
        </span>
      ) : null}
    </div>
  );

  if (!hasDetails) {
    return (
      <li data-reading-id={timelineReadingId(item)} tabIndex={-1} className="team-manager-event team-manager-event--system" data-actor={item.actor} data-kind={item.kind} data-system="true">
        {chipContent}
      </li>
    );
  }

  return (
    <li data-reading-id={timelineReadingId(item)} tabIndex={-1} className="team-manager-event team-manager-event--system team-manager-event--has-details" data-actor={item.actor} data-kind={item.kind} data-system="true">
      <details className="team-system-card">
        <summary className="team-system-card__summary">
          {chipContent}
        </summary>
        <div className="team-system-card__body">
          {entries.length > 0 ? (
            <dl className="team-system-card__params">
              {entries.map(({ label, value }) => (
                <div key={label} className="team-system-card__param">
                  <dt>{label}</dt>
                  <dd title={value}>{value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {extraContent ? (
            <pre className="team-system-card__output">{extraContent}</pre>
          ) : null}
        </div>
      </details>
    </li>
  );
}

function TimelineItem({ item, language, t, continuationNoticeId }) {
  const itemTimestamp = item.timestamp || item.created_at || '';
  const timestamp = Date.parse(itemTimestamp);
  const formatted = Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp))
    : '';
  const timeOnly = Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit' }).format(new Date(timestamp))
    : '';
  const actorLabel = item.actor === 'user' ? t('work.manager.you') : t('work.manager.name');
  const copy = timelineCopy(item, t);
  const isContinuationHandoff = item.event === 'project_continuation_handoff' && item.details?.phase;
  const isUnderstanding = ['project_understanding_ready', 'project_understanding_incomplete'].includes(item.event);
  const isSystem = item.actor === 'system' || (item.actor !== 'user' && SYSTEM_TIMELINE_EVENTS.has(item.event));
  const metadata = formatted ? (
    <div className="conversation-message-metadata">
      <time className="conversation-message-time" dateTime={itemTimestamp} title={`${actorLabel} · ${formatted}`}>{timeOnly || formatted}</time>
    </div>
  ) : null;
  const hasDoubleContent = Boolean(item.content && copy && !isContinuationHandoff && copy !== item.content);

  if (isSystem) {
    return (
      <SystemTimelineItem
        item={item}
        copy={copy}
        metadata={metadata}
      />
    );
  }

  return (
    <li data-reading-id={timelineReadingId(item)} tabIndex={-1} className="team-manager-event" data-actor={item.actor} data-kind={item.kind} data-delivery={item.delivery_status}>
      <div className="team-manager-event__bubble">
        <strong className="sr-only">{actorLabel}</strong>
        <AttachmentList files={item.attachments} t={t} />
        {hasDoubleContent ? (
          <div className="team-manager-event__note">{copy}</div>
        ) : (
          <MarkdownView content={copy} interactive language={language} />
        )}
        {isUnderstanding ? <ProjectUnderstandingReply content={item.content} scan={item.details?.inventory_scan}
          incomplete={item.event === 'project_understanding_incomplete'} t={t} /> : null}
        {hasDoubleContent ? (
          <MarkdownView content={item.content} interactive language={language} />
        ) : null}
        {isContinuationHandoff && continuationNoticeId && item.details?.id === continuationNoticeId ? <button type="button"
          onClick={event => focusWorkContext(event.currentTarget.closest('.product-work-page'), 'continuation', {
            matches: node => node.dataset.noticeId === continuationNoticeId,
          })}>{t('work.manager.continuation.open')}</button> : null}
        <div className="team-manager-event__meta">
          {item.delivery_status ? <span role="status">{deliveryLabel(language, item.delivery_status)}</span> : null}
          {metadata}
        </div>
      </div>
    </li>
  );
}

export { TeamAvatar, TimelineItem };
