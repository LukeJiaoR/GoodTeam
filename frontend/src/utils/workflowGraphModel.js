function normalize(value) {
  return String(value || '').trim();
}

export function displayWorkflowName(name) {
  return normalize(name)
    .replace(/^revise_/, 'revise ')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function nodeByName(nodes) {
  return new Map((Array.isArray(nodes) ? nodes : []).map((node) => [node.name, node]));
}

function plannedIdentifiers(graphData) {
  const levels = Array.isArray(graphData?.execution_plan?.levels)
    ? graphData.execution_plan.levels
    : [];
  return levels.flatMap((level) => {
    if (Array.isArray(level.runnable) && level.runnable.length > 0) return level.runnable;
    return Array.isArray(level.identifiers) ? level.identifiers : [];
  });
}

function rankTable(graphData) {
  const ranks = new Map();
  plannedIdentifiers(graphData).forEach((identifier, index) => {
    if (!ranks.has(identifier)) ranks.set(identifier, index);
  });
  return ranks;
}

function approvalStatusFor(node, run) {
  if (!node?.gate || !run?.approvals) return '';
  return normalize(run.approvals[node.gate]).toLowerCase();
}

export function currentWorkflowNodeName(run) {
  return run?.current_graph_node || run?.current_node || '';
}

export function nextWorkflowNodeName(run) {
  return run?.next_graph_node || run?.next_node || '';
}

function statusFor(node, graphData, ranks) {
  const run = graphData?.run;
  const current = currentWorkflowNodeName(run);
  const next = nextWorkflowNodeName(run);
  const approval = approvalStatusFor(node, run);

  if (node.name === current) return 'active';
  if (node.name === next) return 'queued';
  if (approval === 'changes_requested' || approval === 'rejected') return 'blocked';
  if (approval === 'approved') return 'done';

  const currentRank = ranks.get(current);
  const nodeRank = ranks.get(node.name);
  if (currentRank !== undefined && nodeRank !== undefined && nodeRank < currentRank) {
    return 'done';
  }

  return 'idle';
}

export function relevantWorkflowNodes(graphData) {
  const nodes = nodeByName(graphData?.nodes);
  const ranks = rankTable(graphData);
  const ordered = plannedIdentifiers(graphData)
    .map((name) => nodes.get(name))
    .filter(Boolean)
    .filter((node) => !['terminal', 'revise'].includes(node.kind));

  const extras = [...nodes.values()]
    .filter((node) => !ordered.some((item) => item.name === node.name))
    .filter((node) => ['stage', 'role'].includes(node.kind));

  const hydrated = [...ordered, ...extras].map((node) => ({
    ...node,
    status: statusFor(node, graphData, ranks),
  }));
  const primary = hydrated.filter((node) => ['active', 'queued', 'done', 'blocked'].includes(node.status));
  const idle = hydrated.filter((node) => node.status === 'idle');

  if (primary.length > 0) return { primary, idle };
  return { primary: hydrated.slice(0, 4), idle: hydrated.slice(4) };
}
