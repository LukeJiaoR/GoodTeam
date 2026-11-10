import { useRef, useState } from 'react';
import { runApi } from './runApi';
import { createConversationDelivery, trackConversationDelivery } from './workspace/conversationFeedback';

export default function useProjectConversation({ projectKey, contextRunId = '', refresh }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const [delivery, setDelivery] = useState(null);
  const pending = useRef(false);
  const stopping = useRef(false);
  // Keep unconfirmed request receipts per question: another answer or chat
  // message must not replace the identity needed for an explicit retry.
  const answerDeliveries = useRef(new Map());

  const perform = async (name, operation) => {
    // Revocation must remain reachable while an answer or chat request is
    // unresolved. It cannot release the ordinary mutation's pending latch.
    const stop = name === 'stop_project_continuation';
    const request = stop ? stopping : pending;
    if (request.current || !stop && stopping.current) return false;
    request.current = true;
    if (!stop) setBusy(name);
    setError(null);
    let result;
    try {
      result = await operation();
      await refresh();
      return result;
    } catch (caught) {
      setError(caught);
      // Failure feedback makes an explicit resend available immediately. A
      // background read must not hold the composer after the failure is shown.
      void refresh().catch(() => {});
      // A receipt survives a failed refresh. Never encourage a second POST.
      return result === undefined ? false : result;
    } finally {
      request.current = false;
      if (!stop) setBusy('');
    }
  };

  const deliver = (name, text, operation, intentId = '', attachments = [], questionId = '') => {
    const next = createConversationDelivery({ projectKey, text, intentId,
      previous: questionId ? answerDeliveries.current.get(questionId) : delivery,
      contextKey: questionId ? `answer_question:${questionId}` : `${name}:${contextRunId}`, attachments });
    return perform(name, () => trackConversationDelivery({
      delivery: next, operation: () => operation(next.id), onUpdate: updated => {
        setDelivery(updated);
        if (questionId && updated.status === 'unconfirmed') answerDeliveries.current.set(questionId, updated);
        if (questionId && updated.status === 'recorded') answerDeliveries.current.delete(questionId);
      },
    }));
  };

  return {
    busy, error, delivery, deliver,
    send: (text, attachments = []) => deliver('message', text, (messageId) => runApi.sendTaskAssistantMessage(projectKey, {
      message_id: messageId, text, context_run_id: contextRunId,
      ...(attachments.length ? { attachments: attachments.map(item => item.id) } : {}),
    }), '', attachments),
    answer: (questionId, text) => {
      const message = `/answer ${questionId} ${text}`;
      return deliver('answer_question', message, (messageId) => runApi.sendTaskAssistantMessage(projectKey, {
        message_id: messageId, text: message, context_run_id: '',
      }), '', [], questionId);
    },
    queue: (text) => deliver('queue_task', text, (messageId) => runApi.taskAssistantAction(projectKey, {
      action: 'queue_task', message_id: messageId, text,
    })),
    act: (request) => perform(request.action, () => runApi.taskAssistantAction(projectKey, request)),
  };
}
