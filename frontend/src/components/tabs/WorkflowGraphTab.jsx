import {
  AlertTriangle,
  Check,
  Circle,
  Clock,
  GitBranch,
  Info,
  PauseCircle,
  Play,
  XCircle,
} from 'lucide-react';
import {
  currentWorkflowNodeName,
  displayWorkflowName,
  nextWorkflowNodeName,
  relevantWorkflowNodes,
} from '../../utils/workflowGraphModel';

const STATUS_META = {
  active: {
    label: 'active',
    icon: Play,
    nodeClass: 'border-status-success bg-status-success-bg/35 shadow-card',
    railClass: 'bg-status-success',
    textClass: 'text-status-success-text',
  },
  queued: {
    label: 'queued',
    icon: Clock,
    nodeClass: 'border-status-info bg-status-info-bg/30',
    railClass: 'bg-status-info',
    textClass: 'text-status-info-text',
  },
  done: {
    label: 'done',
    icon: Check,
    nodeClass: 'border-status-success/35 bg-surface-raised',
    railClass: 'bg-status-success',
    textClass: 'text-status-success-text',
  },
  idle: {
    label: 'idle',
    icon: PauseCircle,
    nodeClass: 'border-border-subtle bg-surface-sunken',
    railClass: 'bg-text-muted',
    textClass: 'text-text-muted',
  },
  blocked: {
    label: 'blocked',
    icon: AlertTriangle,
    nodeClass: 'border-status-warning bg-status-warning-bg/30',
    railClass: 'bg-status-warning',
    textClass: 'text-status-warning-text',
  },
};

function EdgeLegend() {
  const statuses = ['active', 'queued', 'idle'];
  return (
    <div className="flex flex-wrap items-center gap-2 text-2xs font-bold uppercase text-text-muted">
      {statuses.map((status) => {
        const meta = STATUS_META[status];
        return (
          <span key={status} className="inline-flex items-center gap-1 rounded-full border border-border-subtle bg-surface-raised px-2 py-1">
            <span className={`h-2 w-2 rounded-full ${meta.railClass}`} />
            {meta.label}
          </span>
        );
      })}
    </div>
  );
}

function StatusPill({ status }) {
  const meta = STATUS_META[status] || STATUS_META.idle;
  const Icon = meta.icon || Circle;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-bold uppercase ${meta.textClass}`}>
      <Icon className="h-3 w-3" />
      {meta.label}
    </span>
  );
}

function WorkerNode({ node, compact = false }) {
  const meta = STATUS_META[node.status] || STATUS_META.idle;
  return (
    <div
      className={`relative min-w-[170px] overflow-hidden rounded-lg border-2 p-3 transition-all ${meta.nodeClass}`}
      title={`${displayWorkflowName(node.name)} · ${node.runner || node.kind || 'workflow node'}`}
    >
      <span className={`absolute inset-y-0 left-0 w-1.5 ${meta.railClass}`} />
      <div className="flex items-start justify-between gap-3 pl-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-extrabold text-text-primary">{displayWorkflowName(node.name)}</div>
          <div className="mt-1 truncate text-2xs font-bold uppercase text-text-muted">
            {compact ? node.kind : node.gate ? `${node.gate} gate` : node.kind}
          </div>
        </div>
        <StatusPill status={node.status} />
      </div>
    </div>
  );
}

function Connector({ muted = false }) {
  const lineClass = muted ? 'border-t-2 border-dashed border-border-subtle' : 'bg-border-strong';
  const arrowClass = muted ? 'border-border-subtle' : 'border-border-strong';
  return (
    <div className="hidden min-w-8 flex-1 items-center md:flex" aria-hidden="true">
      <div className={`h-0.5 w-full ${lineClass}`} />
      <div className={`h-2 w-2 rotate-45 border-r-2 border-t-2 ${arrowClass}`} />
    </div>
  );
}

function ActiveRunHeader({ run }) {
  const current = currentWorkflowNodeName(run);
  const next = nextWorkflowNodeName(run);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle bg-surface-sunken px-4 py-3 text-xs font-bold">
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center gap-2 text-status-success-text">
          <span className="h-2.5 w-2.5 rounded-full bg-status-success animate-pulse" />
          Run active
        </span>
        {current ? <span className="font-mono text-text-primary">Current: {current}</span> : null}
        {next ? <span className="font-mono text-text-secondary">Next: {next}</span> : null}
      </div>
      <span className="truncate font-mono text-2xs text-text-muted" title={run.run_id}>
        {run.run_id}
      </span>
    </div>
  );
}

function EmptyRunHeader() {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border-subtle bg-surface-sunken px-4 py-3 text-xs font-bold text-text-secondary">
      <Info className="h-4 w-4 text-text-muted" />
      No active run; showing enabled workflow capabilities.
    </div>
  );
}

function DynamicWorkflowMap({ graphData }) {
  const { primary, idle } = relevantWorkflowNodes(graphData);
  const activeRun = graphData?.run;

  return (
    <div className="rounded-2xl border-2 border-border-subtle bg-surface-raised p-5 shadow-card">
      <div className="mb-5 space-y-4">
        {activeRun ? <ActiveRunHeader run={activeRun} /> : <EmptyRunHeader />}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-extrabold text-text-primary">Run Focus</h3>
            <p className="text-2xs text-text-muted">
              Nodes are weighted by the current run; inactive enabled workers stay visible below.
            </p>
          </div>
          <EdgeLegend />
        </div>
      </div>

      <div className="overflow-x-auto pb-2">
        <div className="flex min-w-max items-stretch gap-0">
          {primary.map((node, index) => (
            <div key={node.name} className="flex items-center">
              <WorkerNode node={node} />
              {index < primary.length - 1 ? <Connector /> : null}
            </div>
          ))}
        </div>
      </div>

      {idle.length > 0 ? (
        <div className="mt-6 border-t border-border-subtle pt-4">
          <div className="mb-3 flex items-center gap-2 text-2xs font-extrabold uppercase text-text-muted">
            <GitBranch className="h-3.5 w-3.5" />
            Enabled but idle
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {idle.map((node) => (
              <WorkerNode key={node.name} node={node} compact />
            ))}
          </div>
        </div>
      ) : null}
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
        <p className="text-xs text-text-muted">
          Dynamic view of active run focus and enabled workflow capabilities.
        </p>
      </div>

      {loadingGraph ? (
        <Spinner />
      ) : graphError ? (
        <div className="bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl text-status-danger-text flex items-center space-x-3 shadow-card">
          <XCircle className="h-6 w-6 shrink-0" />
          <span className="font-bold text-sm">{graphError}</span>
        </div>
      ) : !hasGraphData ? (
        <div className="bg-surface-raised border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No workflow graph nodes or edges recorded for this project yet.
        </div>
      ) : (
        <DynamicWorkflowMap graphData={graphData} />
      )}
    </div>
  );
}
