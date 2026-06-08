import {
  ChevronDown,
  Info,
  Play,
  RefreshCw,
  X,
} from 'lucide-react';
import { DELIVERY_MODE_OPTIONS } from '../constants/deliveryModes';

export default function NewProjectModal({
  actionLoading,
  createRemote,
  deliveryContractAcceptance,
  deliveryContractFatalPatterns,
  deliveryContractMinEvidence,
  deliveryContractRequired,
  deliveryMode,
  deliveryStandards,
  deploymentBackendMode,
  dryRun,
  handleCreateConfig,
  handleStartWorkflow,
  idea,
  initGit,
  isNewProjectMode,
  modelProfile,
  onClose,
  openDirectoryPicker,
  planeApiHost,
  planeApiKeyEnv,
  planeMcpServer,
  planeProjectId,
  planeWorkspaceSlug,
  projectConfig,
  projectName,
  projectRoot,
  remotePrivate,
  roleModelOverrides,
  setCreateRemote,
  setDeliveryContractAcceptance,
  setDeliveryContractFatalPatterns,
  setDeliveryContractMinEvidence,
  setDeliveryContractRequired,
  setDeliveryMode,
  setDeliveryStandards,
  setDeploymentBackendMode,
  setDryRun,
  setIdea,
  setInitGit,
  setModelProfile,
  setPlaneApiHost,
  setPlaneApiKeyEnv,
  setPlaneMcpServer,
  setPlaneProjectId,
  setPlaneWorkspaceSlug,
  setProjectConfig,
  setProjectName,
  setProjectRoot,
  setRemotePrivate,
  setRoleModelOverrides,
  setShowConfigDrawer,
  setStagingDeployCommand,
  setStagingGitRef,
  setStagingHealthcheckCommand,
  setStagingPublicUrl,
  setStagingRemoteDir,
  setStagingSmokeCommand,
  setStagingSshTarget,
  setStartMode,
  setStartProjectKey,
  setTaskSync,
  setWorkerBackendMode,
  showConfigDrawer,
  stagingDeployCommand,
  stagingGitRef,
  stagingHealthcheckCommand,
  stagingPublicUrl,
  stagingRemoteDir,
  stagingSmokeCommand,
  stagingSshTarget,
  startingWorkflow,
  startMode,
  startProjectKey,
  taskSync,
  workerBackendMode,
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/50 px-4 py-6 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-project-title"
        className="w-full max-w-4xl bg-surface-overlay border-2 border-border-subtle shadow-card rounded-xl overflow-hidden flex flex-col max-h-[90vh]"
      >
        {/* Header */}
        <div className="px-5 py-4 bg-surface-sunken border-b-2 border-border-subtle flex items-start justify-between gap-4 shrink-0">
          <div>
            <p className="text-2xs font-bold uppercase tracking-wider text-text-muted">Start Workflow Console</p>
            <h3 id="new-project-title" className="text-sm font-bold text-accent-text mt-1">
              Create or Adopt Project
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle hover:border-status-danger/30 shadow-button text-text-secondary hover:text-status-danger p-2 rounded-xl transition-all duo-button-physics cursor-pointer shrink-0"
            aria-label="Close start workflow console"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto space-y-6 flex-grow">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 text-left">
            {/* Left Config Panel */}
            <div className="lg:col-span-2 space-y-6">
              <div className="flex bg-surface-sunken p-1 rounded-lg w-fit">
                <button
                  onClick={() => setStartMode('existing_project')}
                  className={`px-4 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${startMode === 'existing_project' ? 'bg-surface-raised shadow-sm text-accent' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  Adopt Existing Project
                </button>
                <button
                  onClick={() => setStartMode('new_project')}
                  className={`px-4 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${isNewProjectMode ? 'bg-surface-raised shadow-sm text-accent' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  Start New Project
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-bold">
                <div className="col-span-2">
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Workflow Goal</label>
                  <textarea
                    value={idea}
                    onChange={(e) => setIdea(e.target.value)}
                    placeholder={startMode === 'existing_project' ? "Enter takeover goal (e.g. audit frontend files)..." : "Describe the new project and desired features..."}
                    className="w-full h-24 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-4 focus:outline-none focus:border-accent font-mono leading-relaxed transition-colors resize-none"
                  />
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Project Name</label>
                  <input
                    type="text"
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    placeholder="e.g. ContentOps Studio"
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Project Key</label>
                  <input
                    type="text"
                    value={startProjectKey}
                    onChange={(e) => setStartProjectKey(e.target.value)}
                    placeholder="e.g. CONTENTOPS"
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent font-mono uppercase transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Model Profile</label>
                  <select
                    value={modelProfile}
                    onChange={(e) => setModelProfile(e.target.value)}
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent transition-colors"
                  >
                    <option value="balanced">Balanced</option>
                    <option value="cheap">Cheap</option>
                    <option value="max_quality">Max Quality</option>
                  </select>
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Delivery Mode</label>
                  <select
                    value={deliveryMode}
                    onChange={(e) => setDeliveryMode(e.target.value)}
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent transition-colors"
                  >
                    {DELIVERY_MODE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </div>

                {deliveryMode === 'custom' && (
                  <div className="col-span-2">
                    <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Delivery Standards</label>
                    <textarea
                      value={deliveryStandards}
                      onChange={(e) => setDeliveryStandards(e.target.value)}
                      placeholder="每行一条交付标准"
                      className="w-full h-24 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-4 focus:outline-none focus:border-accent font-mono leading-relaxed transition-colors resize-none"
                    />
                  </div>
                )}

                <div className="col-span-2 grid grid-cols-1 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                  <div className="font-bold text-2xs text-text-muted uppercase tracking-wider">Project Location</div>
                  <div>
                    <label className="block text-2xs text-text-secondary mb-1 uppercase">Project Root</label>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={projectRoot}
                        onChange={(e) => setProjectRoot(e.target.value)}
                        placeholder="Absolute path to project root"
                        className="flex-1 min-w-0 bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs font-mono focus:outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() => openDirectoryPicker(projectRoot)}
                        className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-primary font-bold px-2.5 py-1.5 rounded-lg text-xs transition-all duo-button-physics cursor-pointer"
                      >
                        Browse…
                      </button>
                    </div>
                  </div>
                </div>

                {isNewProjectMode && (
                  <div className="col-span-2 grid grid-cols-1 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                    <div className="font-bold text-2xs text-text-muted uppercase tracking-wider">Git Repository Configuration</div>
                    <div className="space-y-4">
                      {/* Initialize Git Checkbox */}
                      <div className="flex items-center space-x-2">
                        <input
                          type="checkbox"
                          id="init-git-repo"
                          checked={initGit}
                          onChange={(e) => setInitGit(e.target.checked)}
                          className="rounded text-accent focus:ring-accent h-4 w-4 cursor-pointer"
                        />
                        <label htmlFor="init-git-repo" className="text-xs font-bold text-text-secondary cursor-pointer">
                          Initialize Git repository
                        </label>
                      </div>

                      {/* Create GitHub Repo Checkbox */}
                      <div className="flex items-center space-x-2">
                        <input
                          type="checkbox"
                          id="create-github-repo"
                          checked={createRemote}
                          onChange={(e) => setCreateRemote(e.target.checked)}
                          className="rounded text-accent focus:ring-accent h-4 w-4 cursor-pointer"
                        />
                        <label htmlFor="create-github-repo" className="text-xs font-bold text-text-secondary cursor-pointer">
                          Create GitHub repo & push
                        </label>
                      </div>

                      {/* Revealed options when createRemote is checked */}
                      {createRemote && (
                        <div className="pl-6 space-y-4 border-l-2 border-accent/30 mt-2">
                          {/* Private / Public Toggle */}
                          <div className="flex items-center space-x-3">
                            <span className="text-xs font-bold text-text-secondary">Repository Visibility:</span>
                            <div className="flex bg-surface-raised p-1 rounded-lg border border-border-subtle select-none">
                              <button
                                type="button"
                                onClick={() => setRemotePrivate(true)}
                                className={`px-3 py-1.5 rounded-md text-2xs font-bold transition-all cursor-pointer ${remotePrivate ? 'bg-accent text-text-inverse shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
                              >
                                Private
                              </button>
                              <button
                                type="button"
                                onClick={() => setRemotePrivate(false)}
                                className={`px-3 py-1.5 rounded-md text-2xs font-bold transition-all cursor-pointer ${!remotePrivate ? 'bg-accent text-text-inverse shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
                              >
                                Public
                              </button>
                            </div>
                          </div>

                          {/* Helper / Callout Box */}
                          <div className="bg-surface-raised text-zinc-900 dark:text-zinc-100 p-4 rounded-xl border border-border-subtle text-2xs leading-relaxed space-y-1.5 shadow-sm">
                            <p className="font-extrabold flex items-center gap-1">
                              <Info className="h-3.5 w-3.5 text-accent shrink-0" />
                              GitHub Authentication Requirement
                            </p>
                            <p>
                              Requires a GitHub Personal Access Token in your environment as <strong className="font-bold font-mono">GITHUB_TOKEN</strong> (or <strong className="font-bold font-mono">GH_TOKEN</strong>) before launching GoodTeam.
                            </p>
                            <p>
                              Token scopes — Classic: <strong className="font-bold font-mono">repo</strong>. Fine-grained: <strong className="font-bold font-mono">Administration: Read & write</strong> + <strong className="font-bold font-mono">Contents: Read & write</strong> (on the target account/org).
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="col-span-2 border-t border-border-subtle my-4" />

                {/* Toggle Advanced Button */}
                <div className="col-span-2">
                  <button
                    type="button"
                    onClick={() => setShowConfigDrawer(!showConfigDrawer)}
                    className="text-xs font-bold text-accent-text hover:text-accent transition-colors flex items-center space-x-1"
                  >
                    <span>{showConfigDrawer ? 'Hide Advanced Config' : 'Show Advanced Config (Task Backend, Deployment)'}</span>
                    <ChevronDown className={`h-4 w-4 transition-transform ${showConfigDrawer ? 'rotate-180' : ''}`} />
                  </button>
                </div>

                {showConfigDrawer && (
                  <>
                    <div className="col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                      <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">Services Backend</div>
                      <div>
                        <label className="block text-2xs text-text-secondary mb-1 uppercase">Task Sync</label>
                        <select
                          value={taskSync}
                          onChange={(e) => setTaskSync(e.target.value)}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
                        >
                          <option value="">Local only</option>
                          <option value="plane">Plane Workspace</option>
                          <option value="linear">Linear Workspace</option>
                          <option value="github_issues">GitHub Issues</option>
                        </select>
                      </div>

                      <div>
                        <label className="block text-2xs text-text-secondary mb-1 uppercase">Worker Execution</label>
                        <select
                          value={workerBackendMode}
                          onChange={(e) => setWorkerBackendMode(e.target.value)}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
                        >
                          <option value="codex">Codex CLI / Autopilot</option>
                          <option value="manual">Human Interactive</option>
                          <option value="noop">Dry-run only</option>
                        </select>
                      </div>

                      {taskSync === 'plane' && (
                        <div className="col-span-2 grid grid-cols-2 gap-3 border-2 border-border-subtle p-4 rounded-xl bg-surface-raised mt-2">
                          <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">Plane Credentials</div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">API Host</label>
                            <input type="text" value={planeApiHost} onChange={(e) => setPlaneApiHost(e.target.value)} className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">Workspace Slug</label>
                            <input type="text" value={planeWorkspaceSlug} onChange={(e) => setPlaneWorkspaceSlug(e.target.value)} placeholder="e.g. leader_kanban" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">Project ID</label>
                            <input type="text" value={planeProjectId} onChange={(e) => setPlaneProjectId(e.target.value)} placeholder="uuid" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">API Key Env</label>
                            <input type="text" value={planeApiKeyEnv} onChange={(e) => setPlaneApiKeyEnv(e.target.value)} className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">MCP Server</label>
                            <input type="text" value={planeMcpServer} onChange={(e) => setPlaneMcpServer(e.target.value)} className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                      <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">Deployment & Evidence Contract</div>
                      <div className="col-span-2">
                        <label className="block text-2xs text-text-secondary mb-1.5 uppercase">Deployment Backend</label>
                        <select
                          value={deploymentBackendMode}
                          onChange={(e) => setDeploymentBackendMode(e.target.value)}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs font-semibold focus:outline-none focus:border-accent"
                        >
                          <option value="">None (skip automated deployments)</option>
                          <option value="ssh_compose">Docker Compose via SSH</option>
                        </select>
                      </div>

                      {deploymentBackendMode === 'ssh_compose' && (
                        <div className="col-span-2 grid grid-cols-2 gap-3 border-2 border-border-subtle p-4 rounded-xl bg-surface-raised mt-2 text-xs">
                          <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">SSH Deploy Configuration</div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">Staging SSH Target</label>
                            <input type="text" value={stagingSshTarget} onChange={(e) => setStagingSshTarget(e.target.value)} placeholder="user@host" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">Staging Remote Dir</label>
                            <input type="text" value={stagingRemoteDir} onChange={(e) => setStagingRemoteDir(e.target.value)} placeholder="/srv/app" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">Staging Git Ref</label>
                            <input type="text" value={stagingGitRef} onChange={(e) => setStagingGitRef(e.target.value)} placeholder="main" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">Staging Public URL</label>
                            <input type="text" value={stagingPublicUrl} onChange={(e) => setStagingPublicUrl(e.target.value)} placeholder="http://url:port" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-secondary mb-1">Deploy Command</label>
                            <input type="text" value={stagingDeployCommand} onChange={(e) => setStagingDeployCommand(e.target.value)} placeholder="docker compose up -d" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">Healthcheck Command</label>
                            <input type="text" value={stagingHealthcheckCommand} onChange={(e) => setStagingHealthcheckCommand(e.target.value)} placeholder="curl -f /health" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">Smoke Command</label>
                            <input type="text" value={stagingSmokeCommand} onChange={(e) => setStagingSmokeCommand(e.target.value)} placeholder="curl -f /" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>

                          <div className="col-span-2 border-t-2 border-border-subtle my-1" />

                          <div className="col-span-2 flex items-center space-x-2">
                            <input type="checkbox" id="contract-req" checked={deliveryContractRequired} onChange={(e) => setDeliveryContractRequired(e.target.checked)} className="rounded text-accent focus:ring-accent h-4 w-4 cursor-pointer" />
                            <label htmlFor="contract-req" className="font-bold text-2xs text-text-muted uppercase tracking-wider cursor-pointer">Require functional delivery evidence</label>
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">Minimum Evidence Gate</label>
                            <select value={deliveryContractMinEvidence} onChange={(e) => setDeliveryContractMinEvidence(e.target.value)} className="bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs focus:outline-none focus:border-accent">
                              <option value="infrastructure">Infrastructure Layer</option>
                              <option value="functional">Functional Application Layer</option>
                            </select>
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">Fatal Output Log Patterns (line separate)</label>
                            <textarea value={deliveryContractFatalPatterns} onChange={(e) => setDeliveryContractFatalPatterns(e.target.value)} placeholder="e.g. migrate: executable file not found" className="w-full h-16 bg-surface-sunken text-text-primary border border-border-subtle rounded p-2 font-mono text-xs resize-none focus:outline-none focus:border-accent" />
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">Staging Acceptance Tests (id|type|command per line)</label>
                            <textarea value={deliveryContractAcceptance} onChange={(e) => setDeliveryContractAcceptance(e.target.value)} placeholder="critical-path|ssh_command|docker compose exec -T backend ./migrate status" className="w-full h-16 bg-surface-sunken text-text-primary border border-border-subtle rounded p-2 font-mono text-xs resize-none focus:outline-none focus:border-accent" />
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="col-span-2">
                      <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Role Model Overrides (one per line, role=model)</label>
                      <textarea
                        value={roleModelOverrides}
                        onChange={(e) => setRoleModelOverrides(e.target.value)}
                        placeholder="architect=codex:gpt-5.5&#10;implementation=codex:gpt-5.3-codex"
                        className="w-full h-20 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-3 font-mono leading-relaxed transition-colors resize-none focus:outline-none focus:border-accent"
                      />
                    </div>

                    <div className="col-span-2">
                      <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Local Project Config Path</label>
                      <input
                        type="text"
                        value={projectConfig}
                        onChange={(e) => setProjectConfig(e.target.value)}
                        placeholder="projects/project-key.local.yaml"
                        className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent font-mono transition-colors"
                      />
                    </div>
                  </>
                )}
              </div>

              {/* Right Status Panel */}
              <div className="bg-surface-sunken border-2 border-border-subtle shadow-card rounded-xl p-5 flex flex-col justify-between h-fit self-start space-y-6">
                <div>
                  <h4 className="font-bold text-2xs uppercase tracking-wider text-text-muted mb-4">Launch Check</h4>
                  <div className="space-y-3.5 text-xs font-semibold">
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">Project Target</span>
                      <span className="font-mono text-text-primary truncate max-w-[150px] font-bold">{startProjectKey || 'GTO'}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">Source Mode</span>
                      <span className="font-bold text-accent-text">{startMode === 'existing_project' ? 'Adoption' : 'New Project'}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">Model Profile</span>
                      <span className="font-bold capitalize">{modelProfile}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">Delivery Mode</span>
                      <span className="font-mono font-bold text-accent">{deliveryMode}</span>
                    </div>
                    <div className="flex justify-between items-center pb-1">
                      <span className="text-text-secondary">Worker Backend</span>
                      <span className="font-bold text-text-primary capitalize">{workerBackendMode}</span>
                    </div>
                  </div>
                </div>

                <div className="border-t-2 border-border-subtle pt-4 space-y-4">
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="dry-run"
                      checked={dryRun}
                      onChange={(e) => setDryRun(e.target.checked)}
                      className="rounded text-accent focus:ring-accent h-4 w-4 cursor-pointer"
                    />
                    <label htmlFor="dry-run" className="text-xs font-bold text-text-secondary cursor-pointer">
                      Dry run simulation
                    </label>
                  </div>

                  <div className="bg-surface-sunken text-2xs text-text-secondary font-mono p-3 rounded-lg border-2 border-border-strong leading-relaxed select-all">
                    {startMode === 'existing_project'
                      ? "Adopt mode reads the project config first and generates takeover-context.md before PRD."
                      : "New project mode generates standard prd.md inside target artifacts folder."}
                  </div>

                  <div className="flex flex-col space-y-2">
                    <button
                      type="button"
                      onClick={handleCreateConfig}
                      disabled={actionLoading}
                      className="w-full bg-surface-raised hover:bg-accent-bg hover:text-accent-text border-2 border-border-subtle shadow-button text-text-secondary font-bold py-2 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics shrink-0 cursor-pointer"
                    >
                      Create Local Config
                    </button>
                    <button
                      type="button"
                      onClick={handleStartWorkflow}
                      disabled={actionLoading || startingWorkflow}
                      className="w-full bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold py-3 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center space-x-2 cursor-pointer hover:opacity-90 hover:shadow-button"
                    >
                      {startingWorkflow ? (
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Play className="h-3.5 w-3.5 fill-current" />
                      )}
                      <span>{startingWorkflow ? 'Starting...' : (dryRun ? 'Preview Workflow' : (startMode === 'existing_project' ? 'Start Adoption' : 'Start New Project'))}</span>
                    </button>
                    {startingWorkflow && (
                      <p className="text-2xs text-zinc-500 text-center mt-2 animate-pulse">
                        Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
