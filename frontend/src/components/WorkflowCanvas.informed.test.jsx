import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import WorkflowCanvas from './WorkflowCanvas';

const mocks = vi.hoisted(() => ({ decide: vi.fn(), fitView: vi.fn(), zoomIn: vi.fn(), zoomOut: vi.fn() }));
vi.mock('../features/reviews/reviewApi', () => ({ reviewApi: { decideReview: mocks.decide } }));
vi.mock('@xyflow/react', async () => {
  const React = await import('react');
  return {
    ReactFlow: ({ nodes, onNodeClick, children }) => <div>{nodes.map(node =>
      <button key={node.id} onClick={() => onNodeClick(null, node)}>{node.id}</button>)}{children}</div>,
    ReactFlowProvider: ({ children }) => children,
    Background: () => null, Handle: () => null, Position: { Left: 'left', Right: 'right' },
    Panel: ({ children }) => <div>{children}</div>,
    useNodesState: (value) => [...React.useState(value), () => {}],
    useOnViewportChange: () => {},
    useReactFlow: () => ({ fitView: mocks.fitView, zoomIn: mocks.zoomIn, zoomOut: mocks.zoomOut }),
  };
});
const t = key => key;
const props = () => ({ projectKey: 'DEMO', runId: 'run-1', t,
  steps: [{ key: 'prd-step', approval: 'prd', status: 'review-needed' }],
  reviews: [{ approval: 'prd', artifact: 'prd', artifact_version: 'sha256:v1', is_actionable: true,
    recommendation: { verdict: 'caution' }, evidence_snapshot: {} }],
});
const wrap = value => <MemoryRouter><WorkflowCanvas {...value} /></MemoryRouter>;
afterEach(() => vi.clearAllMocks());

test('canvas manual continuation validates the note and keeps exact identity in its API request', async () => {
  mocks.decide.mockResolvedValue({});
  render(wrap(props()));
  fireEvent.click(await screen.findByRole('button', { name: 'prd-step' }));
  const input = screen.getByRole('textbox');
  const button = screen.getByRole('button', { name: 'work.informedApproval.continue' });
  expect(button).toBeDisabled();
  fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
  expect(mocks.decide).not.toHaveBeenCalled();
  const note = 'I inspected this version and accept its missing reviewer evidence.';
  fireEvent.change(input, { target: { value: note } });
  fireEvent.click(button);
  await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith('DEMO', 'prd', 'approved', note, {
    expectedApproval: { run_id: 'run-1', target_type: 'review', target_id: 'prd', artifact_key: 'prd', artifact_version: 'sha256:v1' },
  }));
});

test('typing a rationale does not invoke canvas navigation or zoom shortcuts', async () => {
  render(wrap(props()));
  fireEvent.click(await screen.findByRole('button', { name: 'prd-step' }));
  const input = screen.getByRole('textbox');
  for (const key of ['f', '+', '-', 'ArrowLeft', 'ArrowRight', 'Enter']) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    fireEvent(input, event);
    expect(event.defaultPrevented).toBe(false);
  }
  expect(mocks.fitView).not.toHaveBeenCalled();
  expect(mocks.zoomIn).not.toHaveBeenCalled();
  expect(mocks.zoomOut).not.toHaveBeenCalled();
});

test('changed evidence clears the old reason and missing binding stays disabled', async () => {
  const value = props();
  const view = render(wrap(value));
  fireEvent.click(await screen.findByRole('button', { name: 'prd-step' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Old evidence reason.' } });
  const next = { ...value, reviews: [{ ...value.reviews[0], artifact_version: 'missing' }] };
  view.rerender(wrap(next));
  expect(screen.getByRole('textbox')).toHaveValue('');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'A new reason cannot create a version.' } });
  expect(screen.getByRole('button', { name: 'work.informedApproval.continue' })).toBeDisabled();
  expect(mocks.decide).not.toHaveBeenCalled();
});
