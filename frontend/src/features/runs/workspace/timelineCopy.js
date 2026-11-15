/**
 * [INPUT]: 依赖时间线事件与 presentationUtils
 * [OUTPUT]: 提供timelineCopy、hasConversationContent
 * [POS]: 区分对话正文和状态文案，保留人工问题的回答入口
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { reviewGateName, statusText, translated } from './presentationUtils';

const CONVERSATION_CONTENT_EVENTS = new Set([
  'conversation_replied', 'intent_clarification_requested', 'project_question_answered',
  'project_inspection_started', 'task_proposed', 'task_revised', 'team_decision_proposed',
  'team_preference_recorded', 'change_intent_summarized', 'change_request_shared',
  'change_discussion_continued',
  'project_iteration_choice_recorded', 'implementation_completed',
]);

export function hasConversationContent(item) {
  return item.actor === 'user' || item.actor !== 'system' && CONVERSATION_CONTENT_EVENTS.has(item.event);
}

export function timelineCopy(item, t) {
  if (item.event === 'project_continuation_handoff' && item.details?.phase) {
    const notice = item.details;
    const questions = notice.human_questions || (notice.human_question_id && notice.human_question
      ? [{ ...notice.human_question, id: notice.human_question_id }] : []);
    const answerCommands = questions.map(question => `/answer ${question.id} <your answer>`);
    const lines = [translated(t, `work.manager.continuation.${notice.phase}`, t('work.manager.continuation.blocked'))];
    const reason = translated(t, `work.manager.continuation.reason.${notice.reason}`, '');
    if (reason) lines.push(reason);
    if (notice.delivered_task_ids?.length) {
      lines.push(t('work.manager.continuation.delivered', { tasks: notice.delivered_task_ids.join(', ') }));
    }
    if (notice.rationale) lines.push(t('work.manager.continuation.rationale', { rationale: notice.rationale }));
    questions.forEach((question, index) => {
      lines.push(t('work.manager.continuation.human_question', { question: question.question }));
      lines.push(t('work.manager.continuation.why_human', { reason: question.why_human }));
      lines.push(t('work.manager.continuation.answer_format', { command: `\`${answerCommands[index]}\`` }));
    });
    lines.push(t(`work.manager.continuation.next.${notice.phase}`));
    return lines.join('\n\n');
  }
  if (['project_continuation_authorized', 'project_continuation_batch_started'].includes(item.event)) {
    return t(`work.manager.events.${item.event}`, { tasks: (item.details?.task_ids || []).join(', '),
      batches: item.details?.max_batches ?? t('work.manager.continuation.no_limit'),
      calls: item.details?.max_manager_calls ?? t('work.manager.continuation.no_limit') });
  }
  if (item.actor === 'user') return item.content;
  if (item.content && ['intent_clarification_requested', 'project_question_answered'].includes(item.event)) return item.content;
  const details = item.details || {};
  return translated(
    t,
    `work.manager.events.${item.event}`,
    (hasConversationContent(item) && item.content) || translated(t, 'work.manager.events.progress', 'The team state changed.'),
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
