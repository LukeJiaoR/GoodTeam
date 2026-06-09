import FilePath from './FilePath';
import Spinner from './Spinner';
import WorkflowGraphTab from './tabs/WorkflowGraphTab';
import RolesTab from './tabs/RolesTab';
import ArtifactsTab from './tabs/ArtifactsTab';
import ActionsTab from './tabs/ActionsTab';
import AgileBoardTab from './tabs/AgileBoardTab';
import SmokeRunsTab from './tabs/SmokeRunsTab';
import WorkersTab from './tabs/WorkersTab';
import ApprovalsTab from './tabs/ApprovalsTab';
import DevLoopTab from './tabs/DevLoopTab';
import SystemTab from './tabs/SystemTab';
import MemoryTab from './tabs/MemoryTab';
import OverviewTab from './tabs/OverviewTab';
import AgentsMemoryTab from './tabs/AgentsMemoryTab';
import MessagesTab from './tabs/MessagesTab';
import { FALLBACK_AGENT_MODES, WORKER_ROUTING_BUCKETS } from '../constants/workers';
import { computeLineDiff, suggestedAnswersForQuestion } from '../utils/approvals';
import { getBadgeStyle, getStatusLabel } from '../utils/status';
import { formatHumanTime } from '../utils/time';

export default function ProjectTabRouter({
  activeRun,
  activeTab,
  actionItems,
  actionLoading,
  agentsMemorySubTab,
  agileBoard,
  answeringOpenQuestions,
  approvalProgressPercent,
  approvalReviews,
  approvedCount,
  approvingAction,
  backendOptions,
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
  debateRun,
  deliveryMode,
  deliveryStandards,
  devLoopData,
  devLoopError,
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
  devLoopCreateRemote,
  devLoopRemotePrivate,
  dryRun,
  editingArtifact,
  editorContent,
  fetchingOpenQuestions,
  gateDebateProjectKey,
  gateDebateRunId,
  generatingPrd,
  getBackendOptionLabel,
  getMissionSummary,
  getResolvingDebateRef,
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
  handleGitInit,
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
  headerStatus,
  idea,
  isNewProjectMode,
  gitInitLoading,
  gitInitWarnings,
  loadingDevLoop,
  loadingDiscoveredAgents,
  loadingGraph,
  loadingMemory,
  memoryData,
  memoryError,
  memoryEventBrowsers,
  messageFeed,
  messageFeedProjectKey,
  messageFeedRunId,
  missionExpanded,
  modelProfile,
  openQuestions,
  openQuestionsAnswers,
  panelGlobalMode,
  panelRoleModels,
  panelRoleOverrides,
  proposedRevision,
  refineLoading,
  roleBackendOptions,
  roleObservations,
  safeDiscoveredAgents,
  savingBackends,
  setActiveTab,
  setAgentsMemorySubTab,
  setConfiguringRoles,
  setCrossLimit,
  setCrossQuery,
  setCrossRole,
  setCrossScope,
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
  setDevLoopCreateRemote,
  setDevLoopRemotePrivate,
  setDryRun,
  setEditingArtifact,
  setEditorContent,
  setIdea,
  setMissionExpanded,
  setModelProfile,
  setOpenQuestionsAnswers,
  setPanelGlobalMode,
  setShowChangesRequestedNoteKey,
  setSimpleRoutingBackends,
  setStartMode,
  setWorkerRoutingMode,
  setChangesRequestedNote,
  showChangesRequestedNoteKey,
  simpleRoutingBackends,
  smokeRunItems,
  smokeRunLoading,
  startContext,
  startingWorkflow,
  startMode,
  state,
  targetMessageFeedKey,
  totalApprovalsCount,
  updateUrl,
  usedBackends,
  visibleGateDebate,
  workerJobs,
  workerRoles,
  workerRoutingMode,
}) {
  return (
    <>
      {/* Tab: Workflow Graph */}
      {activeTab === 'workflow' && (
        <WorkflowGraphTab
          loadingGraph={loadingGraph}
          graphError={graphError}
          graphData={graphData}
          Spinner={Spinner}
        />
      )}

      {/* Tab: Overview */}
      {activeTab === 'overview' && (
        <OverviewTab
          state={state}
          headerStatus={headerStatus}
          roleObservations={roleObservations}
          approvedCount={approvedCount}
          totalApprovalsCount={totalApprovalsCount}
          missionExpanded={missionExpanded}
          setMissionExpanded={setMissionExpanded}
          getMissionSummary={getMissionSummary}
          handleRetryBlocked={handleRetryBlocked}
          actionLoading={actionLoading}
          updateUrl={updateUrl}
          setActiveTab={setActiveTab}
          startContext={startContext}
          FilePath={FilePath}
          activeRun={activeRun}
          startMode={startMode}
          setStartMode={setStartMode}
          isNewProjectMode={isNewProjectMode}
          idea={idea}
          setIdea={setIdea}
          modelProfile={modelProfile}
          setModelProfile={setModelProfile}
          deliveryMode={deliveryMode}
          setDeliveryMode={setDeliveryMode}
          deliveryStandards={deliveryStandards}
          setDeliveryStandards={setDeliveryStandards}
          dryRun={dryRun}
          setDryRun={setDryRun}
          handleStartWorkflow={handleStartWorkflow}
          startingWorkflow={startingWorkflow}
          getBadgeStyle={getBadgeStyle}
          getStatusLabel={getStatusLabel}
        />
      )}

      {activeTab === 'agents_memory' && (
        <AgentsMemoryTab
          agentsMemorySubTab={agentsMemorySubTab}
          setAgentsMemorySubTab={setAgentsMemorySubTab}
          roleObservations={roleObservations}
          getBadgeStyle={getBadgeStyle}
          getStatusLabel={getStatusLabel}
          FilePath={FilePath}
          state={state}
          loadingMemory={loadingMemory}
          memoryError={memoryError}
          memoryData={memoryData}
          memoryEventBrowsers={memoryEventBrowsers}
          crossRole={crossRole}
          setCrossRole={setCrossRole}
          crossQuery={crossQuery}
          setCrossQuery={setCrossQuery}
          crossScope={crossScope}
          setCrossScope={setCrossScope}
          crossLimit={crossLimit}
          setCrossLimit={setCrossLimit}
          crossLoading={crossLoading}
          crossError={crossError}
          crossResults={crossResults}
          handleCrossSearch={handleCrossSearch}
          handleToggleMemoryEvents={handleToggleMemoryEvents}
          handleMemoryEventSearchChange={handleMemoryEventSearchChange}
          handleOpenMemoryEvent={handleOpenMemoryEvent}
          panelGlobalMode={panelGlobalMode}
          setPanelGlobalMode={setPanelGlobalMode}
          panelRoleOverrides={panelRoleOverrides}
          panelRoleModels={panelRoleModels}
          simpleRoutingBackends={simpleRoutingBackends}
          setSimpleRoutingBackends={setSimpleRoutingBackends}
          configuringRoles={configuringRoles}
          setConfiguringRoles={setConfiguringRoles}
          savingBackends={savingBackends}
          actionLoading={actionLoading}
          backendOptions={backendOptions}
          roleBackendOptions={roleBackendOptions}
          workerRoutingMode={workerRoutingMode}
          setWorkerRoutingMode={setWorkerRoutingMode}
          workerRoutingBuckets={WORKER_ROUTING_BUCKETS}
          workerRoles={workerRoles}
          loadingDiscoveredAgents={loadingDiscoveredAgents}
          discoveredAgents={discoveredAgents}
          safeDiscoveredAgents={safeDiscoveredAgents}
          discoveredAgentByBackend={discoveredAgentByBackend}
          customModelSelected={customModelSelected}
          fallbackAgentModes={FALLBACK_AGENT_MODES}
          usedBackends={usedBackends}
          startContext={startContext}
          workerJobs={workerJobs}
          backendsError={backendsError}
          backendsSuccess={backendsSuccess}
          getBackendOptionLabel={getBackendOptionLabel}
          handleAgentChange={handleAgentChange}
          handleApplySimpleRouting={handleApplySimpleRouting}
          handleModelDropdownChange={handleModelDropdownChange}
          handleCustomModelChange={handleCustomModelChange}
          handleSaveWorkerBackends={handleSaveWorkerBackends}
        />
      )}

      {/* Tab: Roles */}
      {activeTab === 'roles' && (
        <RolesTab
          roleObservations={roleObservations}
          getBadgeStyle={getBadgeStyle}
          getStatusLabel={getStatusLabel}
          FilePath={FilePath}
        />
      )}

      {/* Tab: Artifacts */}
      {activeTab === 'artifacts' && (
        <ArtifactsTab
          artifacts={state?.artifacts}
          handleEditArtifact={handleEditArtifact}
          FilePath={FilePath}
        />
      )}

      {/* Tab: Actions */}
      {activeTab === 'actions' && (
        <ActionsTab
          actionItems={actionItems}
          formatHumanTime={formatHumanTime}
          FilePath={FilePath}
        />
      )}

      {/* Tab: Agile Board */}
      {activeTab === 'board' && (
        <AgileBoardTab agileBoard={agileBoard} />
      )}

      {/* Tab: Smoke Runs */}
      {activeTab === 'smoke' && (
        <SmokeRunsTab
          smokeRunItems={smokeRunItems}
          smokeRunLoading={smokeRunLoading}
          handleSmokeRun={handleSmokeRun}
          getBadgeStyle={getBadgeStyle}
          getStatusLabel={getStatusLabel}
          FilePath={FilePath}
        />
      )}

      {/* Tab: Workers */}
      {activeTab === 'workers' && (
        <WorkersTab
          panelGlobalMode={panelGlobalMode}
          setPanelGlobalMode={setPanelGlobalMode}
          panelRoleOverrides={panelRoleOverrides}
          panelRoleModels={panelRoleModels}
          simpleRoutingBackends={simpleRoutingBackends}
          setSimpleRoutingBackends={setSimpleRoutingBackends}
          configuringRoles={configuringRoles}
          setConfiguringRoles={setConfiguringRoles}
          savingBackends={savingBackends}
          actionLoading={actionLoading}
          backendOptions={backendOptions}
          roleBackendOptions={roleBackendOptions}
          workerRoutingMode={workerRoutingMode}
          setWorkerRoutingMode={setWorkerRoutingMode}
          workerRoutingBuckets={WORKER_ROUTING_BUCKETS}
          workerRoles={workerRoles}
          loadingDiscoveredAgents={loadingDiscoveredAgents}
          discoveredAgents={discoveredAgents}
          safeDiscoveredAgents={safeDiscoveredAgents}
          discoveredAgentByBackend={discoveredAgentByBackend}
          customModelSelected={customModelSelected}
          fallbackAgentModes={FALLBACK_AGENT_MODES}
          usedBackends={usedBackends}
          startContext={startContext}
          workerJobs={workerJobs}
          backendsError={backendsError}
          backendsSuccess={backendsSuccess}
          getBadgeStyle={getBadgeStyle}
          getBackendOptionLabel={getBackendOptionLabel}
          handleAgentChange={handleAgentChange}
          handleApplySimpleRouting={handleApplySimpleRouting}
          handleModelDropdownChange={handleModelDropdownChange}
          handleCustomModelChange={handleCustomModelChange}
          handleSaveWorkerBackends={handleSaveWorkerBackends}
        />
      )}

      {/* Tab: Approvals */}
      {activeTab === 'approvals' && (
        <ApprovalsTab
          approvedCount={approvedCount}
          totalApprovalsCount={totalApprovalsCount}
          approvalProgressPercent={approvalProgressPercent}
          approvalReviews={approvalReviews}
          approvingAction={approvingAction}
          actionLoading={actionLoading}
          generatingPrd={generatingPrd}
          editingArtifact={editingArtifact}
          proposedRevision={proposedRevision}
          editorContent={editorContent}
          setEditorContent={setEditorContent}
          openQuestions={openQuestions}
          fetchingOpenQuestions={fetchingOpenQuestions}
          openQuestionsAnswers={openQuestionsAnswers}
          setOpenQuestionsAnswers={setOpenQuestionsAnswers}
          answeringOpenQuestions={answeringOpenQuestions}
          visibleGateDebate={visibleGateDebate}
          gateDebateProjectKey={gateDebateProjectKey}
          gateDebateRunId={gateDebateRunId}
          debateRun={debateRun}
          refineLoading={refineLoading}
          selectedReviewNoteKey={showChangesRequestedNoteKey}
          changesRequestedNote={changesRequestedNote}
          setChangesRequestedNote={setChangesRequestedNote}
          setShowChangesRequestedNoteKey={setShowChangesRequestedNoteKey}
          FilePath={FilePath}
          getBadgeStyle={getBadgeStyle}
          getStatusLabel={getStatusLabel}
          handleEditArtifact={handleEditArtifact}
          handleGeneratePrd={handleGeneratePrd}
          handleOpenQuestionsFromApproval={handleOpenQuestionsFromApproval}
          handleApproveDecision={handleApproveDecision}
          getResolvingDebateRef={getResolvingDebateRef}
          computeLineDiff={computeLineDiff}
          handleDiscardRefinement={handleDiscardRefinement}
          handleAcceptRefinement={handleAcceptRefinement}
          setEditingArtifact={setEditingArtifact}
          handleRefineArtifact={handleRefineArtifact}
          handleSaveArtifact={handleSaveArtifact}
          suggestedAnswersForQuestion={suggestedAnswersForQuestion}
          handleApplyAnswers={handleApplyAnswers}
          handleRunDebate={handleRunDebate}
        />
      )}

      {/* Tab: Messages */}
      {activeTab === 'messages' && (
        <MessagesTab
          messageFeedProjectKey={messageFeedProjectKey}
          messageFeedRunId={messageFeedRunId}
          messageFeed={messageFeed}
          targetMessageFeedKey={targetMessageFeedKey}
        />
      )}

      {/* Tab: Memory */}
      {activeTab === 'memory' && (
        <MemoryTab
          state={state}
          loadingMemory={loadingMemory}
          memoryError={memoryError}
          memoryData={memoryData}
          memoryEventBrowsers={memoryEventBrowsers}
          crossRole={crossRole}
          setCrossRole={setCrossRole}
          crossQuery={crossQuery}
          setCrossQuery={setCrossQuery}
          crossScope={crossScope}
          setCrossScope={setCrossScope}
          crossLimit={crossLimit}
          setCrossLimit={setCrossLimit}
          crossLoading={crossLoading}
          crossError={crossError}
          crossResults={crossResults}
          handleCrossSearch={handleCrossSearch}
          handleToggleMemoryEvents={handleToggleMemoryEvents}
          handleMemoryEventSearchChange={handleMemoryEventSearchChange}
          handleOpenMemoryEvent={handleOpenMemoryEvent}
        />
      )}

      {/* Tab: Dev Loop */}
      {activeTab === 'devloop' && (
        <DevLoopTab
          devLoopData={devLoopData}
          loadingDevLoop={loadingDevLoop}
          devLoopError={devLoopError}
          formatHumanTime={formatHumanTime}
          Spinner={Spinner}
          getBadgeStyle={getBadgeStyle}
          copyLandCommand={copyCommand}
          copiedText={copiedText}
          copyFailedText={copyFailedText}
          FilePath={FilePath}
          dispatchSuccess={dispatchSuccess}
          dispatchError={dispatchError}
          gitInitLoading={gitInitLoading}
          gitInitWarnings={gitInitWarnings}
          handleGitInit={handleGitInit}
          devLoopCreateRemote={devLoopCreateRemote}
          setDevLoopCreateRemote={setDevLoopCreateRemote}
          devLoopRemotePrivate={devLoopRemotePrivate}
          setDevLoopRemotePrivate={setDevLoopRemotePrivate}
          handleDispatchTrack={handleDispatchTrack}
          dispatchName={dispatchName}
          setDispatchName={setDispatchName}
          dispatchRole={dispatchRole}
          setDispatchRole={setDispatchRole}
          dispatchCustomRole={dispatchCustomRole}
          setDispatchCustomRole={setDispatchCustomRole}
          dispatchAdapter={dispatchAdapter}
          setDispatchAdapter={setDispatchAdapter}
          dispatchCustomAdapter={dispatchCustomAdapter}
          setDispatchCustomAdapter={setDispatchCustomAdapter}
          dispatchTask={dispatchTask}
          setDispatchTask={setDispatchTask}
          dispatchConstraints={dispatchConstraints}
          setDispatchConstraints={setDispatchConstraints}
          dispatchVerify={dispatchVerify}
          setDispatchVerify={setDispatchVerify}
          dispatchLoading={dispatchLoading}
          workerRoles={workerRoles}
          roleBackendOptions={roleBackendOptions}
        />
      )}

      {/* Tab: System */}
      {activeTab === 'system' && (
        <SystemTab
          state={state}
          startContext={startContext}
          FilePath={FilePath}
        />
      )}
    </>
  );
}
