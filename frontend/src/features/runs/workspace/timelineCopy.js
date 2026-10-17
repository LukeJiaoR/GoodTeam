import { reviewGateName, statusText, translated } from './presentationUtils';

export function timelineCopy(item, t) {
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
      retryCount: details.retry_count ?? Math.max(0, (details.attempt_count || 0) - 1),
      taskCount: details.task_count || 0,
    },
  );
}
