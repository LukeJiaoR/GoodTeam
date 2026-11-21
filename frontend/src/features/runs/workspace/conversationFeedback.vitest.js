import { describe, expect, it, vi } from 'vitest';
import { conversationTimeline, createConversationDelivery, deliveryLabel, shouldSubmitConversationOnEnter, trackConversationDelivery } from './conversationFeedback';

const message = { id: 'm1', projectKey: 'DEMO', text: 'Keep the schema.', status: 'sending' };
const scope = { projectKey: 'DEMO' };

describe('conversation delivery, not execution authority', () => {
  it('publishes immediately, then records only after the API acknowledges', async () => {
    let resolve;
    const updates = [];
    const request = trackConversationDelivery({ delivery: message,
      operation: () => new Promise((done) => { resolve = done; }), onUpdate: (item) => updates.push(item) });
    expect(updates).toEqual([message]);
    resolve({ accepted: true });
    expect(await request).toEqual({ accepted: true });
    expect(updates.at(-1)).toMatchObject({ status: 'recorded', id: 'm1' });
  });

  it('marks rejection and false results unconfirmed, never stuck sending', async () => {
    const onUpdate = vi.fn();
    await expect(trackConversationDelivery({ delivery: message, onUpdate,
      operation: async () => { throw new Error('offline'); } })).rejects.toThrow('offline');
    expect(onUpdate).toHaveBeenLastCalledWith({ ...message, status: 'unconfirmed' });
    expect(await trackConversationDelivery({ delivery: message, onUpdate, operation: async () => false })).toBe(false);
    expect(onUpdate).toHaveBeenLastCalledWith({ ...message, status: 'unconfirmed' });
  });

  it('reuses identity only for an explicit identical unconfirmed message resend', () => {
    const previous = { ...message, status: 'unconfirmed' };
    expect(createConversationDelivery({ ...message, previous }).id).toBe('m1');
    for (const patch of [{ text: 'Different' }, { projectKey: 'OTHER' }, { contextKey: 'other-draft' }]) {
      expect(createConversationDelivery({ ...message, ...patch, previous }).id).not.toBe('m1');
    }
    expect(createConversationDelivery({ ...message, previous: { ...message, status: 'recorded' } }).id).not.toBe('m1');
  });

  it('reconciles by identity, not matching text', () => {
    const timeline = [{ id: 'old', actor: 'user', content: message.text }];
    expect(conversationTimeline(timeline, message, scope)).toHaveLength(2);
    const recorded = [...timeline, { id: 'm1', actor: 'user', content: message.text }];
    expect(conversationTimeline(recorded, message, scope)).toHaveLength(2);
    expect(conversationTimeline(recorded, message, scope)[1].delivery_status).toBeUndefined();
    expect(conversationTimeline(recorded, { ...message, status: 'recorded' }, scope)).toBe(recorded);
    expect(recorded[1].delivery_status).toBeUndefined();
  });

  it('reconciles an unconfirmed delivery after a durable retry reply arrives', () => {
    const timeline = [
      { id: 'm1', actor: 'user', content: message.text },
      { id: 'reply:m1', actor: 'task_assistant', content: 'The original request is preserved.' },
    ];
    expect(conversationTimeline(timeline, { ...message, status: 'unconfirmed' }, scope)).toBe(timeline);
  });

  it('keeps local delivery within the originating project', () => {
    const timeline = [];
    expect(conversationTimeline(timeline, message, { projectKey: 'OTHER' })).toBe(timeline);
  });

  it('does not submit newlines, held keys or IME commit events', () => {
    expect(shouldSubmitConversationOnEnter({ key: 'Enter' })).toBe(true);
    for (const patch of [{ shiftKey: true }, { repeat: true }, { isComposing: true }, { nativeEvent: { isComposing: true } }, { keyCode: 229 }, { nativeEvent: { keyCode: 229 } }, { key: 'a' }]) {
      expect(shouldSubmitConversationOnEnter({ key: 'Enter', ...patch })).toBe(false);
    }
    expect(deliveryLabel('zh-CN', 'unconfirmed')).toContain('未确认');
  });
});
