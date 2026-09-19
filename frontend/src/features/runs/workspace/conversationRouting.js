// Scope is explicit UI context; it is never an authorization to execute work.
export function isChangeDiscussion(intent) {
  return Boolean(intent?.id && ['discussing', 'ready_to_confirm'].includes(intent.status));
}

export async function sendConversationInput({ text, intent, onStart, onDiscuss, onKeepDiscussing }) {
  if (!text.trim()) return false;
  if (!isChangeDiscussion(intent)) return onStart(text);
  if (!onDiscuss) return false;
  if (intent.status === 'ready_to_confirm') {
    if (!onKeepDiscussing) return false;
    const result = await onKeepDiscussing(intent.id);
    if (result === false) return false;
  }
  // The existing endpoint accepts the complete exact request. Preserve earlier
  // turns rather than replacing the user's constraints with the newest message.
  const previous = intent.draft_text || '';
  const request = previous && previous !== text ? `${previous}\n\n${text}` : text;
  return onDiscuss(intent.id, request);
}
