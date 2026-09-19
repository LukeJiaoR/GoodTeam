import { describe, expect, it, vi } from 'vitest';
import { isChangeDiscussion, sendConversationInput } from './conversationRouting';

describe('conversation scope routing', () => {
  it('keeps ordinary questions on the existing message endpoint', async () => {
    const onStart = vi.fn().mockResolvedValue({ ok: true });
    const onDiscuss = vi.fn();
    await sendConversationInput({ text: 'Why are we blocked?', onStart, onDiscuss });
    expect(onStart).toHaveBeenCalledWith('Why are we blocked?');
    expect(onDiscuss).not.toHaveBeenCalled();
  });

  it('preserves earlier exact constraints when continuing a change discussion', async () => {
    const onStart = vi.fn();
    const onDiscuss = vi.fn().mockResolvedValue({ ok: true });
    await sendConversationInput({
      text: 'Do not change the schema.',
      intent: { id: 'intent-1', status: 'discussing', draft_text: 'Ship Google only.' },
      onStart, onDiscuss,
    });
    expect(onDiscuss).toHaveBeenCalledWith('intent-1', 'Ship Google only.\n\nDo not change the schema.');
    expect(onStart).not.toHaveBeenCalled();
  });

  it('reopens discussion before sending a correction, without confirming it', async () => {
    const calls = [];
    const onKeepDiscussing = vi.fn(async (id) => { calls.push(['keep', id]); return {}; });
    const onDiscuss = vi.fn(async (id, text) => { calls.push(['discuss', id, text]); return {}; });
    await sendConversationInput({
      text: 'Also add tests.', intent: { id: 'intent-1', status: 'ready_to_confirm', draft_text: 'Ship Google only.' },
      onKeepDiscussing, onDiscuss, onStart: vi.fn(),
    });
    expect(calls).toEqual([['keep', 'intent-1'], ['discuss', 'intent-1', 'Ship Google only.\n\nAlso add tests.']]);
  });

  it('never sends after a failed or stale keep-discussing action', async () => {
    const onDiscuss = vi.fn();
    const params = { text: 'Keep the scope.', intent: { id: 'old', status: 'ready_to_confirm' }, onDiscuss };
    expect(await sendConversationInput({ ...params, onKeepDiscussing: async () => false })).toBe(false);
    await expect(sendConversationInput({ ...params, onKeepDiscussing: async () => { throw new Error('stale'); } })).rejects.toThrow('stale');
    expect(onDiscuss).not.toHaveBeenCalled();
  });

  it('does not duplicate a recovered initial draft', async () => {
    const onDiscuss = vi.fn();
    await sendConversationInput({ text: 'Keep the scope.', intent: { id: 'a', status: 'discussing', draft_text: 'Keep the scope.' }, onDiscuss });
    expect(onDiscuss).toHaveBeenCalledWith('a', 'Keep the scope.');
  });

  it.each(['stale', 'ambiguous', 'committing', 'committed', 'cancelled'])('does not route %s intents to a mutation', async (status) => {
    const onStart = vi.fn();
    const onDiscuss = vi.fn();
    const intent = { id: 'intent-1', status };
    expect(isChangeDiscussion(intent)).toBe(false);
    await sendConversationInput({ text: 'What happened?', intent, onStart, onDiscuss });
    expect(onStart).toHaveBeenCalledWith('What happened?');
    expect(onDiscuss).not.toHaveBeenCalled();
  });
});
