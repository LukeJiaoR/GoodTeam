import { describe, expect, it, vi } from 'vitest';
import { conversationTimeline, createConversationDelivery, deliveryLabel, shouldSubmitConversationOnEnter, trackConversationDelivery } from './conversationFeedback';

const message = { id: 'm1', projectKey: 'DEMO', runId: 'r1', text: 'Keep the schema.', status: 'sending' };
const scope = { projectKey: 'DEMO', runId: 'r1' };

describe('conversation delivery, not execution authority', () => {
  it('publishes immediately, then records only after the API acknowledges', async () => {
    let resolve;
    const updates = [];
    const request = trackConversationDelivery({ delivery: message,
      operation: () => new Promise((done) => { resolve = done; }), onUpdate: (item) => updates.push(item) });
    expect(updates).toEqual([message]);
    resolve({ workflow_started: false });
    expect(await request).toEqual({ workflow_started: false });
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
    for (const patch of [{ text: 'Different' }, { projectKey: 'OTHER' }, { runId: 'r2' }, { intentId: 'change1' }]) {
      expect(createConversationDelivery({ ...message, ...patch, previous }).id).not.toBe('m1');
    }
    expect(createConversationDelivery({ ...message, previous: { ...message, status: 'recorded' } }).id).not.toBe('m1');
  });

  it('reconciles by identity, not matching text', () => {
    const timeline = [{ id: 'old', actor: 'user', content: message.text }];
    expect(conversationTimeline(timeline, message, scope)).toHaveLength(2);
    const recorded = [...timeline, { id: 'm1', actor: 'user', content: message.text }];
    expect(conversationTimeline(recorded, message, scope)).toHaveLength(2);
    expect(conversationTimeline(recorded, message, scope)[1].delivery_status).toBe('sending');
    expect(conversationTimeline(recorded, { ...message, status: 'recorded' }, scope)).toBe(recorded);
    expect(recorded[1].delivery_status).toBeUndefined();
  });

  it('never leaks a delivery into another project, run or read-only history', () => {
    const timeline = [];
    for (const patch of [{ projectKey: 'OTHER' }, { runId: 'r2' }, { readOnly: true }]) {
      expect(conversationTimeline(timeline, message, { ...scope, ...patch })).toBe(timeline);
    }
  });

  it('uses the acknowledged durable change event identity', async () => {
    const onUpdate = vi.fn();
    await trackConversationDelivery({ delivery: { ...message, intentId: 'intent1' }, onUpdate,
      operation: async () => ({ intent_commit: { events: [
        { id: 'event1', actor: 'user', event: 'change_request_shared' },
        { id: 'event2', actor: 'manager', event: 'change_intent_summarized' },
      ] } }) });
    const delivered = onUpdate.mock.calls.at(-1)[0];
    expect(delivered.serverMessageId).toBe('event1');
    expect(conversationTimeline([{ id: 'event1' }], delivered, scope)).toEqual([{ id: 'event1' }]);
  });

  it('does not submit newlines, held keys or IME commit events', () => {
    expect(shouldSubmitConversationOnEnter({ key: 'Enter' })).toBe(true);
    for (const patch of [{ shiftKey: true }, { repeat: true }, { isComposing: true }, { nativeEvent: { isComposing: true } }, { keyCode: 229 }, { nativeEvent: { keyCode: 229 } }, { key: 'a' }]) {
      expect(shouldSubmitConversationOnEnter({ key: 'Enter', ...patch })).toBe(false);
    }
    expect(deliveryLabel('zh-CN', 'unconfirmed')).toContain('未确认');
  });
});
