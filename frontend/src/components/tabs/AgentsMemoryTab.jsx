import RolesTab from './RolesTab';
import MemoryTab from './MemoryTab';
import WorkersTab from './WorkersTab';

const SUB_TABS = [
  { id: 'roles', label: 'Roles' },
  { id: 'memory', label: 'Memory' },
  { id: 'workers', label: 'Jobs' },
];

export default function AgentsMemoryTab({
  agentsMemorySubTab,
  setAgentsMemorySubTab,
  roleObservations,
  getBadgeStyle,
  getStatusLabel,
  FilePath,
  state,
  loadingMemory,
  memoryError,
  memoryData,
  memoryEventBrowsers,
  crossRole,
  setCrossRole,
  crossQuery,
  setCrossQuery,
  crossScope,
  setCrossScope,
  crossLimit,
  setCrossLimit,
  crossLoading,
  crossError,
  crossResults,
  handleCrossSearch,
  handleToggleMemoryEvents,
  handleMemoryEventSearchChange,
  handleOpenMemoryEvent,
  panelGlobalMode,
  setPanelGlobalMode,
  panelRoleOverrides,
  panelRoleModels,
  simpleRoutingBackends,
  setSimpleRoutingBackends,
  configuringRoles,
  setConfiguringRoles,
  savingBackends,
  actionLoading,
  backendOptions,
  roleBackendOptions,
  workerRoutingMode,
  setWorkerRoutingMode,
  workerRoutingBuckets,
  workerRoles,
  loadingDiscoveredAgents,
  discoveredAgents,
  safeDiscoveredAgents,
  discoveredAgentByBackend,
  customModelSelected,
  fallbackAgentModes,
  usedBackends,
  startContext,
  workerJobs,
  backendsError,
  backendsSuccess,
  getBackendOptionLabel,
  handleAgentChange,
  handleApplySimpleRouting,
  handleModelDropdownChange,
  handleCustomModelChange,
  handleSaveWorkerBackends,
}) {
  return (
    <div className="space-y-6">
      <div className="flex bg-surface-sunken border border-border-subtle p-1 rounded-xl w-fit space-x-1 shadow-card">
        {SUB_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setAgentsMemorySubTab(tab.id)}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
              agentsMemorySubTab === tab.id
                ? 'bg-surface-raised shadow-button text-accent-text'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {agentsMemorySubTab === 'roles' && (
        <RolesTab
          roleObservations={roleObservations}
          getBadgeStyle={getBadgeStyle}
          getStatusLabel={getStatusLabel}
          FilePath={FilePath}
        />
      )}

      {agentsMemorySubTab === 'memory' && (
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

      {agentsMemorySubTab === 'workers' && (
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
          workerRoutingBuckets={workerRoutingBuckets}
          workerRoles={workerRoles}
          loadingDiscoveredAgents={loadingDiscoveredAgents}
          discoveredAgents={discoveredAgents}
          safeDiscoveredAgents={safeDiscoveredAgents}
          discoveredAgentByBackend={discoveredAgentByBackend}
          customModelSelected={customModelSelected}
          fallbackAgentModes={fallbackAgentModes}
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
    </div>
  );
}
