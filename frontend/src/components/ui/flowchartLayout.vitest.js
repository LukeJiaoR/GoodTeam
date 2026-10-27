import { expect, it } from 'vitest';
import { layoutFlowchart } from './flowchartLayout';
import { parseChatRichContent } from './chatRichContentParser';

const flow = {
  type: 'flowchart', title: 'Check and continue',
  nodes: [{ id: 'end', label: 'Done', kind: 'terminal' }, { id: 'check', label: 'Valid?', kind: 'decision' },
    { id: 'fix', label: 'Ask for details' }, { id: 'start', label: 'Start', kind: 'terminal' }],
  edges: [{ from: 'start', to: 'check' }, { from: 'check', to: 'end', label: 'Yes' },
    { from: 'check', to: 'fix', label: 'No' }, { from: 'fix', to: 'end' }],
};

it('places nodes by connections, including branches, merges and skipped levels', () => {
  expect(parseChatRichContent(JSON.stringify(flow))).toEqual(flow);
  const layout = layoutFlowchart(flow);
  const nodes = Object.fromEntries(layout.nodes.map(node => [node.id, node]));
  expect(nodes.start.y + nodes.start.height).toBeLessThan(nodes.check.y);
  expect(nodes.check.y + nodes.check.height).toBeLessThan(nodes.fix.y);
  expect(nodes.fix.y + nodes.fix.height).toBeLessThan(nodes.end.y);
  expect(layout.edges.every(edge => !edge.path.includes('NaN'))).toBe(true);
  expect(layout.nodes.every(node => node.x >= 0 && node.x + node.width <= layout.width && node.y + node.height <= layout.height)).toBe(true);
  expect(flow.nodes[0]).not.toHaveProperty('x');
});

it('rejects dangling edges, duplicate IDs, unsupported shapes, cycles and oversized flows', () => {
  for (const value of [
    { ...flow, edges: [{ from: 'start', to: 'missing' }] },
    { ...flow, nodes: [flow.nodes[0], flow.nodes[0]] },
    { ...flow, nodes: [{ id: 'x', label: 'Click me', kind: 'html' }] },
    { ...flow, edges: [...flow.edges, { from: 'end', to: 'start' }] },
    { ...flow, nodes: Array.from({ length: 13 }, (_, index) => ({ id: `n${index}`, label: 'Step' })), edges: [] },
    { ...flow, nodes: [{ id: 'x', label: 'a'.repeat(81) }], edges: [] },
    { ...flow, edges: [{ from: 'start', to: 'end', label: 'a'.repeat(41) }] },
  ]) expect(parseChatRichContent(JSON.stringify(value))).toBeNull();
});

it('reserves more room for long labels and keeps isolated steps visible', () => {
  const layout = layoutFlowchart({ nodes: [{ id: 'long', label: '标签'.repeat(40), kind: 'decision' }, { id: 'alone', label: 'Standalone step' }], edges: [] });
  expect(layout.nodes[0].height).toBeGreaterThan(84);
  expect(layout.nodes[0].x + layout.nodes[0].width).toBeLessThan(layout.nodes[1].x);
  expect(layout.height).toBeGreaterThan(layout.nodes[0].height);
});
