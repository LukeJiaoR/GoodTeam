// Layer simple directed flows without scaling their labels to the viewport.
export function layoutFlowchart(value) {
  const ranks = new Map(value.nodes.map(node => [node.id, 0]));
  const incoming = new Map(value.nodes.map(node => [node.id, 0]));
  const outgoing = new Map(value.nodes.map(node => [node.id, []]));
  for (const edge of value.edges) {
    incoming.set(edge.to, incoming.get(edge.to) + 1);
    outgoing.get(edge.from).push(edge.to);
  }
  const queue = value.nodes.filter(node => incoming.get(node.id) === 0).map(node => node.id);
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index];
    for (const target of outgoing.get(id)) {
      ranks.set(target, Math.max(ranks.get(target), ranks.get(id) + 1));
      incoming.set(target, incoming.get(target) - 1);
      if (incoming.get(target) === 0) queue.push(target);
    }
  }
  if (queue.length !== value.nodes.length) return null;
  const levels = Array.from({ length: Math.max(...ranks.values()) + 1 }, () => []);
  for (const node of value.nodes) levels[ranks.get(node.id)].push(node);
  const contentWidth = Math.max(...levels.map(level => level.length)) * 200;
  let top = 24;
  const nodes = levels.flatMap((level, rank) => {
    const placed = level.map((node, index) => {
      const decision = node.kind === 'decision';
      const labelWidth = [...node.label].reduce((width, character) => width + (character.charCodeAt(0) > 255 ? 14 : 9), 0);
      const lines = Math.ceil(labelWidth / (decision ? 72 : 120));
      return { ...node, rank, x: (contentWidth - level.length * 200) / 2 + index * 200 + 28,
        y: top, width: 160, height: Math.max(84, lines * (decision ? 40 : 20) + 32) };
    });
    top += Math.max(...placed.map(node => node.height)) + 64;
    return placed;
  });
  const byId = new Map(nodes.map(node => [node.id, node]));
  const edges = value.edges.map((edge, index) => {
    const from = byId.get(edge.from), to = byId.get(edge.to);
    const startX = from.x + from.width / 2, startY = from.y + from.height;
    const endX = to.x + to.width / 2, endY = to.y;
    if (to.rank === from.rank + 1) {
      const middleY = (startY + endY) / 2;
      return { ...edge, path: `M ${startX} ${startY} V ${middleY} H ${endX} V ${endY - 5}`,
        labelX: (startX + endX) / 2, labelY: middleY - 9 };
    }
    // A connection skipping levels uses an outer lane, clear of intervening nodes.
    const lane = contentWidth + 28 + index * 14;
    return { ...edge, path: `M ${startX} ${startY} V ${startY + 18} H ${lane} V ${endY - 20} H ${endX} V ${endY - 5}`,
      labelX: lane, labelY: (startY + endY) / 2 };
  });
  const hasOuterLane = value.edges.some(edge => byId.get(edge.to).rank > byId.get(edge.from).rank + 1);
  return { nodes, edges, width: contentWidth + (hasOuterLane ? 56 + value.edges.length * 14 : 16), height: top - 40 };
}
