import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { loadReadingPosition, saveReadingPosition, timelineReadingId, unreadConversation } from './conversationReading';

const nearBottom = node => node.scrollHeight - node.scrollTop - node.clientHeight <= 64;
const hidden = () => document.visibilityState === 'hidden';
const rows = node => [...node.querySelectorAll('[data-reading-id]')].filter(row => row.dataset.readingId);
function captureAnchor(node) {
  const top = node.getBoundingClientRect().top;
  const items = rows(node);
  const row = items.find(item => item.getBoundingClientRect().bottom > top) || items.at(-1);
  // Pending controls can extend below the timeline. Keep the final durable
  // event as the bookmark instead of falling back to unrelated old history.
  return row ? { id: row.dataset.readingId, offset: Math.max(-row.getBoundingClientRect().height, row.getBoundingClientRect().top - top) } : null;
}
function lastVisibleId(node) {
  const bounds = node.getBoundingClientRect();
  return rows(node).filter(row => {
    const rect = row.getBoundingClientRect();
    return rect.bottom > bounds.top && rect.bottom <= bounds.bottom + 1;
  }).at(-1)?.dataset.readingId;
}

// Mounted per project/run/read-only scope. Layout synchronization is necessary:
// applying a durable DOM anchor after paint would flash/jump to the latest item.
export default function useConversationReading({ scope, conversationRef, timeline, attentionKey, deliveryKey, assistantFocusKey, visible = true }) {
  const [documentVisible, setDocumentVisible] = useState(() => !hidden());
  const [initial] = useState(() => loadReadingPosition(scope));
  const [reading, setReading] = useState(() => ({ paused: initial ? !initial.following : false,
    seenId: initial?.seenId || '', attentionKey: initial?.attentionKey || attentionKey, stale: false }));
  const current = useRef({ ...reading, initialized: false, anchor: initial?.anchor || null, deliveryKey: '', inspection: null });
  const latest = useRef(null);
  const ids = timeline.map(timelineReadingId).filter(Boolean);
  const timelineKey = JSON.stringify(ids);

  const publish = () => {
    const state = current.current;
    setReading(previous => previous.paused === state.paused && previous.seenId === state.seenId
      && previous.attentionKey === state.attentionKey && previous.stale === state.stale
      ? previous : { paused: state.paused, seenId: state.seenId, attentionKey: state.attentionKey, stale: state.stale });
  };
  const persist = () => {
    const state = current.current;
    if (!state.initialized || (!state.anchor && !state.seenId)) return;
    saveReadingPosition({ scope, following: !state.paused, anchor: state.anchor, seenId: state.seenId, attentionKey: state.attentionKey });
  };
  const acknowledgeVisible = (node, all = false) => {
    const state = current.current;
    const data = latest.current;
    const id = all ? data.ids.at(-1) : lastVisibleId(node);
    // A read frontier is monotonic, including when someone rereads old messages.
    if (all) state.seenId = id || '';
    else if (id && data.ids.indexOf(id) > data.ids.indexOf(state.seenId)) state.seenId = id;
    if (all) { state.attentionKey = data.attentionKey; state.stale = false; }
  };

  useLayoutEffect(() => {
    latest.current = { ids, attentionKey };
    const node = conversationRef.current;
    if (!node) return;
    const state = current.current;
    if (!visible || hidden()) {
      // Background documents and responsive tabs are not readable viewports
      // or places to restore a saved anchor, including on the first mount.
      state.away = true;
      if (!state.initialized) return;
    }
    const updateKey = JSON.stringify([timelineKey, attentionKey, assistantFocusKey]);
    const changed = state.updateKey !== updateKey;
    state.updateKey = updateKey;
    const sending = deliveryKey && state.deliveryKey !== deliveryKey;
    state.deliveryKey = deliveryKey;
    if (!state.initialized) {
      // Wait for history to load instead of overwriting an existing checkpoint
      // with an intermediate empty response.
      if (initial && !ids.length && !sending) return;
      state.initialized = true;
      if (!initial) state.away = false; // A first visible visit establishes its baseline.
      const unread = unreadConversation(timeline, state.seenId);
      const changed = Boolean(initial && (unread.items.length || unread.unavailable || state.attentionKey !== attentionKey));
      state.paused = Boolean(initial && (!initial.following || changed));
      if (state.paused || (initial && state.away)) {
        const row = rows(node).find(item => item.dataset.readingId === initial?.anchor?.id);
        if (row) {
          const offset = Math.min(node.clientHeight, Math.max(-row.getBoundingClientRect().height, initial.anchor.offset));
          node.scrollTop += row.getBoundingClientRect().top - node.getBoundingClientRect().top - offset;
        } else { node.scrollTop = 0; state.stale = Boolean(initial?.anchor || unread.unavailable); }
        state.inspection = node.scrollTop;
      }
    }
    if (state.away && (unreadConversation(timeline, state.seenId).items.length || state.attentionKey !== attentionKey)) state.paused = true;
    // An unchanged response is not evidence the reader has caught up. Keep
    // the away boundary through delayed/stale polls until deliberate reading.
    if (sending && visible && !hidden()) { state.away = false; state.paused = false; state.inspection = null; }
    if (!visible || hidden()) {
      // Background polls must never consume unread items or move the viewport.
      if (unreadConversation(timeline, state.seenId).items.length || state.attentionKey !== attentionKey) state.paused = true;
    } else if (!state.paused && !state.away && (changed || sending)) {
      node.scrollTop = node.scrollHeight;
      const target = node.querySelector('.assistant-team-decision, .assistant-request, .assistant-pending-reply');
      if (target) node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - 16;
      acknowledgeVisible(node, true);
      state.anchor = captureAnchor(node);
    }
    // Showing a responsive panel can queue native scroll notifications without
    // any reader movement. Keep a visible-layout baseline while away; those
    // notifications must not silently resume following or consume delayed polls.
    if (state.away && visible && !hidden()) state.awayPosition = node.scrollTop;
    // This state is a projection of measured/restored DOM position, not data
    // fetching. Publish it before paint so the return controls match the view.
    publish();
    persist();
  // A fresh poll after visibility return must compare the away frontier even
  // when it arrives after the tab becomes visible. Unchanged polls never scroll.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, timeline, timelineKey, attentionKey, deliveryKey, assistantFocusKey, visible, documentVisible]);

  useEffect(() => {
    const onVisibility = () => {
      const node = conversationRef.current;
      if (!node) return;
      setDocumentVisible(!hidden());
      if (hidden()) {
        current.current.away = true;
        // Scroll/return actions already checkpoint the logical reading anchor.
        // A temporary pending/source inspection must not replace it on hide.
        persist();
      }
      else publish(); // Returning to the tab does not acknowledge unseen updates.
    };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onVisibility);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const handleScroll = event => {
    const node = event.currentTarget;
    const state = current.current;
    if (!visible || hidden() || !state.initialized) return;
    if (state.away && Math.abs(node.scrollTop - state.awayPosition) < 1) return;
    const inspected = state.inspection;
    state.inspection = null;
    if (inspected !== null && Math.abs(node.scrollTop - inspected) < 1) return;
    state.away = false;
    state.paused = !nearBottom(node);
    acknowledgeVisible(node, !state.paused);
    state.anchor = captureAnchor(node);
    publish();
    persist();
  };
  const returnToLatest = () => {
    const node = conversationRef.current;
    if (!node || !visible || hidden()) return;
    current.current.initialized = true;
    current.current.away = false;
    current.current.inspection = null;
    current.current.paused = false;
    node.scrollTop = node.scrollHeight;
    acknowledgeVisible(node, true);
    current.current.anchor = captureAnchor(node);
    publish(); persist();
    node.focus({ preventScroll: true });
  };
  const inspect = (target, acknowledge = false) => {
    const node = conversationRef.current;
    if (!node || !target || !visible || hidden()) return;
    node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - 16;
    current.current.inspection = node.scrollTop;
    if (acknowledge) {
      current.current.away = false;
      acknowledgeVisible(node);
      current.current.anchor = captureAnchor(node);
      publish(); persist();
    }
    target.focus({ preventScroll: true });
  };
  const readNewUpdates = () => {
    const first = unreadConversation(timeline, current.current.seenId).items[0];
    const row = rows(conversationRef.current).find(item => item.dataset.readingId === timelineReadingId(first));
    inspect(row, true);
  };
  const inspectEvent = item => {
    const node = conversationRef.current;
    if (!node) return;
    const row = rows(node).find(candidate => candidate.dataset.readingId === timelineReadingId(item));
    // A source preview can skip earlier unread events, so it must not advance
    // the sequential read frontier or replace the reader's bookmark.
    inspect(row);
  };
  return { ...reading, unread: unreadConversation(timeline, reading.seenId), handleScroll, returnToLatest, inspect, inspectEvent, readNewUpdates };
}
