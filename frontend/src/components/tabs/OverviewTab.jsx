import {
  AlertTriangle,
  ChevronDown,
  Play,
  RefreshCw,
} from 'lucide-react';

import { DELIVERY_MODE_OPTIONS } from '../../constants/deliveryModes';

export default function OverviewTab({
  state,
  headerStatus,
  roleObservations,
  approvedCount,
  totalApprovalsCount,
  missionExpanded,
  setMissionExpanded,
  getMissionSummary,
  handleRetryBlocked,
  actionLoading,
  updateUrl,
  setActiveTab,
  startContext,
  FilePath,
  activeRun,
  startMode,
  setStartMode,
  isNewProjectMode,
  idea,
  setIdea,
  modelProfile,
  setModelProfile,
  deliveryMode,
  setDeliveryMode,
  deliveryStandards,
  setDeliveryStandards,
  dryRun,
  setDryRun,
  handleStartWorkflow,
  startingWorkflow,
  getBadgeStyle,
  getStatusLabel,
}) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
        {[
          { label: 'Run Status', val: headerStatus, isBadge: true },
          { label: 'Total Roles', val: String(roleObservations.length || 0), isBadge: false },
          { label: 'Approvals', val: `${approvedCount}/${totalApprovalsCount}`, isBadge: false },
          { label: 'Artifacts', val: String(state?.artifacts ? Object.keys(state.artifacts).length : 0), isBadge: false },
        ].map((sig, idx) => {
          const statBadge = getBadgeStyle(sig.val);
          return (
            <div key={idx} className="bg-surface-raised border-2 border-border-subtle shadow-card p-5 rounded-xl transition-all duration-200 theme-card-hover flex flex-col justify-between h-28">
              <span className="text-2xs font-bold text-text-muted uppercase tracking-wider block">{sig.label}</span>
              {sig.isBadge ? (
                <span className={`inline-block px-3 py-1 rounded-full text-xs font-bold uppercase border w-fit ${statBadge.bg} ${statBadge.text} ${statBadge.border}`}>
                  {getStatusLabel(sig.val)}
                </span>
              ) : (
                <span className="text-3xl font-bold text-text-primary leading-none">{sig.val}</span>
              )}
            </div>
          );
        })}
      </div>

      {state && (
        <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-xl p-6 space-y-4">
          <h4 className="text-2xs font-bold text-text-muted uppercase tracking-wider">Current Mission</h4>
          <p className="text-sm font-semibold text-text-secondary leading-relaxed font-mono select-all">
            {missionExpanded ? state.idea : getMissionSummary(state.idea)}
          </p>

          {state.idea && state.idea.match(/[^.!?]+[.!?]+/g)?.length > 2 && (
            <button
              type="button"
              onClick={() => setMissionExpanded(!missionExpanded)}
              className="text-xs font-bold text-accent-text hover:underline flex items-center space-x-1 cursor-pointer"
            >
              <span>{missionExpanded ? '收起全文 ▴' : '展开全文 ▾'}</span>
            </button>
          )}
        </div>
      )}

      {state?.blockers && state.blockers.length > 0 && (
        <div className="bg-status-danger-bg border-2 border-status-danger p-5 rounded-xl shadow-card space-y-4">
          <div className="flex items-start space-x-3">
            <AlertTriangle className="h-5.5 w-5.5 text-status-danger shrink-0 mt-0.5" />
            <div>
              <h4 className="font-bold text-status-danger-text text-sm uppercase tracking-wide">Execution Blocked</h4>
              <ul className="list-disc pl-5 mt-2 space-y-1 text-xs text-status-danger-text font-mono leading-relaxed">
                {state.blockers.map((blocker, idx) => <li key={idx}>{blocker}</li>)}
              </ul>
            </div>
          </div>
          <div className="flex justify-end pt-3 border-t-2 border-status-danger/15">
            <button
              onClick={handleRetryBlocked}
              disabled={actionLoading}
              className="bg-status-warning border-2 border-status-warning-text shadow-button text-text-inverse font-bold text-xs uppercase tracking-wider px-4 py-2 rounded-xl transition-all duo-button-physics flex items-center space-x-2 cursor-pointer hover:opacity-90"
            >
              <RefreshCw className="h-3.5 w-3.5 animate-pulse" />
              <span>Retry Blocked Workers</span>
            </button>
          </div>
        </div>
      )}

      <div className="flex space-x-4">
        <button
          onClick={() => updateUrl('')}
          className="bg-accent border-2 border-accent-text shadow-button text-text-inverse rounded-xl font-bold uppercase tracking-wider text-xs px-5 py-3 transition-all duo-button-physics cursor-pointer hover:opacity-90"
        >
          Open Project Overview
        </button>
        <button
          onClick={() => setActiveTab('approvals')}
          className="bg-surface-raised border-2 border-border-subtle hover:bg-surface-sunken shadow-card text-text-secondary rounded-xl font-bold uppercase tracking-wider text-xs px-5 py-3 transition-all duo-button-physics cursor-pointer"
        >
          Review Release Notes
        </button>
      </div>

      {state && (
        <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-xl overflow-hidden">
          <div className="px-6 py-4 bg-surface-sunken border-b-2 border-border-subtle">
            <h3 className="font-bold text-2xs text-text-muted uppercase tracking-wider">Run Environment Configuration</h3>
          </div>
          <div className="p-6">
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4 text-xs font-semibold">
              <div className="border-b border-border-subtle pb-2 flex justify-between items-center">
                <dt className="text-text-muted uppercase text-2xs">State Config Path</dt>
                <dd><FilePath path={state.context_packet_path} segments={1} /></dd>
              </div>
              <div className="border-b border-border-subtle pb-2 flex justify-between items-center">
                <dt className="text-text-muted uppercase text-2xs">Local Task Ledger</dt>
                <dd><FilePath path={state.artifacts?.task_ledger} segments={1} /></dd>
              </div>
              <div className="border-b border-border-subtle pb-2 flex justify-between items-center">
                <dt className="text-text-muted uppercase text-2xs">Docs Repository</dt>
                <dd><FilePath path={startContext.project_report?.repos?.docs?.path} segments={1} /></dd>
              </div>
              <div className="border-b border-border-subtle pb-2 flex justify-between items-center">
                <dt className="text-text-muted uppercase text-2xs">Backend Repository</dt>
                <dd><FilePath path={startContext.project_report?.repos?.backend?.path} segments={1} /></dd>
              </div>
              <div className="border-b border-border-subtle pb-2 flex justify-between items-center">
                <dt className="text-text-muted uppercase text-2xs">Frontend Repository</dt>
                <dd><FilePath path={startContext.project_report?.repos?.frontend?.path} segments={1} /></dd>
              </div>
              <div className="border-b border-border-subtle pb-2 flex justify-between items-center">
                <dt className="text-text-muted uppercase text-2xs">Staging SSH Target</dt>
                <dd className="font-mono text-text-secondary">{startContext.deployment_backend?.staging_ssh_target || 'not configured'}</dd>
              </div>
            </dl>
          </div>
        </div>
      )}

      {!activeRun && (
        <details className="group border-2 border-border-subtle shadow-card rounded-xl overflow-hidden bg-surface-raised">
          <summary className="px-6 py-4 font-bold text-xs uppercase tracking-wider text-accent-text hover:bg-surface-sunken transition-colors cursor-pointer list-none flex justify-between items-center select-none">
            <span>Start another run for this project</span>
            <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180 text-text-muted" />
          </summary>
          <div className="p-6 border-t-2 border-border-subtle space-y-6">
            <div className="flex bg-surface-sunken p-1 rounded-lg w-fit text-xs mb-4">
              <button onClick={() => setStartMode('existing_project')} className={`px-3 py-1.5 rounded-lg font-bold transition-all cursor-pointer ${startMode === 'existing_project' ? 'bg-surface-raised shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}>Adopt Existing Project</button>
              <button onClick={() => setStartMode('new_project')} className={`px-3 py-1.5 rounded-lg font-bold transition-all cursor-pointer ${isNewProjectMode ? 'bg-surface-raised shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}>Start New Project</button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-bold">
              <div className="col-span-2">
                <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Goal / Idea</label>
                <textarea value={idea} onChange={(event) => setIdea(event.target.value)} placeholder="Explain the workflow target..." className="w-full h-20 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-3 font-mono leading-relaxed focus:outline-none focus:border-accent" />
              </div>
              <div>
                <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Model Profile</label>
                <select value={modelProfile} onChange={(event) => setModelProfile(event.target.value)} className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-3 py-2 focus:outline-none focus:border-accent">
                  <option value="balanced">Balanced</option>
                  <option value="cheap">Cheap</option>
                  <option value="max_quality">Max Quality</option>
                </select>
              </div>
              <div>
                <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Delivery Mode</label>
                <select value={deliveryMode} onChange={(event) => setDeliveryMode(event.target.value)} className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-3 py-2 focus:outline-none focus:border-accent">
                  {DELIVERY_MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
              {deliveryMode === 'custom' && (
                <div className="col-span-2">
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Delivery Standards</label>
                  <textarea
                    value={deliveryStandards}
                    onChange={(event) => setDeliveryStandards(event.target.value)}
                    placeholder="每行一条交付标准"
                    className="w-full h-20 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-3 font-mono leading-relaxed resize-none focus:outline-none focus:border-accent"
                  />
                </div>
              )}

              <div className="col-span-2 border-t-2 border-border-subtle my-2" />

              <div className="col-span-2 flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <input type="checkbox" id="run-dry" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} className="rounded text-accent focus:ring-accent h-4 w-4 cursor-pointer" />
                  <label htmlFor="run-dry" className="font-bold text-text-secondary uppercase tracking-wider text-2xs cursor-pointer">Dry run simulation</label>
                </div>
                <div className="flex flex-col items-end">
                  <button
                    onClick={handleStartWorkflow}
                    disabled={actionLoading || startingWorkflow}
                    className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-6 py-2.5 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-2 cursor-pointer hover:opacity-90"
                  >
                    {startingWorkflow ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Play className="h-3.5 w-3.5 fill-current" />
                    )}
                    <span>{startingWorkflow ? 'Starting...' : (dryRun ? 'Preview Workflow' : 'Start Workflow')}</span>
                  </button>
                  {startingWorkflow && (
                    <span className="text-2xs text-text-muted mt-2 text-right animate-pulse max-w-xs">
                      Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
