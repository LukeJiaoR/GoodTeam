import ProjectContentShell from './ProjectContentShell';
import ProjectHeader from './ProjectHeader';
import ProjectSidebar from './ProjectSidebar';
import ProjectTabRouter from './ProjectTabRouter';
import { DEFAULT_BACKEND_OPTIONS } from '../constants/workers';
import { buildApprovalMetrics } from '../utils/approvalMetrics';
import { buildProjectNavGroups } from '../utils/navigation';
import { getBadgeStyle, getStatusLabel } from '../utils/status';

export default function ProjectDashboard({
  activeTab,
  actionLoading,
  agentsMemorySubTab,
  answeringOpenQuestions,
  approvingAction,
  backendsError,
  backendsSuccess,
  changesRequestedNote,
  configuringRoles,
  copiedText,
  copyCommand,
  copyFailedText,
  crossError,
  crossLimit,
  crossLoading,
  crossQuery,
  crossResults,
  crossRole,
  crossScope,
  customModelSelected,
  darkMode,
  data,
  debateRun,
  deliveryMode,
  deliveryStandards,
  devLoopData,
  devLoopError,
  directoryPickerModal,
  discoveredAgentByBackend,
  discoveredAgents,
  dispatchAdapter,
  dispatchConstraints,
  dispatchCustomAdapter,
  dispatchCustomRole,
  dispatchError,
  dispatchLoading,
  dispatchName,
  dispatchRole,
  dispatchSuccess,
  dispatchTask,
  dispatchVerify,
  dryRun,
  editingArtifact,
  editorContent,
  error,
  fetchingOpenQuestions,
  gateDebateProjectKey,
  gateDebateRunId,
  generatingPrd,
  getBackendOptionLabel,
  getMissionSummary,
  getResolvingDebateRef,
  gitRemoteCreated,
  gitWarnings,
  graphData,
  graphError,
  handleAcceptRefinement,
  handleAgentChange,
  handleApplyAnswers,
  handleApplySimpleRouting,
  handleApproveDecision,
  handleCrossSearch,
  handleCustomModelChange,
  handleDiscardRefinement,
  handleDispatchTrack,
  handleEditArtifact,
  handleGeneratePrd,
  handleMemoryEventSearchChange,
  handleModelDropdownChange,
  handleOpenMemoryEvent,
  handleOpenQuestionsFromApproval,
  handleRefineArtifact,
  handleRetryBlocked,
  handleRunDebate,
  handleSaveArtifact,
  handleSaveWorkerBackends,
  handleSmokeRun,
  handleStartWorkflow,
  handleToggleMemoryEvents,
  idea,
  isNewProjectMode,
  languageOptions,
  languagePreference,
  loading,
  loadingDevLoop,
  loadingDiscoveredAgents,
  loadingGraph,
  loadingMemory,
  memoryData,
  memoryError,
  memoryEventBrowsers,
  memoryEventDetail,
  message,
  messageFeed,
  messageFeedProjectKey,
  messageFeedRunId,
  missionExpanded,
  mobileMenuOpen,
  modelProfile,
  newProjectModal,
  openQuestions,
  openQuestionsAnswers,
  panelGlobalMode,
  panelRoleModels,
  panelRoleOverrides,
  projectKey,
  proposedRevision,
  refineLoading,
  roleBackendOptions,
  safeDiscoveredAgents,
  savingBackends,
  setActiveTab,
  setAgentsMemorySubTab,
  setChangesRequestedNote,
  setConfiguringRoles,
  setCrossLimit,
  setCrossQuery,
  setCrossRole,
  setCrossScope,
  setDarkMode,
  setDeliveryMode,
  setDeliveryStandards,
  setDispatchAdapter,
  setDispatchConstraints,
  setDispatchCustomAdapter,
  setDispatchCustomRole,
  setDispatchName,
  setDispatchRole,
  setDispatchTask,
  setDispatchVerify,
  setDryRun,
  setEditingArtifact,
  setEditorContent,
  setIdea,
  setLanguagePreference,
  setMemoryEventDetail,
  setMissionExpanded,
  setMobileMenuOpen,
  setModelProfile,
  setOpenQuestionsAnswers,
  setPanelGlobalMode,
  setShowChangesRequestedNoteKey,
  setShowGitNotice,
  setSimpleRoutingBackends,
  setStartMode,
  setTheme,
  setWorkerRoutingMode,
  showChangesRequestedNoteKey,
  showGitNotice,
  simpleRoutingBackends,
  smokeRunLoading,
  startMode,
  startingWorkflow,
  t,
  targetMessageFeedKey,
  theme,
  updateUrl,
  usedBackends,
  visibleGateDebate,
  workerRoles,
  workerRoutingMode,
}) {
  const state = data?.state;
  const startContext = data?.start_context || {};
  const backendOptions = startContext.worker_backend_options || DEFAULT_BACKEND_OPTIONS;

  const activeRun = data?.active_run || false;
  const roleObservations = data?.role_observations || [];
  const approvalReviews = data?.approval_reviews || [];
  const actionItems = data?.action_items || [];
  const smokeRunItems = data?.smoke_run_items || [];
  const agileBoard = data?.agile_board || { columns: {}, counts: {} };
  const workerJobs = data?.worker_jobs || [];

  const headerStatus = state?.next_node || state?.current_node || 'no_active_run';
  const headBadge = getBadgeStyle(headerStatus);

  const {
    actionableCount,
    approvalBannerQuestions,
    approvalProgressPercent,
    approvedCount,
    totalApprovalsCount,
  } = buildApprovalMetrics({ approvalReviews, openQuestions, t });
  const navGroups = buildProjectNavGroups(t);
  const currentMemoryEvent = memoryEventDetail?.data || {};
  const memorySupersedes = currentMemoryEvent.supersedes || '';
  const memorySupersededBy = Array.isArray(currentMemoryEvent.superseded_by)
    ? currentMemoryEvent.superseded_by.filter(Boolean)
    : [];
  const hasMemorySupersedeInfo = Boolean(
    currentMemoryEvent.is_superseded || memorySupersedes || memorySupersededBy.length
  );
  const renderMemoryEventLink = (eventId) => (
    <button
      type="button"
      onClick={() => handleOpenMemoryEvent(eventId)}
      className="bg-accent-bg hover:opacity-90 text-accent-text px-2 py-0.5 rounded font-mono border border-accent/30 transition-all duo-button-physics focus:outline-none"
      aria-label={`Open full memory event ${eventId}`}
    >
      {eventId}
    </button>
  );

  return (
    <div className="min-h-screen bg-surface-base text-text-primary transition-colors flex flex-col lg:flex-row font-sans antialiased">

      <ProjectSidebar
        activeTab={activeTab}
        actionableCount={actionableCount}
        darkMode={darkMode}
        languageOptions={languageOptions}
        languagePreference={languagePreference}
        mobileMenuOpen={mobileMenuOpen}
        navGroups={navGroups}
        projectKey={projectKey}
        setActiveTab={setActiveTab}
        setAgentsMemorySubTab={setAgentsMemorySubTab}
        setDarkMode={setDarkMode}
        setLanguagePreference={setLanguagePreference}
        setMobileMenuOpen={setMobileMenuOpen}
        setTheme={setTheme}
        startContext={startContext}
        state={state}
        t={t}
        theme={theme}
        updateUrl={updateUrl}
      />

      <ProjectHeader
        activeRun={activeRun}
        getStatusLabel={getStatusLabel}
        headBadge={headBadge}
        headerStatus={headerStatus}
        mobileMenuOpen={mobileMenuOpen}
        mobileStatusClass={`${headBadge.bg} ${headBadge.text} ${headBadge.border}`}
        setMobileMenuOpen={setMobileMenuOpen}
        startContext={startContext}
        state={state}
        t={t}
        updateUrl={updateUrl}
      />

      <ProjectContentShell
        actionableCount={actionableCount}
        approvalBannerQuestions={approvalBannerQuestions}
        directoryPickerModal={directoryPickerModal}
        error={error}
        gitRemoteCreated={gitRemoteCreated}
        gitWarnings={gitWarnings}
        hasMemorySupersedeInfo={hasMemorySupersedeInfo}
        loading={loading}
        loadingLabel={t('chrome.loading')}
        mainPanelChildren={
          <ProjectTabRouter
            activeRun={activeRun}
            activeTab={activeTab}
            actionItems={actionItems}
            actionLoading={actionLoading}
            agentsMemorySubTab={agentsMemorySubTab}
            agileBoard={agileBoard}
            answeringOpenQuestions={answeringOpenQuestions}
            approvalProgressPercent={approvalProgressPercent}
            approvalReviews={approvalReviews}
            approvedCount={approvedCount}
            approvingAction={approvingAction}
            backendOptions={backendOptions}
            backendsError={backendsError}
            backendsSuccess={backendsSuccess}
            changesRequestedNote={changesRequestedNote}
            configuringRoles={configuringRoles}
            copiedText={copiedText}
            copyCommand={copyCommand}
            copyFailedText={copyFailedText}
            crossError={crossError}
            crossLimit={crossLimit}
            crossLoading={crossLoading}
            crossQuery={crossQuery}
            crossResults={crossResults}
            crossRole={crossRole}
            crossScope={crossScope}
            customModelSelected={customModelSelected}
            debateRun={debateRun}
            deliveryMode={deliveryMode}
            deliveryStandards={deliveryStandards}
            devLoopData={devLoopData}
            devLoopError={devLoopError}
            discoveredAgentByBackend={discoveredAgentByBackend}
            discoveredAgents={discoveredAgents}
            dispatchAdapter={dispatchAdapter}
            dispatchConstraints={dispatchConstraints}
            dispatchCustomAdapter={dispatchCustomAdapter}
            dispatchCustomRole={dispatchCustomRole}
            dispatchError={dispatchError}
            dispatchLoading={dispatchLoading}
            dispatchName={dispatchName}
            dispatchRole={dispatchRole}
            dispatchSuccess={dispatchSuccess}
            dispatchTask={dispatchTask}
            dispatchVerify={dispatchVerify}
            dryRun={dryRun}
            editingArtifact={editingArtifact}
            editorContent={editorContent}
            fetchingOpenQuestions={fetchingOpenQuestions}
            gateDebateProjectKey={gateDebateProjectKey}
            gateDebateRunId={gateDebateRunId}
            generatingPrd={generatingPrd}
            getBackendOptionLabel={getBackendOptionLabel}
            getMissionSummary={getMissionSummary}
            getResolvingDebateRef={getResolvingDebateRef}
            graphData={graphData}
            graphError={graphError}
            handleAcceptRefinement={handleAcceptRefinement}
            handleAgentChange={handleAgentChange}
            handleApplyAnswers={handleApplyAnswers}
            handleApplySimpleRouting={handleApplySimpleRouting}
            handleApproveDecision={handleApproveDecision}
            handleCrossSearch={handleCrossSearch}
            handleCustomModelChange={handleCustomModelChange}
            handleDiscardRefinement={handleDiscardRefinement}
            handleDispatchTrack={handleDispatchTrack}
            handleEditArtifact={handleEditArtifact}
            handleGeneratePrd={handleGeneratePrd}
            handleMemoryEventSearchChange={handleMemoryEventSearchChange}
            handleModelDropdownChange={handleModelDropdownChange}
            handleOpenMemoryEvent={handleOpenMemoryEvent}
            handleOpenQuestionsFromApproval={handleOpenQuestionsFromApproval}
            handleRefineArtifact={handleRefineArtifact}
            handleRetryBlocked={handleRetryBlocked}
            handleRunDebate={handleRunDebate}
            handleSaveArtifact={handleSaveArtifact}
            handleSaveWorkerBackends={handleSaveWorkerBackends}
            handleSmokeRun={handleSmokeRun}
            handleStartWorkflow={handleStartWorkflow}
            handleToggleMemoryEvents={handleToggleMemoryEvents}
            headerStatus={headerStatus}
            idea={idea}
            isNewProjectMode={isNewProjectMode}
            loadingDevLoop={loadingDevLoop}
            loadingDiscoveredAgents={loadingDiscoveredAgents}
            loadingGraph={loadingGraph}
            loadingMemory={loadingMemory}
            memoryData={memoryData}
            memoryError={memoryError}
            memoryEventBrowsers={memoryEventBrowsers}
            messageFeed={messageFeed}
            messageFeedProjectKey={messageFeedProjectKey}
            messageFeedRunId={messageFeedRunId}
            missionExpanded={missionExpanded}
            modelProfile={modelProfile}
            openQuestions={openQuestions}
            openQuestionsAnswers={openQuestionsAnswers}
            panelGlobalMode={panelGlobalMode}
            panelRoleModels={panelRoleModels}
            panelRoleOverrides={panelRoleOverrides}
            proposedRevision={proposedRevision}
            refineLoading={refineLoading}
            roleBackendOptions={roleBackendOptions}
            roleObservations={roleObservations}
            safeDiscoveredAgents={safeDiscoveredAgents}
            savingBackends={savingBackends}
            setActiveTab={setActiveTab}
            setAgentsMemorySubTab={setAgentsMemorySubTab}
            setChangesRequestedNote={setChangesRequestedNote}
            setConfiguringRoles={setConfiguringRoles}
            setCrossLimit={setCrossLimit}
            setCrossQuery={setCrossQuery}
            setCrossRole={setCrossRole}
            setCrossScope={setCrossScope}
            setDeliveryMode={setDeliveryMode}
            setDeliveryStandards={setDeliveryStandards}
            setDispatchAdapter={setDispatchAdapter}
            setDispatchConstraints={setDispatchConstraints}
            setDispatchCustomAdapter={setDispatchCustomAdapter}
            setDispatchCustomRole={setDispatchCustomRole}
            setDispatchName={setDispatchName}
            setDispatchRole={setDispatchRole}
            setDispatchTask={setDispatchTask}
            setDispatchVerify={setDispatchVerify}
            setDryRun={setDryRun}
            setEditingArtifact={setEditingArtifact}
            setEditorContent={setEditorContent}
            setIdea={setIdea}
            setMissionExpanded={setMissionExpanded}
            setModelProfile={setModelProfile}
            setOpenQuestionsAnswers={setOpenQuestionsAnswers}
            setPanelGlobalMode={setPanelGlobalMode}
            setShowChangesRequestedNoteKey={setShowChangesRequestedNoteKey}
            setSimpleRoutingBackends={setSimpleRoutingBackends}
            setStartMode={setStartMode}
            setWorkerRoutingMode={setWorkerRoutingMode}
            showChangesRequestedNoteKey={showChangesRequestedNoteKey}
            simpleRoutingBackends={simpleRoutingBackends}
            smokeRunItems={smokeRunItems}
            smokeRunLoading={smokeRunLoading}
            startContext={startContext}
            startingWorkflow={startingWorkflow}
            startMode={startMode}
            state={state}
            targetMessageFeedKey={targetMessageFeedKey}
            totalApprovalsCount={totalApprovalsCount}
            updateUrl={updateUrl}
            usedBackends={usedBackends}
            visibleGateDebate={visibleGateDebate}
            workerJobs={workerJobs}
            workerRoles={workerRoles}
            workerRoutingMode={workerRoutingMode}
          />
        }
        memoryEventDetail={memoryEventDetail}
        memorySupersededBy={memorySupersededBy}
        memorySupersedes={memorySupersedes}
        message={message}
        newProjectModal={newProjectModal}
        renderMemoryEventLink={renderMemoryEventLink}
        setActiveTab={setActiveTab}
        setMemoryEventDetail={setMemoryEventDetail}
        setShowGitNotice={setShowGitNotice}
        showGitNotice={showGitNotice}
        t={t}
      />
    </div>
  );
}
