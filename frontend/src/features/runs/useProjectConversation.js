import { useRef, useState } from 'react';
import { runApi } from './runApi';
import { createConversationDelivery, trackConversationDelivery } from './workspace/conversationFeedback';

export default function useProjectConversation({ projectKey, contextRunId = '', refresh }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const [delivery, setDelivery] = useState(null);
  const pending = useRef(false);

  const perform = async (name, operation) => {
    if (pending.current) return false;
    pending.current = true;
    setBusy(name);
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
      pending.current = false;
      setBusy('');
    }
  };

  const deliver = (name, text, operation, intentId = '') => {
    const next = createConversationDelivery({ projectKey, text, intentId, previous: delivery,
      contextKey: `${name}:${contextRunId}` });
    return perform(name, () => trackConversationDelivery({
      delivery: next, operation: () => operation(next.id), onUpdate: setDelivery,
    }));
  };

  return {
    busy, error, delivery, deliver,
    send: (text) => deliver('message', text, (messageId) => runApi.sendTaskAssistantMessage(projectKey, {
      message_id: messageId, text, context_run_id: contextRunId,
    })),
    queue: (text) => deliver('queue_task', text, (messageId) => runApi.taskAssistantAction(projectKey, {
      action: 'queue_task', message_id: messageId, text,
    })),
    act: (request) => perform(request.action, () => runApi.taskAssistantAction(projectKey, request)),
  };
}
