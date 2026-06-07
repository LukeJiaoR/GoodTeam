import {
  AlertTriangle,
  Check,
  Info,
  Lock,
  XCircle,
} from 'lucide-react';

const NODE_WIDTH = 200;
const NODE_HEIGHT = 90;
const COLUMN_WIDTH = 270;
const ROW_HEIGHT = 150;
const PADDING_X = 40;
const PADDING_Y = 40;

function computeWorkflowLayout(rawNodes, rawEdges) {
  const nodes = Array.isArray(rawNodes) ? rawNodes : [];
  const edges = Array.isArray(rawEdges) ? rawEdges : [];
  const allNodeNames = new Set(nodes.map((node) => node.name));

  for (const edge of edges) {
    allNodeNames.add(edge.from);
    allNodeNames.add(edge.to);
  }

  const nodeMap = {};
  for (const name of allNodeNames) {
    const originalNode = nodes.find((node) => node.name === name);
    nodeMap[name] = {
      name,
      gate: originalNode?.gate || '',
      runner: originalNode?.runner || '',
      kind: originalNode?.kind || 'stage',
      incoming: [],
      outgoing: [],
      rank: 0,
      index: 0,
    };
  }

  for (const edge of edges) {
    if (nodeMap[edge.from] && nodeMap[edge.to]) {
      nodeMap[edge.from].outgoing.push(edge);
      nodeMap[edge.to].incoming.push(edge);
    }
  }

  const maxIterations = 1000;
  let iterations = 0;
  let changed = true;

  while (changed && iterations < maxIterations) {
    changed = false;
    iterations += 1;

    for (const edge of edges) {
      const fromNode = nodeMap[edge.from];
      const toNode = nodeMap[edge.to];

      if (
        fromNode &&
        toNode &&
        fromNode.rank + 1 > toNode.rank &&
        toNode.rank < Object.keys(nodeMap).length
      ) {
        toNode.rank = fromNode.rank + 1;
        changed = true;
      }
    }
  }

  const columns = {};
  for (const node of Object.values(nodeMap)) {
    if (!columns[node.rank]) {
      columns[node.rank] = [];
    }
    node.index = columns[node.rank].length;
    columns[node.rank].push(node);
  }

  return { nodeMap, columns };
}

function createEdgePath(fromNode, toNode) {
  const fromLeft = PADDING_X + fromNode.rank * COLUMN_WIDTH;
  const fromTop = PADDING_Y + fromNode.index * ROW_HEIGHT;
  const toLeft = PADDING_X + toNode.rank * COLUMN_WIDTH;
  const toTop = PADDING_Y + toNode.index * ROW_HEIGHT;

  if (toNode.rank > fromNode.rank) {
    const x1 = fromLeft + NODE_WIDTH;
    const y1 = fromTop + NODE_HEIGHT / 2;
    const x2 = toLeft - 8;
    const y2 = toTop + NODE_HEIGHT / 2;
    const controlX = (x1 + x2) / 2;

    return {
      path: `M ${x1} ${y1} C ${controlX} ${y1}, ${controlX} ${y2}, ${x2} ${y2}`,
      midX: controlX,
      midY: (y1 + y2) / 2,
    };
  }

  if (toNode.rank === fromNode.rank) {
    const x1 = fromLeft + NODE_WIDTH;
    const y1 = fromTop + NODE_HEIGHT / 2;
    const x2 = toLeft - 8;
    const y2 = toTop + NODE_HEIGHT / 2;
    const controlX = x1 + 40;

    return {
      path: `M ${x1} ${y1} C ${controlX} ${y1}, ${x2 - 40} ${y2}, ${x2} ${y2}`,
      midX: controlX - 20,
      midY: (y1 + y2) / 2,
    };
  }

  const x1 = fromLeft;
  const y1 = fromTop + NODE_HEIGHT / 2;
  const x2 = toLeft - 8;
  const y2 = toTop + NODE_HEIGHT / 2;
  const controlX1 = x1 - 50;
  const controlX2 = x2 - 50;

  return {
    path: `M ${x1} ${y1} C ${controlX1} ${y1}, ${controlX2} ${y2}, ${x2} ${y2}`,
    midX: (controlX1 + controlX2) / 2,
    midY: (y1 + y2) / 2,
  };
}

function getEdgeColor(type) {
  const norm = String(type || '').toLowerCase().trim();
  if (norm === 'approved') return 'var(--success)';
  if (norm === 'changes_requested' || norm === 'blocked') return 'var(--warning)';
  if (norm === 'rejected' || norm === 'error') return 'var(--danger)';
  return 'var(--text-muted)';
}

function getEdgeMarker(type) {
  const norm = String(type || '').toLowerCase().trim();
  if (['approved', 'changes_requested', 'rejected', 'error', 'blocked'].includes(norm)) {
    return `url(#arrow-${norm})`;
  }
  return 'url(#arrow-default)';
}

function getNodeTone({ isCurrent, isNext, node, approvalStatus }) {
  if (isCurrent) {
    return 'border-status-success bg-status-success-bg/35';
  }
  if (isNext) {
    return 'border-status-info bg-status-info-bg/35';
  }
  if (node.gate && approvalStatus === 'approved') {
    return 'border-status-success bg-status-success-bg/25';
  }
  if (node.gate && approvalStatus === 'changes_requested') {
    return 'border-status-warning bg-status-warning-bg/25';
  }
  if (node.gate && approvalStatus === 'rejected') {
    return 'border-status-danger bg-status-danger-bg/25';
  }
  return 'border-border-subtle bg-surface-raised';
}

function ApprovalBadge({ status }) {
  if (status === 'approved') {
    return (
      <span className="bg-status-success-bg border border-status-success/30 rounded-full px-1.5 py-0.5 text-2xs font-bold uppercase flex items-center gap-0.5 text-status-success-text" title="Approved">
        <Check className="h-2.5 w-2.5 text-status-success" />
        <span>approved</span>
      </span>
    );
  }

  if (status === 'changes_requested') {
    return (
      <span className="bg-status-warning-bg border border-status-warning/30 rounded-full px-1.5 py-0.5 text-2xs font-bold uppercase flex items-center gap-0.5 text-status-warning-text" title="Changes Requested">
        <AlertTriangle className="h-2.5 w-2.5 text-status-warning" />
        <span>changes</span>
      </span>
    );
  }

  if (status === 'rejected') {
    return (
      <span className="bg-status-danger-bg border border-status-danger/30 rounded-full px-1.5 py-0.5 text-2xs font-bold uppercase flex items-center gap-0.5 text-status-danger-text" title="Rejected">
        <XCircle className="h-2.5 w-2.5 text-status-danger" />
        <span>rejected</span>
      </span>
    );
  }

  return null;
}

function WorkflowCanvas({ graphData }) {
  const safeNodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
  const safeEdges = Array.isArray(graphData?.edges) ? graphData.edges : [];
  const { nodeMap, columns } = computeWorkflowLayout(safeNodes, safeEdges);
  const colIndices = Object.keys(columns).map(Number).sort((a, b) => a - b);
  const numCols = colIndices.length || 1;
  const maxNodesInAnyCol = Math.max(...Object.values(columns).map((column) => column.length), 1);
  const canvasWidth = numCols * COLUMN_WIDTH + PADDING_X * 2;
  const canvasHeight = maxNodesInAnyCol * ROW_HEIGHT + PADDING_Y * 2;
  const currentNode = graphData.run?.current_graph_node || graphData.run?.current_node;
  const nextNode = graphData.run?.next_graph_node || graphData.run?.next_node;

  return (
    <div
      className="relative select-none"
      style={{ width: `${canvasWidth}px`, height: `${canvasHeight}px` }}
    >
      <svg
        className="absolute inset-0 pointer-events-none z-0"
        style={{ width: '100%', height: '100%' }}
      >
        <defs>
          <marker id="arrow-approved" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L9,3 z" fill="var(--success)" />
          </marker>
          <marker id="arrow-changes_requested" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L9,3 z" fill="var(--warning)" />
          </marker>
          <marker id="arrow-rejected" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L9,3 z" fill="var(--danger)" />
          </marker>
          <marker id="arrow-error" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L9,3 z" fill="var(--danger)" />
          </marker>
          <marker id="arrow-blocked" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L9,3 z" fill="var(--warning)" />
          </marker>
          <marker id="arrow-default" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L9,3 z" fill="var(--text-muted)" />
          </marker>
        </defs>

        {safeEdges.map((edge, idx) => {
          const fromNode = nodeMap[edge.from];
          const toNode = nodeMap[edge.to];
          if (!fromNode || !toNode) return null;

          const { path } = createEdgePath(fromNode, toNode);
          const strokeColor = getEdgeColor(edge.type);
          const marker = getEdgeMarker(edge.type);

          return (
            <path
              key={idx}
              d={path}
              fill="none"
              stroke={strokeColor}
              strokeWidth="2.5"
              markerEnd={marker}
              className="transition-all duration-300 opacity-80"
            />
          );
        })}
      </svg>

      {safeEdges.map((edge, idx) => {
        const fromNode = nodeMap[edge.from];
        const toNode = nodeMap[edge.to];
        if (!fromNode || !toNode) return null;

        const { midX, midY } = createEdgePath(fromNode, toNode);
        const color = getEdgeColor(edge.type);

        return (
          <div
            key={idx}
            className="absolute bg-surface-raised border border-border-subtle rounded-full px-2 py-0.5 text-2xs font-bold shadow-card select-none pointer-events-none z-10"
            style={{
              left: `${midX}px`,
              top: `${midY}px`,
              transform: 'translate(-50%, -50%)',
              color,
            }}
          >
            {edge.type}
          </div>
        );
      })}

      {Object.values(nodeMap).map((node) => {
        const isCurrent = currentNode === node.name;
        const isNext = nextNode === node.name;
        const approvalStatus = node.gate && graphData.run?.approvals
          ? graphData.run.approvals[node.gate]
          : null;
        const left = PADDING_X + node.rank * COLUMN_WIDTH;
        const top = PADDING_Y + node.index * ROW_HEIGHT;
        const nodeTone = getNodeTone({ isCurrent, isNext, node, approvalStatus });

        return (
          <div
            key={node.name}
            className={`absolute border-2 rounded-xl p-4 flex flex-col justify-between z-20 shadow-card ${nodeTone}`}
            style={{
              left: `${left}px`,
              top: `${top}px`,
              width: `${NODE_WIDTH}px`,
              height: `${NODE_HEIGHT}px`,
            }}
          >
            <div className="flex justify-between items-start gap-1">
              <span
                className="font-mono text-2xs font-bold text-text-primary truncate select-all"
                title={node.name}
              >
                {node.name}
              </span>

              <div className="flex items-center space-x-1 shrink-0">
                {node.gate ? <ApprovalBadge status={approvalStatus} /> : null}
                {isCurrent ? (
                  <span className="h-2 w-2 rounded-full bg-status-success animate-pulse" title="Current Node" />
                ) : isNext ? (
                  <span className="h-2 w-2 rounded-full bg-status-info animate-pulse" title="Next Node" />
                ) : null}
              </div>
            </div>

            <div className="space-y-1">
              {node.gate && (
                <div className="flex items-center text-2xs text-text-secondary font-bold">
                  <Lock className="h-3 w-3 mr-1 text-text-muted" />
                  <span className="uppercase">{node.gate} Gate</span>
                </div>
              )}
              <div className="flex justify-between items-center text-2xs font-bold uppercase tracking-wider text-text-muted">
                <span>{node.kind}</span>
                {node.runner && (
                  <span className="font-mono text-2xs max-w-[100px] truncate" title={node.runner}>
                    {node.runner}
                  </span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ActiveRunHeader({ run }) {
  const currentNode = run.current_graph_node || run.current_node;
  const nextNode = run.next_graph_node || run.next_node;

  return (
    <div className="mb-6 bg-surface-sunken p-4 rounded-xl border border-border-subtle flex flex-col gap-3 text-xs font-bold">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center space-x-2">
          <span className="text-text-muted uppercase tracking-wider">Active Run:</span>
          <span className="font-mono text-text-primary">{run.run_id}</span>
        </div>
        <div className="flex flex-wrap gap-4">
          {currentNode && (
            <div className="flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-accent animate-pulse" />
              <span className="text-text-muted">Current Node:</span>
              <span className="text-accent-text font-mono">{currentNode}</span>
            </div>
          )}
          {nextNode && (
            <div className="flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-status-info animate-pulse" />
              <span className="text-text-muted">Next Node:</span>
              <span className="text-status-info-text font-mono">{nextNode}</span>
            </div>
          )}
        </div>
      </div>
      <div className="border-t border-border-subtle pt-2 flex items-center space-x-1.5 text-2xs text-text-secondary font-normal">
        <Info className="h-3.5 w-3.5 text-text-muted shrink-0" />
        <span className="text-text-primary">
          Note: Colored edge pills represent transition path types, not the project's actual gate decisions.
        </span>
      </div>
    </div>
  );
}

export default function WorkflowGraphTab({ loadingGraph, graphError, graphData, Spinner }) {
  const hasGraphData = graphData && (
    (Array.isArray(graphData.nodes) ? graphData.nodes : []).length > 0 ||
    (Array.isArray(graphData.edges) ? graphData.edges : []).length > 0
  );

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Workflow Graph</h2>
        <p className="text-xs text-text-muted">Visual topology of pipeline nodes, decision gates, transition edges, and active execution run path.</p>
      </div>

      {loadingGraph ? (
        <Spinner />
      ) : graphError ? (
        <div className="bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl text-status-danger-text flex items-center space-x-3 shadow-card">
          <AlertTriangle className="h-6 w-6 shrink-0" />
          <span className="font-bold text-sm">{graphError}</span>
        </div>
      ) : !hasGraphData ? (
        <div className="bg-surface-raised border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No workflow graph nodes or edges recorded for this project yet.
        </div>
      ) : (
        <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-6 overflow-x-auto relative">
          {graphData.run ? <ActiveRunHeader run={graphData.run} /> : null}
          <WorkflowCanvas graphData={graphData} />
        </div>
      )}
    </div>
  );
}
