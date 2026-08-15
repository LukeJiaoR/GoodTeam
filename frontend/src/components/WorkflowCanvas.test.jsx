import { act, render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { translate } from '../i18n';
import WorkflowCanvas, { buildCanvasNodes, canvasLevel } from './WorkflowCanvas';
import { reviewApi } from '../features/reviews/reviewApi';

const viewport = vi.hoisted(() => ({ onChange: null }));

vi.mock('@xyflow/react', async () => {
  const React = await import('react');
  return {
    ReactFlow: ({ children, nodes, onNodeClick }) => (
      <div data-testid="reactflow">
        {nodes.map((node) => (
          <button type="button" key={node.id} data-node={node.id} data-status={node.data.step.status} data-focused={node.data.focused ? 'true' : 'false'} onClick={() => onNodeClick?.(null, node)}>
            {node.data.title}
          </button>
        ))}
        {children}
      </div>
    ),
    Background: () => null,
    Panel: ({ children }) => <div data-testid="canvas-panel">{children}</div>,
    ReactFlowProvider: ({ children }) => <div>{children}</div>,
    useNodesState: (initial) => {
      const state = React.useState(initial);
      return [...state, () => {}];
    },
    useReactFlow: () => ({ fitView: () => {}, zoomIn: () => {}, zoomOut: () => {} }),
    useOnViewportChange: ({ onChange }) => { viewport.onChange = onChange; },
  };
});

vi.mock('../features/reviews/reviewApi', () => ({
  reviewApi: { decideReview: vi.fn() },
}));

const steps = [
  { key: 'takeover', number: '1', title: 'Takeover Context', status: 'done', approval: 'takeover', status_label: 'Done' },
  { key: 'prd', number: '2', title: 'Product / Requirement', status: 'review-needed', approval: 'prd', status_label: 'Needs review', feedback: 'Human review required.' },
  { key: 'rfc', number: '3', title: 'Architecture', status: 'not-started', approval: 'rfc', status_label: 'Not started' },
];
const reviews = [
  { approval: 'prd', title: 'PRD', is_actionable: true, evidence_snapshot: { ci_status: 'passed', smoke_status: 'unknown', qa_status: 'failed', blocker_count: 1 } },
];

function t(key, params) { return translate('en', key, params); }

function renderCanvas(props = {}) {
  return render(
    <MemoryRouter>
      <WorkflowCanvas projectKey="DEMO" steps={steps} reviews={reviews} t={t} {...props} />
    </MemoryRouter>,
  );
}

test('buildCanvasNodes lays out stages in wrapped rows with translated labels', () => {
  const nodes = buildCanvasNodes(steps, 0);
  expect(nodes).toHaveLength(3);
  expect(nodes[1].position.x).toBe(250);
  expect(nodes[0].data.title).toBe('Takeover');
  expect(nodes[1].data.label).toBe('Needs review');
});

test('canvasLevel maps zoom to the three semantic levels', () => {
  expect(canvasLevel(0.6)).toBe(0);
  expect(canvasLevel(1.0)).toBe(1);
  expect(canvasLevel(1.6)).toBe(2);
});

test('falls back to the pipeline view when requested', () => {
  renderCanvas({ fallback: true });
  expect(screen.getByRole('navigation', { name: 'Workflow pipeline' })).toBeVisible();
  expect(screen.getByText('Using the simplified pipeline view.')).toBeVisible();
});

test('renders every stage as a canvas node with status data', () => {
  renderCanvas();
  expect(screen.getByRole('button', { name: 'Product / Requirement' })).toHaveAttribute('data-status', 'review-needed');
  expect(screen.getByRole('button', { name: 'Takeover' })).toHaveAttribute('data-status', 'done');
});

test('updates the level indicator from viewport zoom', () => {
  renderCanvas();
  expect(screen.getByText('Overview')).toBeVisible();
  act(() => { viewport.onChange({ zoom: 1.5 }); });
  expect(screen.getByText('Details')).toBeVisible();
});

test('opens the gate detail card and approves from it', async () => {
  const user = userEvent.setup();
  reviewApi.decideReview.mockResolvedValue({ ok: true });
  renderCanvas();

  await user.click(screen.getByRole('button', { name: 'Product / Requirement' }));
  expect(await screen.findByRole('dialog', { name: 'Stage details' })).toBeVisible();
  expect(screen.getByText('Human review required.')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Open full review' })).toHaveAttribute('href', '/projects/DEMO/review/prd');
  expect(screen.getByText('CI: Passed')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Approve' }));
  expect(reviewApi.decideReview).toHaveBeenCalledWith('DEMO', 'prd', 'approved', '');
});

test('keyboard arrows move focus and Escape closes the detail card', () => {
  renderCanvas();
  const canvas = document.querySelector('.workflow-canvas');
  fireEvent.keyDown(canvas, { key: 'ArrowRight' });
  fireEvent.keyDown(canvas, { key: 'ArrowRight' });
  fireEvent.keyDown(canvas, { key: 'Enter' });
  expect(screen.getByRole('dialog', { name: 'Stage details' })).toBeVisible();
  fireEvent.keyDown(canvas, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: 'Stage details' })).not.toBeInTheDocument();
});
