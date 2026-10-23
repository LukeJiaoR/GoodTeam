// Browser-local reading metadata only. This is never workflow/approval authority.
export const READING_STORAGE_KEY = 'goodteam:conversation-reading:v1';
export const READING_MAX_AGE = 30 * 24 * 60 * 60 * 1000;
const MAX_SCOPES = 40;
const MAX_BYTES = 128 * 1024;
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 512;
const finiteOffset = value => Number.isFinite(value) && Math.abs(value) <= 100000;

export function readingScope(projectKey, mission) {
  return JSON.stringify([projectKey || '', mission?.run_id || '', Boolean(mission?.read_only)]);
}

export function timelineReadingId(item) {
  // Optimistic sends are not durable anchors and must not replace a saved frontier.
  return item?.delivery_status ? '' : String(item?.id || item?.message_id || '');
}

function validRecord(record, now) {
  return record && record.version === 1 && validId(record.scope)
    && Number.isFinite(record.savedAt) && record.savedAt <= now + 60000 && now - record.savedAt <= READING_MAX_AGE
    && typeof record.following === 'boolean'
    && (record.seenId === '' || validId(record.seenId))
    && typeof record.attentionKey === 'string' && record.attentionKey.length <= 4096
    && (record.anchor === null || (validId(record.anchor?.id) && finiteOffset(record.anchor.offset)));
}

function readRecords(storage, now) {
  const raw = storage.getItem(READING_STORAGE_KEY);
  if (!raw || raw.length > MAX_BYTES) return [];
  let records;
  try { records = JSON.parse(raw); } catch { return []; }
  return Array.isArray(records) ? records.filter(record => validRecord(record, now)).slice(-MAX_SCOPES).map(cleanRecord) : [];
}

export function loadReadingPosition(scope, storage, now = Date.now()) {
  try { return readRecords(storage ?? localStorage, now).find(record => record.scope === scope) || null; }
  catch { return null; }
}

function cleanRecord(record) {
  return { version: record.version, scope: record.scope, savedAt: record.savedAt, following: record.following,
    anchor: record.anchor ? { id: record.anchor.id, offset: record.anchor.offset } : null,
    seenId: record.seenId, attentionKey: record.attentionKey };
}

export function saveReadingPosition(record, storage, now = Date.now()) {
  const saved = cleanRecord({ ...record, version: 1, savedAt: now });
  if (!validRecord(saved, now)) return false;
  try {
    const target = storage ?? localStorage;
    const records = readRecords(target, now).filter(item => item.scope !== saved.scope);
    const bounded = [...records, saved].slice(-MAX_SCOPES);
    while (JSON.stringify(bounded).length > MAX_BYTES && bounded.length > 1) bounded.shift();
    target.setItem(READING_STORAGE_KEY, JSON.stringify(bounded));
    return true;
  } catch { return false; } // Disabled/full storage must not interrupt reading or drafts.
}

export function unreadConversation(timeline, seenId) {
  const durable = (timeline || []).filter(item => timelineReadingId(item));
  const index = durable.findIndex(item => timelineReadingId(item) === seenId);
  if (seenId && index < 0) return { items: [], unavailable: true };
  return { items: seenId ? durable.slice(index + 1) : [], unavailable: false };
}

export function readingChangeSummary(items, language) {
  const zh = language?.startsWith('zh');
  const groups = { replies: 0, reviews: 0, results: 0, work: 0 };
  for (const item of items) {
    const event = item.event || '';
    if (/review|approval|change_request/.test(event)) groups.reviews++;
    else if (/deliverable|verification|completed/.test(event)) groups.results++;
    else if (['assistant', 'manager', 'task_assistant', 'user'].includes(item.actor) || /answered|repl|message|conversation/.test(event)) groups.replies++;
    else groups.work++;
  }
  const labels = zh ? { replies: '条对话', reviews: '项审核变化', results: '项结果', work: '项进展' }
    : { replies: 'conversation updates', reviews: 'review changes', results: 'results', work: 'work updates' };
  return Object.entries(groups).filter(([, count]) => count).map(([kind, count]) => `${count}${zh ? '' : ' '}${labels[kind]}`).join(zh ? '、' : ' · ');
}

export function readingCopy(language) {
  return language?.startsWith('zh') ? {
    label: '阅读进度', unread: count => `${count} 条未读更新`, changed: '待处理事项有变化。',
    open: '阅读新更新', unavailable: '之前的未读进度对应的记录已不再可用，无法完整恢复未读数量。', stale: '之前的阅读记录已不在当前历史中，已保留在可用历史的开头。',
  } : {
    label: 'Reading progress', unread: count => `${count} unread updates`, changed: 'Pending items have changed.',
    open: 'Read new updates', unavailable: 'Earlier unread progress cannot be fully restored because its event is no longer available.', stale: 'The saved reading position is no longer in this history. Showing the available history from the start.',
  };
}
