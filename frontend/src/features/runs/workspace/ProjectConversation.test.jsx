import { render, fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { READING_STORAGE_KEY, readingScope, saveReadingPosition } from './conversationReading';

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
import ProjectConversation from './ProjectConversation';
const props = { projectKey: 'DEMO', mission: { source: 'workflow', project_key: 'DEMO', run_id: 'run-a' },
  assistantState: {}, language: 'en', timeline: [], t: key => key, onStart: () => {} };
const view = more => <MemoryRouter><ProjectConversation {...props} {...more}/></MemoryRouter>;
function readingHistory(container) {
  const node = container.querySelector('.team-manager-conversation');
  Object.defineProperties(node, { scrollHeight: { configurable: true, value: 1500 }, clientHeight: { configurable: true, value: 400 }});
  node.scrollTop = 200;
  fireEvent.scroll(node);
  return node;
}
test('new approval must not take the user away from manually reading history or drafting', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  const textarea = container.querySelector('textarea');
  fireEvent.change(textarea, { target: { value: 'Unsent draft remains in progress' }});
  textarea.focus();
  rerender(view({ attention: { id: 'approval:prd', kind: 'approval', target_id: 'prd', actions: ['approve'] } }));
  expect(document.activeElement).toBe(textarea);
  expect(textarea.value).toBe('Unsent draft remains in progress');
  expect(node.scrollTop).toBe(200);
});
test('background assistant failure must not take the user away from manually reading history', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  node.getBoundingClientRect = () => ({ top: 100 });
  const original = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function() {
    return this.matches('.assistant-request') ? { top: 1000 } : original.call(this);
  };
  try {
    rerender(view({ assistantState: { assistant_requests: { 'request-1': { status: 'failed', text: 'Original request', attempt: 1 } } } }));
    expect(node.scrollTop).toBe(200);
  } finally { HTMLElement.prototype.getBoundingClientRect = original; }
});
test('existing low-level timeline append already respects manual history reading', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ timeline: [{id:'event-1',actor:'system',event:'work_dispatched',content:'Worker started'}] }));
  expect(node.scrollTop).toBe(200);
});
test('same unresolved approval changing intent phase must not override a later manual scroll', () => {
  const attention = { id: 'approval:prd', kind: 'approval', target_id: 'prd', actions: ['approve'] };
  const { container, rerender } = render(view({ attention }));
  const node = readingHistory(container);
  rerender(view({ attention: { ...attention, intent_commit: { id: 'intent-1', status: 'ready_to_confirm', draft_text: 'Keep the API stable' } } }));
  expect(node.scrollTop).toBe(200);
});
test('explicit user message sending already resumes following and should remain an exception', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ timeline: [{id:'message-1',actor:'user',event:'mission_requested',content:'Send this', delivery_status:'sending'}] }));
  expect(node.scrollTop).toBe(1500);
});

const approval = { id: 'approval:prd', kind: 'approval', target_id: 'prd', actions: ['approve'] };
const failedRequest = { assistant_requests: { 'request-1': { status: 'failed', text: 'Original request', attempt: 1 } } };

test('explicit pending navigation changes position and focus only, preserving the draft and decision authority', () => {
  const onDecide = vi.fn(), onAssistantAction = vi.fn();
  const { container, rerender } = render(view({ onDecide, onAssistantAction }));
  const node = readingHistory(container);
  const textarea = container.querySelector('textarea');
  fireEvent.change(textarea, { target: { value: 'My unsent thought' }});
  textarea.focus();
  rerender(view({ attention: approval, onDecide, onAssistantAction }));
  const target = container.querySelector('.project-conversation-attention');
  node.getBoundingClientRect = () => ({ top: 100 });
  target.getBoundingClientRect = () => ({ top: 1000 });
  const button = screen.getByRole('button', { name: 'assistant.project.viewPending' });
  button.focus();
  fireEvent.click(button);
  expect(node.scrollTop).toBe(1084);
  expect(document.activeElement).toBe(target);
  expect(textarea.value).toBe('My unsent thought');
  expect(onDecide).not.toHaveBeenCalled();
  expect(onAssistantAction).not.toHaveBeenCalled();
  // Looking at pending controls does not silently resume following.
  rerender(view({ attention: { ...approval, id: 'approval:rfc' }, onDecide, onAssistantAction }));
  expect(node.scrollTop).toBe(1084);
});

test('pending affordance resolves with current props rather than retaining a dismissed snapshot', () => {
  const { container, rerender } = render(view({ attention: approval }));
  const node = readingHistory(container);
  expect(screen.getByRole('button', { name: 'assistant.project.viewPending' })).toBeInTheDocument();
  rerender(view());
  expect(screen.queryByRole('button', { name: 'assistant.project.viewPending' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'assistant.project.returnLatest' })).toBeInTheDocument();
  expect(node.scrollTop).toBe(200);
});

test('explicit return to latest restores automatic following without dispatching a command', () => {
  const onStart = vi.fn();
  const { container, rerender } = render(view({ onStart }));
  const node = readingHistory(container);
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.returnLatest' }));
  expect(node.scrollTop).toBe(1500);
  expect(document.activeElement).toBe(node);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
  Object.defineProperty(node, 'scrollHeight', { configurable: true, value: 1700 });
  rerender(view({ onStart, timeline: [{ id: 'new', actor: 'system', content: 'Progress' }] }));
  expect(node.scrollTop).toBe(1700);
  expect(onStart).not.toHaveBeenCalled();
});

test('scrolling back near the bottom resumes normal attention following', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  node.scrollTop = 1080;
  fireEvent.scroll(node);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
  rerender(view({ attention: approval }));
  expect(node.scrollTop).toBe(1500);
});

test.each([
  { projectKey: 'OTHER' },
  { mission: { ...props.mission, run_id: 'run-b' } },
  { mission: { ...props.mission, read_only: true } },
])('changed conversation scope resets follow state without leaking the prior notice: %j', changed => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view(changed));
  const changedNode = container.querySelector('.team-manager-conversation');
  expect(changedNode).not.toBe(node);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
  Object.defineProperties(changedNode, { scrollHeight: { configurable: true, value: 1500 }, clientHeight: { configurable: true, value: 400 } });
  changedNode.scrollTop = 300;
  fireEvent.scroll(changedNode);
  rerender(view({ ...changed, attention: approval }));
  expect(changedNode.scrollTop).toBe(300);
});

test('same-scope polls with fresh object identities preserve the manually chosen position', () => {
  const { container, rerender } = render(view({ attention: approval }));
  const node = readingHistory(container);
  rerender(view({ mission: { ...props.mission }, attention: { ...approval } }));
  expect(node.scrollTop).toBe(200);
});

test('an assistant recovery remains visible as an explicit pending item until resolved', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ assistantState: failedRequest }));
  expect(node.scrollTop).toBe(200);
  expect(screen.getByRole('button', { name: 'assistant.project.viewPending' })).toBeInTheDocument();
  rerender(view({ assistantState: { assistant_requests: { 'request-1': { status: 'succeeded' } } } }));
  expect(screen.queryByRole('button', { name: 'assistant.project.viewPending' })).not.toBeInTheDocument();
});

test('active work does not advertise its superseded blocker as pending', () => {
  const { container } = render(view({ attention: { kind: 'blocker', id: 'blocked' }, executionRunning: true }));
  readingHistory(container);
  expect(screen.queryByRole('button', { name: 'assistant.project.viewPending' })).not.toBeInTheDocument();
});

test('team decision changes stay quiet while reading, with a visible way to inspect', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ assistantState: { pending_team_decision: { id: 'decision-a', status: 'pending' } } }));
  expect(node.scrollTop).toBe(200);
  expect(screen.getByRole('button', { name: 'assistant.project.viewPending' })).toBeInTheDocument();
  rerender(view({ assistantState: { pending_team_decision: { id: 'decision-b', status: 'stale' } } }));
  expect(node.scrollTop).toBe(200);
});


test('a pause with no durable history cannot resurrect a stale notice on scope return', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ projectKey: 'OTHER' }));
  rerender(view());
  expect(container.querySelector('.team-manager-conversation')).not.toBe(node);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
});

test('sending a new message clears the reading notice before the programmatic scroll event', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ timeline: [{ id: 'send-1', actor: 'user', content: 'My message', delivery_status: 'sending' }] }));
  expect(node.scrollTop).toBe(1500);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
  rerender(view({ timeline: [{ id: 'send-1', actor: 'user', content: 'My message' }] }));
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
});


test('a running assistant request cannot hide a second request needing recovery', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ assistantState: { assistant_requests: {
    running: { status: 'running', attempt: 1 }, failed: { status: 'failed', attempt: 2, text: 'Needs recovery' },
  } } }));
  expect(node.scrollTop).toBe(200);
  expect(screen.getByRole('button', { name: 'assistant.project.viewPending' })).toBeInTheDocument();
});


test('the browser scroll event from inspecting a short pending group does not resume following', () => {
  const { container, rerender } = render(view({ attention: approval }));
  const node = readingHistory(container);
  const target = container.querySelector('.project-conversation-attention');
  node.getBoundingClientRect = () => ({ top: 100 });
  target.getBoundingClientRect = () => ({ top: 1000 });
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.viewPending' }));
  expect(node.scrollTop).toBe(1084); // only 16 px remain: inside the follow threshold
  fireEvent.scroll(node); // emitted by the browser for the explicit inspection
  expect(screen.getByRole('button', { name: 'assistant.project.returnLatest' })).toBeInTheDocument();
  rerender(view({ attention: { ...approval, id: 'approval:rfc' } }));
  expect(node.scrollTop).toBe(1084);
  // A later user scroll towards the end can deliberately resume following.
  node.scrollTop = 1100;
  fireEvent.scroll(node);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
});


test('explicit sending clears an unconsumed inspection scroll marker before later manual reading', () => {
  const { container, rerender } = render(view({ attention: approval }));
  const node = readingHistory(container);
  const target = container.querySelector('.project-conversation-attention');
  node.getBoundingClientRect = () => ({ top: 100 });
  target.getBoundingClientRect = () => ({ top: 516 });
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.viewPending' }));
  expect(node.scrollTop).toBe(600);
  // The browser has not delivered the pending programmatic scroll event yet.
  rerender(view({ attention: approval, timeline: [{ id: 'send-now', actor: 'user', content: 'Send', delivery_status: 'sending' }] }));
  expect(node.scrollTop).toBe(1500);
  node.scrollTop = 600;
  fireEvent.scroll(node);
  expect(screen.getByRole('button', { name: 'assistant.project.returnLatest' })).toBeInTheDocument();
});

const history = Array.from({ length: 8 }, (_, i) => ({ id: `history-${i}`, actor: 'system', event: 'progress', content: `Progress ${i}` }));
const readingStorage = () => JSON.parse(localStorage.getItem(READING_STORAGE_KEY) || '[]').find(record => record.scope === readingScope('DEMO', props.mission));
function measuredHistory(container) {
  const node = container.querySelector('.team-manager-conversation');
  Object.defineProperties(node, { scrollHeight: { configurable: true, value: 1600 }, clientHeight: { configurable: true, value: 400 } });
  node.getBoundingClientRect = () => ({ top: 100, bottom: 500, height: 400 });
  for (const [i, row] of [...container.querySelectorAll('[data-reading-id]')].entries()) {
    row.getBoundingClientRect = () => ({ top: 100 + i * 200 - node.scrollTop, bottom: 300 + i * 200 - node.scrollTop, height: 200 });
  }
  return node;
}

test('manual reading persists a durable anchor and frontier without raw message content', () => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = 430;
  fireEvent.scroll(node);
  expect(readingStorage()).toMatchObject({ following: false, anchor: { id: 'history-2', offset: -30 }, seenId: 'history-7' });
  rerender(view({ timeline: [...history, { id: 'after-away', actor: 'system', content: 'Private update text' }] }));
  expect(node.scrollTop).toBe(430);
  expect(screen.getByRole('button', { name: 'Read new updates' })).toBeInTheDocument();
  expect(readingStorage().seenId).toBe('history-7');
  expect(localStorage.getItem(READING_STORAGE_KEY)).not.toContain('Private update text');
});

test('a hidden tab preserves its last read frontier through new arrivals and visibility return', () => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = 1200;
  fireEvent.scroll(node);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  fireEvent(document, new Event('visibilitychange'));
  try {
    rerender(view({ timeline: [...history, { id: 'new-hidden', actor: 'system', content: 'New' }] }));
    expect(node.scrollTop).toBe(1200);
    expect(readingStorage().seenId).toBe('history-7');
    visibility.mockReturnValue('visible');
    fireEvent(document, new Event('visibilitychange'));
    expect(readingStorage().seenId).toBe('history-7');
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.returnLatest' }));
    expect(readingStorage().seenId).toBe('new-hidden');
  } finally { visibility.mockRestore(); }
});

test('restoring a removed anchor never fabricates a read frontier or changes workflow authority', () => {
  saveReadingPosition({ scope: readingScope('DEMO', props.mission), following: false, seenId: 'removed',
    anchor: { id: 'removed', offset: 20 }, attentionKey: 'old' });
  const onDecide = vi.fn();
  const { container } = render(view({ timeline: history, attention: approval, onDecide }));
  expect(container.querySelector('.team-manager-conversation').scrollTop).toBe(0);
  expect(readingStorage().seenId).toBe('removed');
  expect(screen.queryByRole('button', { name: 'Read new updates' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.viewPending' }));
  expect(readingStorage().seenId).toBe('removed');
  expect(onDecide).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.returnLatest' }));
  expect(readingStorage().seenId).toBe('history-7');
});

test('storage failures preserve manual reading, input and explicit follow control', () => {
  const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
  try {
    const { container, rerender } = render(view({ timeline: history }));
    const node = measuredHistory(container);
    node.scrollTop = 450;
    fireEvent.scroll(node);
    const input = container.querySelector('textarea');
    fireEvent.change(input, { target: { value: 'Keep my unsent text' } });
    rerender(view({ timeline: [...history, { id: 'later', actor: 'system', content: 'Later' }] }));
    expect(node.scrollTop).toBe(450);
    expect(input.value).toBe('Keep my unsent text');
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.returnLatest' }));
    expect(node.scrollTop).toBe(1600);
  } finally { storage.mockRestore(); }
});

test('a response first received after a hidden tab returns still compares the away frontier', () => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = 1200;
  fireEvent.scroll(node);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  fireEvent(document, new Event('visibilitychange'));
  visibility.mockReturnValue('visible');
  fireEvent(document, new Event('visibilitychange'));
  try {
    rerender(view({ timeline: [...history, { id: 'arrived-after-return', actor: 'system', content: 'Unseen while away' }] }));
    expect(node.scrollTop).toBe(1200);
    expect(readingStorage().seenId).toBe('history-7');
    expect(screen.getByRole('button', { name: 'Read new updates' })).toBeInTheDocument();
    // A fresh unchanged poll must not consume that frontier either.
    rerender(view({ timeline: [...history, { id: 'arrived-after-return', actor: 'system', content: 'Unseen while away' }] }));
    expect(readingStorage().seenId).toBe('history-7');
  } finally { visibility.mockRestore(); }
});

test('reload and A-B-A restore an exact durable anchor while B remains independent', () => {
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function() {
    if (this.classList.contains('team-manager-conversation')) return { top: 100, bottom: 500, height: 400 };
    if (this.dataset.readingId) {
      const i = history.findIndex(item => item.id === this.dataset.readingId);
      const top = 100 + i * 200 - this.closest('.team-manager-conversation').scrollTop;
      return { top, bottom: top + 200, height: 200 };
    }
    return { top: 0, bottom: 0, height: 0 };
  });
  try {
    const { container, rerender, unmount } = render(view({ timeline: history }));
    let node = container.querySelector('.team-manager-conversation');
    Object.defineProperties(node, { scrollHeight: { configurable: true, value: 1600 }, clientHeight: { configurable: true, value: 400 } });
    node.scrollTop = 430;
    fireEvent.scroll(node);
    expect(readingStorage().anchor).toEqual({ id: 'history-2', offset: -30 });
    rerender(view({ projectKey: 'OTHER', timeline: history }));
    expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
    rerender(view({ timeline: history }));
    node = container.querySelector('.team-manager-conversation');
    expect(node.scrollTop).toBe(430);
    expect(screen.getByRole('button', { name: 'assistant.project.returnLatest' })).toBeInTheDocument();
    unmount();
    const reload = render(view({ timeline: history }));
    expect(reload.container.querySelector('.team-manager-conversation').scrollTop).toBe(430);
  } finally { rect.mockRestore(); }
});

test('unchanged visible polls cannot erase an away boundary before a delayed update arrives', () => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = 1200;
  fireEvent.scroll(node);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  fireEvent(document, new Event('visibilitychange'));
  visibility.mockReturnValue('visible');
  fireEvent(document, new Event('visibilitychange'));
  try {
    rerender(view({ timeline: history.map(item => ({ ...item })) }));
    rerender(view({ timeline: history.map(item => ({ ...item })) }));
    const arrival = { id: 'delayed-away-result', actor: 'system', content: 'A delayed result' };
    rerender(view({ timeline: [...history, arrival] }));
    expect(node.scrollTop).toBe(1200);
    expect(readingStorage().seenId).toBe('history-7');
    expect(screen.getByRole('button', { name: 'Read new updates' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.returnLatest' }));
    expect(readingStorage().seenId).toBe(arrival.id);
    Object.defineProperty(node, 'scrollHeight', { configurable: true, value: 1800 });
    rerender(view({ timeline: [...history, arrival, { id: 'after-explicit-reading', actor: 'system', content: 'Latest' }] }));
    expect(node.scrollTop).toBe(1800);
    expect(readingStorage().seenId).toBe('after-explicit-reading');
  } finally { visibility.mockRestore(); }
});

test('pending inspection followed by hiding and reload preserves the original logical bookmark', () => {
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function() {
    if (this.classList.contains('team-manager-conversation')) return { top: 100, bottom: 500, height: 400 };
    if (this.classList.contains('project-conversation-attention')) return { top: 1700 - this.closest('.team-manager-conversation').scrollTop, bottom: 1900, height: 200 };
    if (this.dataset.readingId) {
      const i = history.findIndex(item => item.id === this.dataset.readingId);
      const top = 100 + i * 200 - this.closest('.team-manager-conversation').scrollTop;
      return { top, bottom: top + 200, height: 200 };
    }
    return { top: 0, bottom: 0, height: 0 };
  });
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  try {
    const { container, unmount } = render(view({ timeline: history, attention: approval }));
    const node = container.querySelector('.team-manager-conversation');
    Object.defineProperties(node, { scrollHeight: { configurable: true, value: 1800 }, clientHeight: { configurable: true, value: 400 } });
    node.scrollTop = 430;
    fireEvent.scroll(node);
    const saved = readingStorage();
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.viewPending' }));
    fireEvent.scroll(node); // consume the programmatic inspection event
    expect(node.scrollTop).not.toBe(430);
    visibility.mockReturnValue('hidden');
    fireEvent(document, new Event('visibilitychange'));
    fireEvent(window, new Event('pagehide'));
    expect(readingStorage().anchor).toEqual(saved.anchor);
    expect(readingStorage().seenId).toBe(saved.seenId);
    unmount();
    visibility.mockReturnValue('visible');
    const restored = render(view({ timeline: history, attention: approval }));
    expect(restored.container.querySelector('.team-manager-conversation').scrollTop).toBe(430);
  } finally { rect.mockRestore(); visibility.mockRestore(); }
});

test('inspecting a later source event does not acknowledge skipped unread events or resume following', () => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = 430;
  fireEvent.scroll(node);
  const saved = readingStorage();
  const arrivals = [
    { id: 'first-update', actor: 'task_assistant', event: 'project_question_answered', content: 'The first result needs review.' },
    { id: 'second-update', actor: 'task_assistant', event: 'project_question_answered', content: 'The second result is available.' },
  ];
  rerender(view({ timeline: [...history, ...arrivals] }));
  const source = container.querySelector('[data-reading-id="second-update"]');
  source.getBoundingClientRect = () => ({ top: 1500, bottom: 1700, height: 200 });
  fireEvent.click(screen.getByRole('button', { name: 'The second result is available.' }));
  fireEvent.scroll(node);
  expect(document.activeElement).toBe(source);
  expect(readingStorage().seenId).toBe(saved.seenId);
  expect(readingStorage().anchor).toEqual(saved.anchor);
  expect(screen.getByRole('button', { name: 'Read new updates' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'assistant.project.returnLatest' })).toBeInTheDocument();
});

test.each([430, 1200])('a hidden mounted conversation preserves its bookmark, frontier and draft at scroll %i', position => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = position;
  fireEvent.scroll(node);
  const input = container.querySelector('textarea');
  fireEvent.change(input, { target: { value: 'Keep my draft while viewing execution' } });
  const saved = readingStorage();
  rerender(view({ timeline: history, sectionProps: { hidden: true } }));
  const arrival = { id: 'while-panel-hidden', actor: 'system', content: 'Unseen update' };
  rerender(view({ timeline: [...history, arrival], sectionProps: { hidden: true } }));
  fireEvent.scroll(node); // Hidden layout changes cannot act as deliberate reading.
  expect(node.scrollTop).toBe(position);
  expect(readingStorage().seenId).toBe(saved.seenId);
  expect(readingStorage().anchor).toEqual(saved.anchor);
  expect(input.value).toBe('Keep my draft while viewing execution');
  rerender(view({ timeline: [...history, arrival] }));
  expect(node.scrollTop).toBe(position);
  expect(readingStorage().seenId).toBe(saved.seenId);
  expect(readingStorage().anchor).toEqual(saved.anchor);
  expect(screen.getByRole('region', { name: 'Reading progress' })).toHaveTextContent('1 unread updates');
});

test('returning from a hidden conversation preserves its away boundary through unchanged polls until explicit reading', () => {
  const { container, rerender } = render(view({ timeline: history }));
  const node = measuredHistory(container);
  node.scrollTop = 1200;
  fireEvent.scroll(node);
  const saved = readingStorage();
  rerender(view({ timeline: history, sectionProps: { hidden: true } }));
  rerender(view({ timeline: history.map(item => ({ ...item })) }));
  rerender(view({ timeline: history.map(item => ({ ...item })) }));
  const arrival = { id: 'after-panel-return', actor: 'system', content: 'Delayed unseen update' };
  rerender(view({ timeline: [...history, arrival] }));
  expect(node.scrollTop).toBe(1200);
  expect(readingStorage().seenId).toBe(saved.seenId);
  expect(readingStorage().anchor).toEqual(saved.anchor);
  expect(screen.getByRole('button', { name: 'Read new updates' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'assistant.project.returnLatest' }));
  expect(readingStorage().seenId).toBe(arrival.id);
  Object.defineProperty(node, 'scrollHeight', { configurable: true, value: 1800 });
  rerender(view({ timeline: [...history, arrival, { id: 'after-reading', actor: 'system', content: 'Latest update' }] }));
  expect(node.scrollTop).toBe(1800);
  expect(readingStorage().seenId).toBe('after-reading');
});

test.each([false, true])('an initially hidden conversation defers checkpoint restoration until shown, following=%s', following => {
  const anchor = following ? { id: 'history-6', offset: 0 } : { id: 'history-2', offset: -30 };
  saveReadingPosition({ scope: readingScope('DEMO', props.mission), following, seenId: 'history-7',
    anchor, attentionKey: '[[],[],[],[]]' });
  const saved = readingStorage();
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function() {
    if (this.closest('[hidden]')) return { top: 0, bottom: 0, height: 0 };
    if (this.classList.contains('team-manager-conversation')) return { top: 100, bottom: 500, height: 400 };
    if (this.dataset.readingId) {
      const i = history.findIndex(item => item.id === this.dataset.readingId);
      const top = 100 + i * 200 - this.closest('.team-manager-conversation').scrollTop;
      return { top, bottom: top + 200, height: 200 };
    }
    return { top: 0, bottom: 0, height: 0 };
  });
  try {
    const { container, rerender } = render(view({ timeline: history, sectionProps: { hidden: true } }));
    const node = container.querySelector('.team-manager-conversation');
    Object.defineProperties(node, { scrollHeight: { configurable: true, value: 1600 }, clientHeight: { configurable: true, value: 400 } });
    expect(node.scrollTop).toBe(0);
    expect(readingStorage()).toEqual(saved);
    rerender(view({ timeline: history.map(item => ({ ...item })), sectionProps: { hidden: true } }));
    expect(readingStorage()).toEqual(saved);
    rerender(view({ timeline: history }));
    expect(node.scrollTop).toBe(following ? 1200 : 430);
    fireEvent.scroll(node); // The restore itself does not advance the frontier.
    expect(readingStorage().anchor).toEqual(anchor);
    const arrival = { id: 'after-hidden-mount', actor: 'system', content: 'Unseen update' };
    rerender(view({ timeline: [...history, arrival] }));
    expect(readingStorage().seenId).toBe(saved.seenId);
    expect(screen.getByRole('button', { name: 'Read new updates' })).toBeInTheDocument();
  } finally { rect.mockRestore(); }
});

test.each([null, false, true])('a conversation first mounted in a background document initializes when visible, following=%s', following => {
  const anchor = following ? { id: 'history-6', offset: 0 } : { id: 'history-2', offset: -30 };
  if (following !== null) saveReadingPosition({ scope: readingScope('DEMO', props.mission), following,
    seenId: 'history-7', anchor, attentionKey: '[[],[],[],[]]' });
  const saved = readingStorage();
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  try {
    const { container, rerender } = render(view({ timeline: history }));
    const node = measuredHistory(container);
    expect(readingStorage()).toEqual(saved);
    rerender(view({ timeline: history.map(item => ({ ...item })) }));
    expect(readingStorage()).toEqual(saved);
    visibility.mockReturnValue('visible');
    fireEvent(document, new Event('visibilitychange'));
    const position = following === null ? 1600 : following ? 1200 : 430;
    expect(node.scrollTop).toBe(position);
    expect(readingStorage()).toMatchObject({ following: following ?? true, seenId: 'history-7' });
    if (saved) expect(readingStorage().anchor).toEqual(anchor);
    rerender(view({ timeline: history.map(item => ({ ...item })) }));
    expect(node.scrollTop).toBe(position);
    expect(readingStorage().seenId).toBe('history-7');
  } finally { visibility.mockRestore(); }
});
