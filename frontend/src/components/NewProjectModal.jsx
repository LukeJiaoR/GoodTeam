import {
  ChevronDown,
  Calculator,
  Info,
  Plus,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
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
  contextEstimate,
  contextSources,
  deliveryMode,
  deliveryStandards,
  deploymentBackendMode,
  dryRun,
  generatingContext,
  handleCreateConfig,
  handleContextEstimate,
  handleContextGenerate,
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
  setContextEstimate,
  setContextSources,
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
  previewingContext,
  startingWorkflow,
  startMode,
  startProjectKey,
  taskSync,
  t = (key) => key,
  workerBackendMode,
}) {
  const deliveryModeLabel = (option) => {
    const key = `newProject.deliveryModes.${option.value}`;
    const label = t(key);
    return label === key ? option.label : label;
  };
  const updateContextSource = (index, patch) => {
    setContextSources(contextSources.map((source, sourceIndex) => (
      sourceIndex === index ? { ...source, ...patch } : source
    )));
    setContextEstimate(null);
  };
  const addContextSource = () => {
    setContextSources([...contextSources, { kind: 'dir', value: '' }]);
    setContextEstimate(null);
  };
  const removeContextSource = (index) => {
    const nextSources = contextSources.filter((_, sourceIndex) => sourceIndex !== index);
    setContextSources(nextSources.length > 0 ? nextSources : [{ kind: 'dir', value: '' }]);
    setContextEstimate(null);
  };
  const hasContextSources = contextSources.some((source) => source.value.trim());
  const contextTokenCount = contextEstimate?.estimate?.est_tokens ?? 0;
  const contextModel = contextEstimate?.recommended_model ?? '';

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
            <p className="text-2xs font-bold uppercase tracking-wider text-text-muted">{t('newProject.consoleEyebrow')}</p>
            <h3 id="new-project-title" className="text-sm font-bold text-accent-text mt-1">
              {t('newProject.title')}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle hover:border-status-danger/30 shadow-button text-text-secondary hover:text-status-danger p-2 rounded-xl transition-all duo-button-physics cursor-pointer shrink-0"
            aria-label={t('newProject.closeAria')}
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
                  {t('newProject.mode.adopt')}
                </button>
                <button
                  onClick={() => setStartMode('new_project')}
                  className={`px-4 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${isNewProjectMode ? 'bg-surface-raised shadow-sm text-accent' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  {t('newProject.mode.new')}
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-bold">
                <div className="col-span-2">
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.goal.label')}</label>
                  <textarea
                    value={idea}
                    onChange={(e) => setIdea(e.target.value)}
                    placeholder={startMode === 'existing_project' ? t('newProject.goal.placeholderAdopt') : t('newProject.goal.placeholderNew')}
                    className="w-full h-24 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-4 focus:outline-none focus:border-accent font-mono leading-relaxed transition-colors resize-none"
                  />
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.projectName.label')}</label>
                  <input
                    type="text"
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    placeholder={t('newProject.projectName.placeholder')}
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.projectKey.label')}</label>
                  <input
                    type="text"
                    value={startProjectKey}
                    onChange={(e) => setStartProjectKey(e.target.value)}
                    placeholder={t('newProject.projectKey.placeholder')}
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent font-mono uppercase transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.modelProfile.label')}</label>
                  <select
                    value={modelProfile}
                    onChange={(e) => setModelProfile(e.target.value)}
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent transition-colors"
                  >
                    <option value="balanced">{t('newProject.modelProfiles.balanced')}</option>
                    <option value="cheap">{t('newProject.modelProfiles.cheap')}</option>
                    <option value="max_quality">{t('newProject.modelProfiles.max_quality')}</option>
                  </select>
                </div>

                <div>
                  <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.deliveryMode.label')}</label>
                  <select
                    value={deliveryMode}
                    onChange={(e) => setDeliveryMode(e.target.value)}
                    className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 focus:outline-none focus:border-accent transition-colors"
                  >
                    {DELIVERY_MODE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{deliveryModeLabel(option)}</option>
                    ))}
                  </select>
                </div>

                {deliveryMode === 'custom' && (
                  <div className="col-span-2">
                    <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.deliveryStandards.label')}</label>
                    <textarea
                      value={deliveryStandards}
                      onChange={(e) => setDeliveryStandards(e.target.value)}
                      placeholder={t('newProject.deliveryStandards.placeholder')}
                      className="w-full h-24 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-4 focus:outline-none focus:border-accent font-mono leading-relaxed transition-colors resize-none"
                    />
                  </div>
                )}

                <div className="col-span-2 grid grid-cols-1 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                  <div className="font-bold text-2xs text-text-muted uppercase tracking-wider">{t('newProject.projectLocation.title')}</div>
                  <div>
                    <label className="block text-2xs text-text-secondary mb-1 uppercase">{t('newProject.projectRoot.label')}</label>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={projectRoot}
                        onChange={(e) => setProjectRoot(e.target.value)}
                        placeholder={t('newProject.projectRoot.placeholder')}
                        className="flex-1 min-w-0 bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs font-mono focus:outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() => openDirectoryPicker(projectRoot)}
                        className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-primary font-bold px-2.5 py-1.5 rounded-lg text-xs transition-all duo-button-physics cursor-pointer"
                      >
                        {t('newProject.projectRoot.browse')}
                      </button>
                    </div>
                  </div>
                </div>

                <div className="col-span-2 grid grid-cols-1 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-bold text-2xs text-text-muted uppercase tracking-wider">Project context</div>
                      <p className="mt-1 text-2xs text-text-secondary leading-relaxed">
                        Declare files, directories, or URLs before GoodTeam builds its cached project understanding.
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={addContextSource}
                      className="bg-surface-raised hover:bg-surface-overlay border-2 border-border-subtle shadow-button text-text-primary p-2 rounded-lg transition-all duo-button-physics cursor-pointer shrink-0"
                      aria-label="Add context source"
                    >
                      <Plus className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="space-y-2">
                    {contextSources.map((source, index) => (
                      <div key={`context-source-${index}`} className="grid grid-cols-[96px_minmax(0,1fr)_36px] gap-2 items-center">
                        <select
                          value={source.kind}
                          onChange={(e) => updateContextSource(index, { kind: e.target.value })}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-2 py-2 text-xs font-bold focus:outline-none focus:border-accent"
                          aria-label={`Context source ${index + 1} type`}
                        >
                          <option value="file">File</option>
                          <option value="dir">Dir</option>
                          <option value="url">URL</option>
                        </select>
                        <input
                          type="text"
                          value={source.value}
                          onChange={(e) => updateContextSource(index, { value: e.target.value })}
                          placeholder={source.kind === 'url' ? 'https://example.com/context.md' : '/absolute/path/or/project-relative'}
                          className="min-w-0 w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:border-accent"
                        />
                        <button
                          type="button"
                          onClick={() => removeContextSource(index)}
                          className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle hover:border-status-danger/30 text-text-secondary hover:text-status-danger p-2 rounded-lg transition-all duo-button-physics cursor-pointer"
                          aria-label={`Remove context source ${index + 1}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>

                  {contextEstimate && (
                    <div className="bg-surface-raised border-2 border-border-subtle rounded-lg px-3 py-2 text-xs flex flex-wrap items-center justify-between gap-2">
                      <span className="font-bold text-accent-text">
                        ~{contextTokenCount.toLocaleString()} tokens · recommend {contextModel}
                      </span>
                      <span className="text-2xs text-text-muted font-mono">
                        {contextEstimate.estimate.source_count} sources · {contextEstimate.estimate.bytes.toLocaleString()} bytes
                      </span>
                    </div>
                  )}

                  <div className="flex flex-col sm:flex-row gap-2">
                    <button
                      type="button"
                      onClick={handleContextEstimate}
                      disabled={previewingContext || generatingContext || !hasContextSources}
                      className="flex-1 bg-surface-raised hover:bg-accent-bg hover:text-accent-text border-2 border-border-subtle shadow-button text-text-secondary font-bold py-2 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                    >
                      {previewingContext ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Calculator className="h-3.5 w-3.5" />}
                      <span>Preview cost</span>
                    </button>
                    <button
                      type="button"
                      onClick={handleContextGenerate}
                      disabled={generatingContext || previewingContext || !contextEstimate}
                      className="flex-1 bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold py-2 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                    >
                      {generatingContext ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                      <span>Generate context</span>
                    </button>
                  </div>
                </div>

                {isNewProjectMode && (
                  <div className="col-span-2 grid grid-cols-1 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                    <div className="font-bold text-2xs text-text-muted uppercase tracking-wider">{t('newProject.git.title')}</div>
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
                          {t('newProject.git.init')}
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
                          {t('newProject.git.createRemote')}
                        </label>
                      </div>

                      {/* Revealed options when createRemote is checked */}
                      {createRemote && (
                        <div className="pl-6 space-y-4 border-l-2 border-accent/30 mt-2">
                          {/* Private / Public Toggle */}
                          <div className="flex items-center space-x-3">
                            <span className="text-xs font-bold text-text-secondary">{t('newProject.git.visibility')}</span>
                            <div className="flex bg-surface-raised p-1 rounded-lg border border-border-subtle select-none">
                              <button
                                type="button"
                                onClick={() => setRemotePrivate(true)}
                                className={`px-3 py-1.5 rounded-md text-2xs font-bold transition-all cursor-pointer ${remotePrivate ? 'bg-accent text-text-inverse shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
                              >
                                {t('newProject.git.private')}
                              </button>
                              <button
                                type="button"
                                onClick={() => setRemotePrivate(false)}
                                className={`px-3 py-1.5 rounded-md text-2xs font-bold transition-all cursor-pointer ${!remotePrivate ? 'bg-accent text-text-inverse shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
                              >
                                {t('newProject.git.public')}
                              </button>
                            </div>
                          </div>

                          {/* Helper / Callout Box */}
                          <div className="bg-surface-raised text-zinc-900 dark:text-zinc-100 p-4 rounded-xl border border-border-subtle text-2xs leading-relaxed space-y-1.5 shadow-sm">
                            <p className="font-extrabold flex items-center gap-1">
                              <Info className="h-3.5 w-3.5 text-accent shrink-0" />
                              {t('newProject.git.authTitle')}
                            </p>
                            <p>
                              {t('newProject.git.authToken')} <strong className="font-bold font-mono">GITHUB_TOKEN</strong> (or <strong className="font-bold font-mono">GH_TOKEN</strong>) {t('newProject.git.authBeforeLaunch')}
                            </p>
                            <p>
                              {t('newProject.git.authScopes')} <strong className="font-bold font-mono">repo</strong>. {t('newProject.git.authFineGrained')} <strong className="font-bold font-mono">Administration: Read & write</strong> + <strong className="font-bold font-mono">Contents: Read & write</strong> (on the target account/org).
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
                    <span>{showConfigDrawer ? t('newProject.advanced.hide') : t('newProject.advanced.show')}</span>
                    <ChevronDown className={`h-4 w-4 transition-transform ${showConfigDrawer ? 'rotate-180' : ''}`} />
                  </button>
                </div>

                {showConfigDrawer && (
                  <>
                    <div className="col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                      <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">{t('newProject.advanced.servicesBackend')}</div>
                      <div>
                        <label className="block text-2xs text-text-secondary mb-1 uppercase">{t('newProject.advanced.taskSync')}</label>
                        <select
                          value={taskSync}
                          onChange={(e) => setTaskSync(e.target.value)}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
                        >
                          <option value="">{t('newProject.advanced.taskSyncLocal')}</option>
                          <option value="plane">{t('newProject.advanced.taskSyncPlane')}</option>
                          <option value="linear">{t('newProject.advanced.taskSyncLinear')}</option>
                          <option value="github_issues">{t('newProject.advanced.taskSyncGithubIssues')}</option>
                        </select>
                      </div>

                      <div>
                        <label className="block text-2xs text-text-secondary mb-1 uppercase">{t('newProject.advanced.workerExecution')}</label>
                        <select
                          value={workerBackendMode}
                          onChange={(e) => setWorkerBackendMode(e.target.value)}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
                        >
                          <option value="codex">{t('newProject.advanced.workerExecutionCodex')}</option>
                          <option value="manual">{t('newProject.advanced.workerExecutionManual')}</option>
                          <option value="noop">{t('newProject.advanced.workerExecutionNoop')}</option>
                        </select>
                      </div>

                      {taskSync === 'plane' && (
                        <div className="col-span-2 grid grid-cols-2 gap-3 border-2 border-border-subtle p-4 rounded-xl bg-surface-raised mt-2">
                          <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">{t('newProject.advanced.planeCredentials')}</div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.planeApiHost')}</label>
                            <input type="text" value={planeApiHost} onChange={(e) => setPlaneApiHost(e.target.value)} className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.planeWorkspaceSlug')}</label>
                            <input type="text" value={planeWorkspaceSlug} onChange={(e) => setPlaneWorkspaceSlug(e.target.value)} placeholder="e.g. leader_kanban" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.planeProjectId')}</label>
                            <input type="text" value={planeProjectId} onChange={(e) => setPlaneProjectId(e.target.value)} placeholder="uuid" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.planeApiKeyEnv')}</label>
                            <input type="text" value={planeApiKeyEnv} onChange={(e) => setPlaneApiKeyEnv(e.target.value)} className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.planeMcpServer')}</label>
                            <input type="text" value={planeMcpServer} onChange={(e) => setPlaneMcpServer(e.target.value)} className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent" />
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 border-2 border-border-subtle p-5 rounded-xl bg-surface-sunken">
                      <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">{t('newProject.advanced.deploymentContract')}</div>
                      <div className="col-span-2">
                        <label className="block text-2xs text-text-secondary mb-1.5 uppercase">{t('newProject.advanced.deploymentBackend')}</label>
                        <select
                          value={deploymentBackendMode}
                          onChange={(e) => setDeploymentBackendMode(e.target.value)}
                          className="w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-lg px-3 py-1.5 text-xs font-semibold focus:outline-none focus:border-accent"
                        >
                          <option value="">{t('newProject.advanced.deploymentNone')}</option>
                          <option value="ssh_compose">{t('newProject.advanced.deploymentSshCompose')}</option>
                        </select>
                      </div>

                      {deploymentBackendMode === 'ssh_compose' && (
                        <div className="col-span-2 grid grid-cols-2 gap-3 border-2 border-border-subtle p-4 rounded-xl bg-surface-raised mt-2 text-xs">
                          <div className="col-span-2 font-bold text-2xs text-text-muted uppercase tracking-wider">{t('newProject.advanced.sshDeployConfig')}</div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.stagingSshTarget')}</label>
                            <input type="text" value={stagingSshTarget} onChange={(e) => setStagingSshTarget(e.target.value)} placeholder="user@host" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.stagingRemoteDir')}</label>
                            <input type="text" value={stagingRemoteDir} onChange={(e) => setStagingRemoteDir(e.target.value)} placeholder="/srv/app" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.stagingGitRef')}</label>
                            <input type="text" value={stagingGitRef} onChange={(e) => setStagingGitRef(e.target.value)} placeholder="main" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.stagingPublicUrl')}</label>
                            <input type="text" value={stagingPublicUrl} onChange={(e) => setStagingPublicUrl(e.target.value)} placeholder="http://url:port" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.deployCommand')}</label>
                            <input type="text" value={stagingDeployCommand} onChange={(e) => setStagingDeployCommand(e.target.value)} placeholder="docker compose up -d" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.healthcheckCommand')}</label>
                            <input type="text" value={stagingHealthcheckCommand} onChange={(e) => setStagingHealthcheckCommand(e.target.value)} placeholder="curl -f /health" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>
                          <div>
                            <label className="block text-2xs text-text-secondary mb-1">{t('newProject.advanced.smokeCommand')}</label>
                            <input type="text" value={stagingSmokeCommand} onChange={(e) => setStagingSmokeCommand(e.target.value)} placeholder="curl -f /" className="w-full bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 font-mono text-2xs focus:outline-none focus:border-accent" />
                          </div>

                          <div className="col-span-2 border-t-2 border-border-subtle my-1" />

                          <div className="col-span-2 flex items-center space-x-2">
                            <input type="checkbox" id="contract-req" checked={deliveryContractRequired} onChange={(e) => setDeliveryContractRequired(e.target.checked)} className="rounded text-accent focus:ring-accent h-4 w-4 cursor-pointer" />
                            <label htmlFor="contract-req" className="font-bold text-2xs text-text-muted uppercase tracking-wider cursor-pointer">{t('newProject.advanced.requireFunctionalEvidence')}</label>
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.minimumEvidenceGate')}</label>
                            <select value={deliveryContractMinEvidence} onChange={(e) => setDeliveryContractMinEvidence(e.target.value)} className="bg-surface-sunken text-text-primary border border-border-subtle rounded px-2 py-1 text-xs focus:outline-none focus:border-accent">
                              <option value="infrastructure">{t('newProject.advanced.evidenceInfrastructure')}</option>
                              <option value="functional">{t('newProject.advanced.evidenceFunctional')}</option>
                            </select>
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.fatalPatterns')}</label>
                            <textarea value={deliveryContractFatalPatterns} onChange={(e) => setDeliveryContractFatalPatterns(e.target.value)} placeholder="e.g. migrate: executable file not found" className="w-full h-16 bg-surface-sunken text-text-primary border border-border-subtle rounded p-2 font-mono text-xs resize-none focus:outline-none focus:border-accent" />
                          </div>
                          <div className="col-span-2">
                            <label className="block text-2xs text-text-muted mb-1">{t('newProject.advanced.acceptanceTests')}</label>
                            <textarea value={deliveryContractAcceptance} onChange={(e) => setDeliveryContractAcceptance(e.target.value)} placeholder="critical-path|ssh_command|docker compose exec -T backend ./migrate status" className="w-full h-16 bg-surface-sunken text-text-primary border border-border-subtle rounded p-2 font-mono text-xs resize-none focus:outline-none focus:border-accent" />
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="col-span-2">
                      <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.advanced.roleModelOverrides')}</label>
                      <textarea
                        value={roleModelOverrides}
                        onChange={(e) => setRoleModelOverrides(e.target.value)}
                        placeholder="architect=codex:gpt-5.5&#10;implementation=codex:gpt-5.3-codex"
                        className="w-full h-20 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-3 font-mono leading-relaxed transition-colors resize-none focus:outline-none focus:border-accent"
                      />
                    </div>

                    <div className="col-span-2">
                      <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">{t('newProject.advanced.projectConfigPath')}</label>
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
                  <h4 className="font-bold text-2xs uppercase tracking-wider text-text-muted mb-4">{t('newProject.launchCheck.title')}</h4>
                  <div className="space-y-3.5 text-xs font-semibold">
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">{t('newProject.launchCheck.projectTarget')}</span>
                      <span className="font-mono text-text-primary truncate max-w-[150px] font-bold">{startProjectKey || 'GTO'}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">{t('newProject.launchCheck.sourceMode')}</span>
                      <span className="font-bold text-accent-text">{startMode === 'existing_project' ? t('newProject.sourceMode.adoption') : t('newProject.sourceMode.newProject')}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">{t('newProject.launchCheck.modelProfile')}</span>
                      <span className="font-bold capitalize">{modelProfile}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                      <span className="text-text-secondary">{t('newProject.launchCheck.deliveryMode')}</span>
                      <span className="font-mono font-bold text-accent">{deliveryMode}</span>
                    </div>
                    <div className="flex justify-between items-center pb-1">
                      <span className="text-text-secondary">{t('newProject.launchCheck.workerBackend')}</span>
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
                      {t('newProject.dryRun')}
                    </label>
                  </div>

                  <div className="bg-surface-sunken text-2xs text-text-secondary font-mono p-3 rounded-lg border-2 border-border-strong leading-relaxed select-all">
                    {startMode === 'existing_project'
                      ? t('newProject.modeHint.adopt')
                      : t('newProject.modeHint.new')}
                  </div>

                  <div className="flex flex-col space-y-2">
                    <button
                      type="button"
                      onClick={handleCreateConfig}
                      disabled={actionLoading}
                      className="w-full bg-surface-raised hover:bg-accent-bg hover:text-accent-text border-2 border-border-subtle shadow-button text-text-secondary font-bold py-2 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics shrink-0 cursor-pointer"
                    >
                      {t('newProject.actions.createConfig')}
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
                      <span>{startingWorkflow ? t('newProject.actions.starting') : (dryRun ? t('newProject.actions.preview') : (startMode === 'existing_project' ? t('newProject.actions.startAdoption') : t('newProject.actions.startNew')))}</span>
                    </button>
                    {startingWorkflow && (
                      <p className="text-2xs text-zinc-500 text-center mt-2 animate-pulse">
                        {t('newProject.startingHint')}
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
