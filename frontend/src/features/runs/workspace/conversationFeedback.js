// Presentation-only delivery state: it is not a task, approval, or execution status.
export function createConversationDelivery({ projectKey, runId = '', text, intentId = '', contextKey = '', previous }) {
  const reusable = !intentId && previous?.status === 'unconfirmed'
    && previous.projectKey === projectKey && previous.runId === runId
    && (previous.contextKey || '') === contextKey && !previous.intentId && previous.text === text;
  return {
    id: reusable ? previous.id : globalThis.crypto?.randomUUID?.() || `message-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    projectKey, runId, text, intentId, contextKey,
    timestamp: new Date().toISOString(),
    status: 'sending',
  };
}

export async function trackConversationDelivery({ delivery, operation, onUpdate }) {
  onUpdate(delivery);
  try {
    const result = await operation();
    if (result === false) {
      onUpdate({ ...delivery, status: 'unconfirmed' });
      return false;
    }
    const events = result?.intent_commit?.events || [];
    const recordedEvent = delivery.intentId
      ? events.findLast((event) => event.actor === 'user' && event.event === 'change_request_shared')
      : null;
    onUpdate({ ...delivery, status: 'recorded', serverMessageId: recordedEvent?.id || delivery.id });
    return result;
  } catch (error) {
    // A transport failure does not prove the server rejected the request.
    // Keep the same ID for an explicit resend; never auto-retry or auto-approve.
    onUpdate({ ...delivery, status: 'unconfirmed' });
    throw error;
  }
}

export function conversationTimeline(timeline, delivery, { projectKey, runId = '', readOnly = false }) {
  if (!delivery || readOnly || delivery.projectKey !== projectKey || delivery.runId !== runId) return timeline;
  const serverId = delivery.serverMessageId || delivery.id;
  const found = timeline.some((item) => item.id === serverId || item.message_id === serverId);
  if (found) {
    // A delayed response or explicit server-side retry may record its reply
    // after the transport already failed. Durable receipt wins over local UI.
    const hasReply = !delivery.intentId && timeline.some((item) => (
      item.actor !== 'user' && (item.id === `reply:${delivery.id}` || item.message_id === `reply:${delivery.id}`)
    ));
    if (delivery.status === 'recorded' || hasReply || !delivery.intentId) return timeline;
    return timeline.map((item) => item.id === serverId || item.message_id === serverId
      ? { ...item, delivery_status: delivery.status } : item);
  }
  return [...timeline, {
    id: delivery.id, actor: 'user', event: 'mission_requested', kind: 'intent',
    content: delivery.text, timestamp: delivery.timestamp, delivery_status: delivery.status,
  }];
}

export function shouldSubmitConversationOnEnter(event) {
  return event.key === 'Enter' && !event.shiftKey && !event.repeat
    && !event.isComposing && !event.nativeEvent?.isComposing
    && event.keyCode !== 229 && event.nativeEvent?.keyCode !== 229;
}

const DELIVERY_LABELS = {
  en: { sending: 'Sending…', recorded: 'Recorded', unconfirmed: 'Request not confirmed — check the error before retrying' },
  'zh-CN': { sending: '发送中…', recorded: '已记录', unconfirmed: '请求未确认，请查看错误后再重试' },
};

export function deliveryLabel(language, status) {
  return (DELIVERY_LABELS[language] || DELIVERY_LABELS.en)[status] || '';
}
