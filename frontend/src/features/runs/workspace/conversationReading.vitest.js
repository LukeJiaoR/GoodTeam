import { beforeEach, expect, test } from 'vitest';
import { loadReadingPosition, readingScope, READING_MAX_AGE, READING_STORAGE_KEY, saveReadingPosition, timelineReadingId, unreadConversation } from './conversationReading';

const now = 1800000000000;
const scope = readingScope('PROJECT', { run_id: 'run-a' });
const record = { scope, following: false, anchor: { id: 'event-2', offset: -22 }, seenId: 'event-8', attentionKey: 'approval:prd' };
beforeEach(() => localStorage.clear());

test('checkpoints persist only bounded identity and viewport metadata and remain scope isolated', () => {
  expect(saveReadingPosition({ ...record, content: 'must never persist conversation content' }, localStorage, now)).toBe(true);
  const restored = loadReadingPosition(scope, localStorage, now + 1000);
  expect(restored).toEqual({ ...record, version: 1, savedAt: now });
  expect(localStorage.getItem(READING_STORAGE_KEY)).not.toContain('must never');
  for (const other of [readingScope('OTHER', { run_id: 'run-a' }), readingScope('PROJECT', { run_id: 'run-b' }), readingScope('PROJECT', { run_id: 'run-a', read_only: true })]) {
    expect(loadReadingPosition(other, localStorage, now)).toBeNull();
  }
});

test.each([
  '{', '{}', 'null', '[]', JSON.stringify([{ ...record, version: 77, savedAt: now }]),
  JSON.stringify([{ ...record, version: 1, savedAt: now, anchor: { id: 'event-2', offset: '22' } }]),
  JSON.stringify([{ ...record, version: 1, savedAt: now, anchor: { id: 'event-2', offset: 99999999 } }]),
  JSON.stringify([{ ...record, version: 1, savedAt: now, seenId: [] }]),
  JSON.stringify([{ ...record, version: 1, savedAt: now, following: 'false' }]),
  JSON.stringify([{ ...record, version: 1, savedAt: now, attentionKey: 'x'.repeat(5000) }]),
  'x'.repeat(129 * 1024),
])('malformed or unsupported storage cannot restore a bogus position', raw => {
  localStorage.setItem(READING_STORAGE_KEY, raw);
  expect(loadReadingPosition(scope, localStorage, now)).toBeNull();
  expect(saveReadingPosition(record, localStorage, now)).toBe(true);
  expect(loadReadingPosition(scope, localStorage, now)).toMatchObject(record);
});

test('expired and future records are ignored and old scopes are bounded', () => {
  saveReadingPosition(record, localStorage, now);
  expect(loadReadingPosition(scope, localStorage, now + READING_MAX_AGE + 1)).toBeNull();
  expect(loadReadingPosition(scope, localStorage, now - 60001)).toBeNull();
  for (let i = 0; i < 70; i++) saveReadingPosition({ ...record, scope: `scope-${i}` }, localStorage, now + i);
  expect(JSON.parse(localStorage.getItem(READING_STORAGE_KEY))).toHaveLength(40);
  expect(loadReadingPosition('scope-0', localStorage, now + 100)).toBeNull();
  expect(loadReadingPosition('scope-69', localStorage, now + 100)).toBeTruthy();
});

test('unavailable or full storage cannot throw during reading', () => {
  const storage = { getItem() { throw new Error('disabled'); }, setItem() { throw new Error('quota'); } };
  expect(loadReadingPosition(scope, storage, now)).toBeNull();
  expect(saveReadingPosition(record, storage, now)).toBe(false);
  expect(saveReadingPosition(record, { getItem: () => '[]', setItem: storage.setItem }, now)).toBe(false);
});

test('unread progress uses durable identity, not timestamps, poll identity or optimistic sends', () => {
  const events = [{ id: 'a' }, { message_id: 'b' }, { id: 'sending', delivery_status: 'sending' }, { id: 'c' }];
  expect(timelineReadingId(events[2])).toBe('');
  expect(unreadConversation(events, 'a').items).toEqual([events[1], events[3]]);
  expect(unreadConversation(events.map(item => ({ ...item })), 'b').items).toEqual([events[3]]);
  expect(unreadConversation(events, 'c').items).toEqual([]);
  expect(unreadConversation(events, 'removed')).toEqual({ items: [], unavailable: true });
  expect(unreadConversation(events, '')).toEqual({ items: [], unavailable: false });
});

test('maximum-size valid metadata cannot grow beyond the readable storage budget', () => {
  for (let i = 0; i < 50; i++) saveReadingPosition({ ...record, scope: `scope-${i}-${'s'.repeat(450)}`,
    attentionKey: 'a'.repeat(4096), seenId: 'e'.repeat(512), anchor: { id: 'x'.repeat(512), offset: -5 } }, localStorage, now);
  expect(localStorage.getItem(READING_STORAGE_KEY).length).toBeLessThanOrEqual(128 * 1024);
  expect(loadReadingPosition(`scope-49-${'s'.repeat(450)}`, localStorage, now)).toBeTruthy();
});
