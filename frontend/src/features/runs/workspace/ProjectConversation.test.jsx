import { render, fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
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
  expect(node.scrollTop).toBe(1500);
  expect(screen.queryByRole('button', { name: 'assistant.project.returnLatest' })).not.toBeInTheDocument();
  node.scrollTop = 300;
  fireEvent.scroll(node);
  rerender(view({ ...changed, attention: approval }));
  expect(node.scrollTop).toBe(300);
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


test('returning to an earlier scope does not resurrect that scope’s old pause state', () => {
  const { container, rerender } = render(view());
  const node = readingHistory(container);
  rerender(view({ projectKey: 'OTHER' }));
  rerender(view());
  expect(node.scrollTop).toBe(1500);
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
