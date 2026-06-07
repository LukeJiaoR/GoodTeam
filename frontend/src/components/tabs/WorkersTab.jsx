import {
  AlertTriangle,
  Check,
  CheckCircle,
  CheckCircle2,
  Info,
  RefreshCw,
  XCircle,
} from 'lucide-react';

function getBackendLabel(backend) {
  if (backend === 'codex') return 'Codex CLI';
  if (backend === 'anthropic' || backend === 'claude') return 'Claude CLI';
  return backend;
}

function SelectControl({ value, onChange, disabled, children, className = '' }) {
  return (
    <select
      value={value}
      onChange={onChange}
      disabled={disabled}
      className={`w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-xl px-3 py-2 text-xs font-bold focus:outline-none focus:border-accent transition-colors ${className}`}
    >
      {children}
    </select>
  );
}

function TextInput({ value, onChange, disabled, placeholder }) {
  return (
    <input
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={onChange}
      disabled={disabled}
      className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-xl px-3 py-2 text-xs font-bold font-mono focus:outline-none focus:border-accent transition-colors"
    />
  );
}

function RoutingModeToggle({ workerRoutingMode, setWorkerRoutingMode }) {
  return (
    <div className="flex items-center gap-2 rounded-xl bg-surface-sunken p-1 border border-border-subtle">
      {['simple', 'advanced'].map((mode) => (
        <button
          key={mode}
          type="button"
          onClick={() => setWorkerRoutingMode(mode)}
          className={`flex-1 px-3 py-1.5 rounded-lg text-2xs font-extrabold uppercase tracking-wider transition-all ${
            workerRoutingMode === mode
              ? 'bg-surface-raised text-accent-text shadow-button'
              : 'text-text-muted hover:text-text-primary'
          }`}
        >
          {mode === 'simple' ? 'Simple' : 'Advanced'}
        </button>
      ))}
    </div>
  );
}

function SimpleRoutingPanel({
  workerRoutingBuckets,
  simpleRoutingBackends,
  setSimpleRoutingBackends,
  savingBackends,
  actionLoading,
  roleBackendOptions,
  getBackendOptionLabel,
  workerRoles,
  handleApplySimpleRouting,
}) {
  return (
    <div className="space-y-3 border border-border-subtle rounded-xl p-4 bg-surface-sunken">
      {workerRoutingBuckets.map((bucket) => (
        <div key={bucket.key} className="space-y-1.5">
          <label className="block text-2xs text-text-muted uppercase font-bold tracking-wider">
            {bucket.label}
          </label>
          <SelectControl
            value={simpleRoutingBackends[bucket.key] || ''}
            onChange={(e) => setSimpleRoutingBackends((prev) => ({ ...prev, [bucket.key]: e.target.value }))}
            disabled={savingBackends || actionLoading}
          >
            <option value="">Inherit global default</option>
            {roleBackendOptions.map((opt) => (
              <option key={opt} value={opt}>
                {getBackendOptionLabel(opt)}
              </option>
            ))}
          </SelectControl>
          <p className="text-2xs text-text-secondary font-medium">
            {bucket.roles.filter((role) => workerRoles.includes(role)).join(', ') || 'No matching roles in this project.'}
          </p>
        </div>
      ))}
      <button
        type="button"
        onClick={handleApplySimpleRouting}
        disabled={savingBackends || actionLoading}
        className="w-full bg-accent hover:bg-accent text-text-inverse border-b-4 border-accent-text active:border-b-0 active:translate-y-1 px-4 py-2 rounded-xl text-xs font-extrabold tracking-wide uppercase transition-all duo-button-physics"
      >
        Apply Simple Routing
      </button>
    </div>
  );
}

function AgentReadinessBadges({ discoveredAgents, safeDiscoveredAgents }) {
  if (discoveredAgents === null || safeDiscoveredAgents.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {safeDiscoveredAgents.map((agent) => (
        <span
          key={agent.backend}
          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-2xs font-extrabold uppercase tracking-wider ${
            agent.ready
              ? 'bg-status-success-bg text-status-success-text border-status-success/30'
              : 'bg-status-warning-bg text-status-warning-text border-status-warning/30'
          }`}
          title={agent.hint || ''}
        >
          {agent.backend} {agent.ready ? 'ready' : 'not-ready'}
        </span>
      ))}
    </div>
  );
}

function RoleRoutingConfig({
  role,
  configuringRoles,
  setConfiguringRoles,
  panelRoleOverrides,
  panelGlobalMode,
  panelRoleModels,
  discoveredAgentByBackend,
  savingBackends,
  actionLoading,
  roleBackendOptions,
  getBackendOptionLabel,
  handleAgentChange,
  customModelSelected,
  fallbackAgentModes,
  handleModelDropdownChange,
  handleCustomModelChange,
}) {
  const isConfiguring = configuringRoles[role];
  const selectedBackend = panelRoleOverrides[role] || '';
  const effectiveBackend = selectedBackend || panelGlobalMode;
  const selectedModel = panelRoleModels[role] || '';
  const selectedAgent = effectiveBackend ? discoveredAgentByBackend[effectiveBackend] : null;
  const isBuiltin = ['fake', 'noop', 'human'].includes(effectiveBackend);
  const hasModel = effectiveBackend && !isBuiltin;

  return (
    <div className="border-b border-border-subtle pb-3.5 pt-1">
      <div className="flex items-center justify-between space-x-4">
        <div className="space-y-1">
          <span className="text-xs font-bold text-text-primary capitalize">{role} Role</span>
          <div className="text-2xs text-text-muted flex items-center space-x-1.5 flex-wrap">
            <span className="font-semibold text-text-secondary">
              {selectedBackend ? getBackendOptionLabel(selectedBackend) : `Inherit default (${getBackendOptionLabel(panelGlobalMode)})`}
            </span>
            {hasModel && (
              <>
                <span className="text-text-muted">/</span>
                <span className="bg-surface-sunken text-text-secondary px-1.5 py-0.5 rounded font-mono text-2xs">
                  {selectedModel || 'default'}
                </span>
              </>
            )}
          </div>
        </div>

        <button
          type="button"
          onClick={() => setConfiguringRoles((prev) => ({ ...prev, [role]: !prev[role] }))}
          className={`px-3 py-1.5 border-2 rounded-xl text-xs font-bold transition-all flex items-center space-x-1 shadow-button ${
            isConfiguring
              ? 'bg-surface-sunken border-border-strong text-text-secondary'
              : 'bg-surface-raised border-border-subtle hover:border-accent text-text-primary'
          }`}
        >
          <span>{isConfiguring ? 'Close' : 'Configure'}</span>
        </button>
      </div>

      {isConfiguring && (
        <div className="mt-3 bg-surface-sunken border border-border-subtle rounded-xl p-4 space-y-4">
          <div>
            <label className="block text-2xs text-text-muted mb-1.5 uppercase font-bold tracking-wider">
              Step 1: Select Agent Backend
            </label>
            <SelectControl
              value={selectedBackend}
              onChange={(e) => handleAgentChange(role, e.target.value)}
              disabled={savingBackends || actionLoading}
            >
              <option value="">Use default</option>
              {roleBackendOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {getBackendOptionLabel(opt)}
                </option>
              ))}
            </SelectControl>
            {selectedAgent && (
              <p className="mt-1 text-2xs text-text-secondary font-medium leading-relaxed">
                {selectedAgent.ready ? 'Ready' : 'Not ready'}
                {selectedAgent.hint ? ` - ${selectedAgent.hint}` : ''}
              </p>
            )}
          </div>

          {effectiveBackend && (
            <div className="border-t border-border-subtle pt-3">
              {isBuiltin ? (
                <div className="text-2xs text-text-secondary font-medium bg-surface-raised border border-border-subtle px-3 py-2 rounded-lg italic">
                  Model selection not applicable for built-in backends.
                </div>
              ) : (
                <div className="space-y-3">
                  <div>
                    <label className="block text-2xs text-text-muted mb-1.5 uppercase font-bold tracking-wider">
                      Step 2: Select Model
                    </label>
                    {(() => {
                      const agentInfo = discoveredAgentByBackend[effectiveBackend];
                      const modelsList = agentInfo?.models || fallbackAgentModes[effectiveBackend]?.models || ['auto'];
                      const defaultModel = agentInfo?.model_default || fallbackAgentModes[effectiveBackend]?.model_default || 'auto';
                      const isCustom = customModelSelected[role] || Boolean(selectedModel && !modelsList.includes(selectedModel));
                      const dropdownVal = isCustom ? '__custom__' : (modelsList.includes(selectedModel) ? selectedModel : (selectedModel ? '__custom__' : defaultModel));

                      return (
                        <div className="space-y-3">
                          <SelectControl
                            value={dropdownVal}
                            onChange={(e) => handleModelDropdownChange(role, e.target.value)}
                            disabled={savingBackends || actionLoading}
                          >
                            {modelsList.map((model) => (
                              <option key={model} value={model}>
                                {model === defaultModel ? `${model} (Default)` : model}
                              </option>
                            ))}
                            <option value="__custom__">Custom...</option>
                          </SelectControl>

                          {isCustom && (
                            <div className="space-y-1">
                              <label className="block text-2xs text-text-muted uppercase font-bold tracking-wider">
                                Custom Model Name
                              </label>
                              <TextInput
                                placeholder="e.g. gpt-5.5-custom"
                                value={selectedModel}
                                onChange={(e) => handleCustomModelChange(role, e.target.value)}
                                disabled={savingBackends || actionLoading}
                              />
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WorkerRoutingPanel(props) {
  const {
    panelGlobalMode,
    setPanelGlobalMode,
    savingBackends,
    actionLoading,
    backendOptions,
    workerRoutingMode,
    setWorkerRoutingMode,
    workerRoutingBuckets,
    simpleRoutingBackends,
    setSimpleRoutingBackends,
    roleBackendOptions,
    getBackendOptionLabel,
    workerRoles,
    handleApplySimpleRouting,
    loadingDiscoveredAgents,
    discoveredAgents,
    safeDiscoveredAgents,
    configuringRoles,
    setConfiguringRoles,
    panelRoleOverrides,
    panelRoleModels,
    discoveredAgentByBackend,
    customModelSelected,
    fallbackAgentModes,
    handleAgentChange,
    handleModelDropdownChange,
    handleCustomModelChange,
    backendsError,
    backendsSuccess,
    handleSaveWorkerBackends,
  } = props;

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card p-6 rounded-2xl space-y-6">
      <div className="border-b border-border-subtle pb-3">
        <h3 className="text-sm font-extrabold text-text-primary">Worker Routing Configuration</h3>
        <p className="text-xs text-text-muted mt-0.5">Configure which agent CLI execution backends are used for each project role.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="space-y-4">
          <div>
            <label className="block text-2xs text-text-muted mb-1.5 uppercase font-bold tracking-wider">Global Default Mode</label>
            <SelectControl
              value={panelGlobalMode}
              onChange={(e) => setPanelGlobalMode(e.target.value)}
              disabled={savingBackends || actionLoading}
            >
              {backendOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {getBackendLabel(opt)}
                </option>
              ))}
            </SelectControl>
          </div>

          <RoutingModeToggle
            workerRoutingMode={workerRoutingMode}
            setWorkerRoutingMode={setWorkerRoutingMode}
          />

          {workerRoutingMode === 'simple' && (
            <SimpleRoutingPanel
              workerRoutingBuckets={workerRoutingBuckets}
              simpleRoutingBackends={simpleRoutingBackends}
              setSimpleRoutingBackends={setSimpleRoutingBackends}
              savingBackends={savingBackends}
              actionLoading={actionLoading}
              roleBackendOptions={roleBackendOptions}
              getBackendOptionLabel={getBackendOptionLabel}
              workerRoles={workerRoles}
              handleApplySimpleRouting={handleApplySimpleRouting}
            />
          )}

          <div className={`space-y-3 ${workerRoutingMode === 'simple' ? 'hidden' : ''}`}>
            <span className="block text-2xs text-text-muted uppercase font-bold tracking-wider">Per-Role Routing Overrides</span>
            {loadingDiscoveredAgents && (
              <p className="text-2xs text-text-muted italic">Loading discovered agents...</p>
            )}
            <AgentReadinessBadges
              discoveredAgents={discoveredAgents}
              safeDiscoveredAgents={safeDiscoveredAgents}
            />
            {workerRoles.map((role) => (
              <RoleRoutingConfig
                key={role}
                role={role}
                configuringRoles={configuringRoles}
                setConfiguringRoles={setConfiguringRoles}
                panelRoleOverrides={panelRoleOverrides}
                panelGlobalMode={panelGlobalMode}
                panelRoleModels={panelRoleModels}
                discoveredAgentByBackend={discoveredAgentByBackend}
                savingBackends={savingBackends}
                actionLoading={actionLoading}
                roleBackendOptions={roleBackendOptions}
                getBackendOptionLabel={getBackendOptionLabel}
                handleAgentChange={handleAgentChange}
                customModelSelected={customModelSelected}
                fallbackAgentModes={fallbackAgentModes}
                handleModelDropdownChange={handleModelDropdownChange}
                handleCustomModelChange={handleCustomModelChange}
              />
            ))}
          </div>

          <div className="pt-2 space-y-3">
            {backendsError && (
              <div className="p-3 bg-status-danger-bg border border-status-danger/30 rounded-xl text-xs font-bold text-status-danger-text flex items-center space-x-2">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span>{backendsError}</span>
              </div>
            )}
            {backendsSuccess && (
              <div className="p-3 bg-status-success-bg border border-status-success/30 rounded-xl text-xs font-bold text-status-success-text flex items-center space-x-2">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>{backendsSuccess}</span>
              </div>
            )}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleSaveWorkerBackends}
                disabled={savingBackends || actionLoading}
                className="bg-accent hover:bg-accent text-text-inverse border-b-4 border-accent-text active:border-b-0 active:translate-y-1 px-5 py-2 rounded-xl text-xs font-extrabold tracking-wide uppercase transition-all flex items-center space-x-2 shadow-button"
              >
                {savingBackends ? (
                  <>
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    <span>Saving...</span>
                  </>
                ) : (
                  <>
                    <Check className="h-3.5 w-3.5" />
                    <span>Save Configurations</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        <PreflightPanel {...props} />
      </div>
    </div>
  );
}

function PreflightPanel({
  usedBackends,
  startContext,
  getBadgeStyle,
  panelGlobalMode,
}) {
  return (
    <div className="space-y-4 border-t lg:border-t-0 lg:border-l border-border-subtle lg:pl-8 pt-4 lg:pt-0">
      <div className="font-extrabold text-2xs text-text-muted uppercase tracking-wider">Backend Readiness Preflight</div>

      <div className="space-y-3.5">
        {usedBackends.map((backend) => {
          const preflight = startContext.project_report?.worker_backends_preflight?.[backend] || {};
          const isReady = preflight.ready ?? false;
          const badge = getBadgeStyle(isReady ? 'success' : 'error');
          const Icon = badge.icon;

          return (
            <div key={backend} className="bg-surface-sunken border border-border-subtle rounded-xl p-3.5 space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-xs font-extrabold text-text-primary capitalize">
                  {getBackendLabel(backend)}
                  {backend === panelGlobalMode && <span className="text-2xs text-text-muted font-normal ml-1.5 lowercase">(global default)</span>}
                </span>

                <span className={`inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-2xs font-extrabold border uppercase tracking-wider ${badge.bg} ${badge.text} ${badge.border}`}>
                  <Icon className="h-3 w-3" />
                  <span>{isReady ? 'Ready' : 'Not Ready'}</span>
                </span>
              </div>

              {preflight.hint ? (
                <p className="text-2xs text-text-secondary font-medium leading-relaxed bg-surface-raised p-2 rounded-lg border border-border-subtle font-mono select-all">
                  {preflight.hint}
                </p>
              ) : (
                <p className="text-2xs text-text-muted italic font-medium leading-relaxed">
                  No additional hints or warnings available.
                </p>
              )}
            </div>
          );
        })}
        {usedBackends.length === 0 && (
          <div className="text-xs text-text-muted italic">
            No backends are currently configured or in use.
          </div>
        )}
      </div>
    </div>
  );
}

function WorkerJobsGrid({ workerJobs }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {workerJobs.map((job) => {
        const isCompleted = job.status === 'completed';
        const isRunning = job.status === 'running';
        const isZeroExit = String(job.summary || '').includes('exit code 0') || isCompleted;

        return (
          <div
            key={job.id}
            className="bg-surface-raised border-2 border-border-subtle shadow-card p-5 rounded-2xl hover:-translate-y-0.5 hover:shadow-card transition-all duration-200 space-y-3"
          >
            <div className="flex justify-between items-center border-b border-border-subtle pb-2">
              <div>
                <h3 className="font-extrabold text-sm text-text-primary leading-snug">{job.role}</h3>
                <span className="text-2xs text-text-muted font-bold capitalize tracking-wide">{job.adapter} adapter</span>
              </div>

              <div className="flex items-center space-x-1.5 shrink-0">
                {isCompleted ? (
                  <span className="bg-status-success-bg text-status-success-text border border-status-success/30 font-extrabold text-2xs uppercase tracking-wider px-2 py-0.5 rounded-full flex items-center space-x-1 select-none">
                    <Check className="h-3 w-3" />
                    <span>Done</span>
                  </span>
                ) : isRunning ? (
                  <span className="bg-status-info-bg text-status-info-text border border-status-info/30 font-extrabold text-2xs uppercase tracking-wider px-2 py-0.5 rounded-full flex items-center space-x-1 animate-pulse select-none">
                    <RefreshCw className="h-2.5 w-2.5 animate-spin" />
                    <span>Running</span>
                  </span>
                ) : (
                  <span className="bg-status-warning-bg text-status-warning-text border border-status-warning/30 font-extrabold text-2xs uppercase tracking-wider px-2 py-0.5 rounded-full select-none">
                    {job.status}
                  </span>
                )}
              </div>
            </div>

            <div className="font-mono text-2xs font-bold text-text-secondary space-y-1.5">
              <div className="flex justify-between items-center">
                <span className="text-text-muted uppercase tracking-wider text-2xs">Job ID</span>
                <span className="select-all text-text-primary">{job.id.substring(0, 12)}...{job.id.slice(-6)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-text-muted uppercase tracking-wider text-2xs">PID</span>
                <span className="text-text-primary font-bold">{job.pid || 'n/a'}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-text-muted uppercase tracking-wider text-2xs">Heartbeat</span>
                <span className="text-text-primary">{job.heartbeat_at ? new Date(job.heartbeat_at).toLocaleTimeString() : 'none'}</span>
              </div>

              <div className="border-t border-border-subtle pt-2 font-sans normal-case text-xs font-semibold leading-relaxed flex items-start space-x-2 text-text-secondary">
                {isCompleted && isZeroExit ? (
                  <CheckCircle className="h-4 w-4 text-accent shrink-0 mt-0.5" />
                ) : isCompleted ? (
                  <XCircle className="h-4 w-4 text-status-danger shrink-0 mt-0.5" />
                ) : (
                  <Info className="h-4 w-4 text-status-info shrink-0 mt-0.5" />
                )}
                <span className="truncate" title={job.summary}>{job.summary || 'Executing task...'}</span>
              </div>
            </div>
          </div>
        );
      })}
      {workerJobs.length === 0 && (
        <div className="col-span-full border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No background worker tasks active or completed.
        </div>
      )}
    </div>
  );
}

export default function WorkersTab(props) {
  const { workerJobs } = props;

  return (
    <div className="space-y-6">
      <WorkerRoutingPanel {...props} />

      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Worker Jobs</h2>
        <p className="text-xs text-text-muted">Live observation of background Codex CLI and agent executors.</p>
      </div>

      <WorkerJobsGrid workerJobs={workerJobs} />
    </div>
  );
}
