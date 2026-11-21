import { useRef, useState } from 'react';
import { runApi } from './runApi';
import { createConversationDelivery, trackConversationDelivery } from './workspace/conversationFeedback';

export default function useProjectConversation({ projectKey, refresh }) {
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
    const stop = ['stop_project_continuation', 'stop_manager_request'].includes(name);
    const request = stop ? stopping : pending;
    if (request.current || !stop && stopping.current) return false;
    request.current = true;
    if (!stop) setBusy(name);
    setError(null);
    let result;
    try {
      result = await operation();
      if (result?.accepted || stop) {
        // Durable admission releases the composer while the native turn runs.
        // Polling owns subsequent stream/finish updates; a slow GET is not a
        // reason to prevent another user message. The same applies once Stop
        // has durably acknowledged revocation.
        void refresh().catch(() => {});
      } else {
        await refresh();
      }
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

  const deliver = (name, text, operation, attachments = [], questionId = '') => {
    const next = createConversationDelivery({ projectKey, text,
      previous: questionId ? answerDeliveries.current.get(questionId) : delivery,
      contextKey: questionId ? `answer_question:${questionId}` : name, attachments });
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
      message_id: messageId, text, background: true,
      ...(attachments.length ? { attachments: attachments.map(item => item.id) } : {}),
    }), attachments),
    answer: (questionId, text) => {
      const message = `/answer ${questionId} ${text}`;
      return deliver('answer_question', message, (messageId) => runApi.sendTaskAssistantMessage(projectKey, {
        message_id: messageId, text: message,
      }), [], questionId);
    },
    queue: (text) => deliver('queue_task', text, (messageId) => runApi.taskAssistantAction(projectKey, {
      action: 'queue_task', message_id: messageId, text,
    })),
    act: (request) => perform(request.action, () => runApi.taskAssistantAction(projectKey,
      request.action === 'retry_message' ? { ...request, background: true } : request)),
  };
}
