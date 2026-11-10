import { reviewGateName, statusText, translated } from './presentationUtils';

export function timelineCopy(item, t) {
  if (item.event === 'project_continuation_handoff' && item.details?.phase) {
    const notice = item.details;
    const questions = notice.human_questions || (notice.human_question_id && notice.human_question
      ? [{ ...notice.human_question, id: notice.human_question_id }] : []);
    const answerCommands = questions.map(question => `/answer ${question.id} <your answer>`);
    const lines = [t(`work.manager.continuation.${notice.phase}`),
      t('work.manager.continuation.reason', { reason: translated(t, `work.manager.continuation.reason.${notice.reason}`, notice.reason) }),
      t('work.manager.continuation.budget', { batches: `${notice.batches_started}/${notice.max_batches}`,
        calls: `${notice.manager_calls}/${notice.max_manager_calls}` })];
    if (notice.delivered_task_ids?.length) {
      lines.push(t('work.manager.continuation.delivered', { tasks: notice.delivered_task_ids.join(', ') }));
      lines.push(t('work.manager.continuation.receipts', { receipts: notice.delivery_receipt_ids.join(', ') }));
    }
    if (notice.rationale) lines.push(t('work.manager.continuation.rationale', { rationale: notice.rationale }));
    questions.forEach((question, index) => {
      lines.push(t('work.manager.continuation.human_question', { question: question.question }));
      lines.push(t('work.manager.continuation.why_human', { reason: question.why_human }));
      lines.push(t('work.manager.continuation.answer_format', { command: `\`${answerCommands[index]}\`` }));
    });
    lines.push(t(`work.manager.continuation.next.${notice.phase}`));
    if (notice.next_action && !answerCommands.includes(notice.next_action)) lines.push(notice.next_action);
    return lines.join('\n\n');
  }
  if (['project_continuation_authorized', 'project_continuation_batch_started'].includes(item.event)) {
    return t(`work.manager.events.${item.event}`, { tasks: (item.details?.task_ids || []).join(', '),
      batches: item.details?.max_batches, calls: item.details?.max_manager_calls });
  }
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
