import { useState, useEffect } from 'react';
import LanguageSwitcher from './components/LanguageSwitcher';
import NewProjectModalContainer from './components/NewProjectModalContainer';
import ProjectDashboard from './components/ProjectDashboard';
import ProjectsHomePage from './components/ProjectsHomePage';
import { useAppearancePreferences } from './hooks/useAppearancePreferences';
import { useClipboardFeedback } from './hooks/useClipboardFeedback';
import { useDirectoryPicker } from './hooks/useDirectoryPicker';
import { useDispatchTrackForm } from './hooks/useDispatchTrackForm';
import { useNewProjectForm } from './hooks/useNewProjectForm';
import { useProjectUrl } from './hooks/useProjectUrl';
import { useDevLoopData } from './hooks/useDevLoopData';
import { useMemoryData } from './hooks/useMemoryData';
import { useCrossProjectMemorySearch } from './hooks/useCrossProjectMemorySearch';
import { useApprovalEditor } from './hooks/useApprovalEditor';
import { useWorkerBackendSettings } from './hooks/useWorkerBackendSettings';
import { useMessageFeeds } from './hooks/useMessageFeeds';
import { useWorkflowGraphData } from './hooks/useWorkflowGraphData';
import { buildCustomDeliveryPayload } from './utils/deliveryPayload';
import { getMissionSummary } from './utils/missionSummary';
import { resolveProjectRootFromRepos } from './utils/projectRoot';
import { parseRoleModelOverrideLines, parseRoleModelOverridesMap } from './utils/roleModelOverrides';
import {
  LANGUAGE_PREFERENCE_AUTO,
} from './i18n';

export default function App() {
  const { projectKey, updateUrl } = useProjectUrl();
  const [activeTab, setActiveTab] = useState('overview');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const [agentsMemorySubTab, setAgentsMemorySubTab] = useState('roles');
  const [newProjectModalOpen, setNewProjectModalOpen] = useState(false);


  const appearance = useAppearancePreferences();
  const { darkMode, setDarkMode, theme, setTheme, languagePreference, setLanguagePreference, t } = appearance;
  const languageOptions = [
    { value: LANGUAGE_PREFERENCE_AUTO, label: t('language.auto'), ariaLabel: t('language.useAuto') },
    { value: 'en', label: t('language.english'), ariaLabel: t('language.useEnglish') },
    { value: 'zh-CN', label: t('language.chinese'), ariaLabel: t('language.useChinese') },
  ];
  // Mobile navigation drawer toggle
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [showConfigDrawer, setShowConfigDrawer] = useState(false);
  const [missionExpanded, setMissionExpanded] = useState(false);

  const newProjectForm = useNewProjectForm(setNewProjectModalOpen);
  const {
    openNewProjectModal,
    closeNewProjectModal,
    idea,
    setIdea,
    projectName,
    setProjectName,
    startProjectKey,
    setStartProjectKey,
    modelProfile,
    setModelProfile,
    deliveryMode,
    setDeliveryMode,
    deliveryStandards,
    setDeliveryStandards,
    roleModelOverrides,
    setRoleModelOverrides,
    projectRoot,
    setProjectRoot,
    projectConfig,
    setProjectConfig,
    taskSync,
    setTaskSync,
    workerBackendMode,
    setWorkerBackendMode,
    deploymentBackendMode,
    setDeploymentBackendMode,
    startMode,
    setStartMode,
    dryRun,
    setDryRun,
    isNewProjectMode,
    initGit,
    setInitGit,
    createRemote,
    setCreateRemote,
    remotePrivate,
    setRemotePrivate,
    gitWarnings,
    setGitWarnings,
    gitRemoteCreated,
    setGitRemoteCreated,
    showGitNotice,
    setShowGitNotice,
    planeApiHost,
    setPlaneApiHost,
    planeWorkspaceSlug,
    setPlaneWorkspaceSlug,
    planeProjectId,
    setPlaneProjectId,
    planeApiKeyEnv,
    setPlaneApiKeyEnv,
    planeMcpServer,
    setPlaneMcpServer,
    stagingSshTarget,
    setStagingSshTarget,
    stagingRemoteDir,
    setStagingRemoteDir,
    stagingGitRef,
    setStagingGitRef,
    stagingPublicUrl,
    setStagingPublicUrl,
    stagingDeployCommand,
    setStagingDeployCommand,
    stagingHealthcheckCommand,
    setStagingHealthcheckCommand,
    stagingSmokeCommand,
    setStagingSmokeCommand,
    deliveryContractRequired,
    setDeliveryContractRequired,
    deliveryContractMinEvidence,
    setDeliveryContractMinEvidence,
    deliveryContractFatalPatterns,
    setDeliveryContractFatalPatterns,
    deliveryContractAcceptance,
    setDeliveryContractAcceptance,
  } = newProjectForm;

  const dispatchTrackForm = useDispatchTrackForm();
  const {
    dispatchName,
    setDispatchName,
    dispatchRole,
    setDispatchRole,
    dispatchCustomRole,
    setDispatchCustomRole,
    dispatchAdapter,
    setDispatchAdapter,
    dispatchCustomAdapter,
    setDispatchCustomAdapter,
    dispatchTask,
    setDispatchTask,
    dispatchConstraints,
    setDispatchConstraints,
    dispatchVerify,
    setDispatchVerify,
    dispatchLoading,
    setDispatchLoading,
    dispatchError,
    setDispatchError,
    dispatchSuccess,
    setDispatchSuccess,
    resetDispatchForm,
  } = dispatchTrackForm;

  // Form submit button loading states
  const [actionLoading, setActionLoading] = useState(false);
  const [generatingPrd, setGeneratingPrd] = useState(false);
  const [startingWorkflow, setStartingWorkflow] = useState(false);
  const [smokeRunLoading, setSmokeRunLoading] = useState(false);
  const [approvingAction, setApprovingAction] = useState(null);

  // Changes requested note states
  const [changesRequestedNote, setChangesRequestedNote] = useState('');
  const [showChangesRequestedNoteKey, setShowChangesRequestedNoteKey] = useState(null);

  const { graphData, loadingGraph, graphError, fetchWorkflowGraph } = useWorkflowGraphData();
  const { devLoopData, loadingDevLoop, devLoopError, fetchDevLoop } = useDevLoopData();
  const memory = useMemoryData(projectKey);
  const {
    memoryData,
    loadingMemory,
    memoryError,
    memoryEventDetail,
    setMemoryEventDetail,
    memoryEventBrowsers,
    fetchMemory,
    handleOpenMemoryEvent,
    handleToggleMemoryEvents,
    handleMemoryEventSearchChange,
  } = memory;
  const crossMemorySearch = useCrossProjectMemorySearch(projectKey, activeTab, agentsMemorySubTab);
  const {
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
  } = crossMemorySearch;
  const { copiedText, copyFailedText, copyCommand } = useClipboardFeedback();

  const { directoryPickerModal, openDirectoryPicker } = useDirectoryPicker(setProjectRoot);

  const gateDebateReviews = data?.approval_reviews || [];
  const actionableGateReview = gateDebateReviews.find(r => r.is_actionable) || gateDebateReviews[0];
  const messageFeeds = useMessageFeeds({ activeTab, projectKey, data, actionableGateReview });
  const {
    gateDebateProjectKey,
    gateDebateRunId,
    debateRun,
    visibleGateDebate,
    messageFeedProjectKey,
    messageFeedRunId,
    messageFeed,
    targetMessageFeedKey,
    handleRunDebate,
    getResolvingDebateRef,
  } = messageFeeds;

  const showToast = (msg, isError = false) => {
    setMessage({ text: msg, isError });
    setTimeout(() => setMessage(null), 5000);
  };

  const fetchState = async (key, showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const url = key ? `/api/state?project=${encodeURIComponent(key)}` : '/api/state';
      const response = await fetch(url);
      if (!response.ok) {
        const errPayload = await response.json();
        throw new Error(errPayload.error || 'Failed to fetch state');
      }
      const payload = await response.json();
      setData(payload);

      // Seed form values when switching to a project dashboard
      if (payload.type === 'project' && payload.start_context) {
        const sc = payload.start_context;
        setProjectName(sc.project_name || '');
        setStartProjectKey(sc.project_key || '');
        setModelProfile(sc.model_profile || 'balanced');
        setDeliveryMode(sc.delivery_mode || 'init_project');
        setProjectConfig(sc.project_config || '');
        setWorkerBackendMode(sc.worker_backend?.mode || 'codex');

        const repos = sc.project_report?.repos || {};
        setProjectRoot(resolveProjectRootFromRepos(repos));

        // Task backend
        const tb = sc.task_backend || {};
        const syncs = tb.sync || [];
        setTaskSync(syncs[0] || '');
        if (tb.plane) {
          setPlaneApiHost(tb.plane.api_host_url || 'https://api.plane.so/');
          setPlaneWorkspaceSlug(tb.plane.workspace_slug || '');
          setPlaneProjectId(tb.plane.project_id || '');
          setPlaneApiKeyEnv(tb.plane.api_key_env || 'PLANE_API_KEY');
          setPlaneMcpServer(tb.plane.mcp_server || 'plane');
        }

        // Deployment
        if (sc.deployment_backend) {
          const db = sc.deployment_backend;
          setDeploymentBackendMode(db.mode || '');
          setStagingSshTarget(db.staging_ssh_target || '');
          setStagingRemoteDir(db.staging_remote_dir || '');
          setStagingGitRef(db.staging_git_ref || '');
          setStagingPublicUrl(db.staging_public_url || '');
          setStagingDeployCommand(db.staging_deploy_command || '');
          setStagingHealthcheckCommand(db.staging_healthcheck_command || '');
          setStagingSmokeCommand(db.staging_smoke_command || '');
        }

        if (showLoading) {
          seedWorkerBackendSettings(sc);
        }

        // Delivery Contract
        if (sc.delivery_contract) {
          const dc = sc.delivery_contract;
          setDeliveryContractRequired(dc.required || false);
          setDeliveryContractMinEvidence(dc.minimum_evidence || 'infrastructure');
          // Format fatal patterns
          const fp = sc.project_report?.project?.delivery_contract?.fatal_output_patterns || {};
          setDeliveryContractFatalPatterns(Object.values(fp).join('\n'));
          // Format staging acceptance
          const sa = sc.project_report?.project?.delivery_contract?.staging_acceptance || {};
          const saLines = Object.entries(sa).map(([id, item]) => `${id}|${item.type}|${item.command}`).join('\n');
          setDeliveryContractAcceptance(saLines);
        }

        // Role model overrides text
        if (sc.role_model_overrides) {
          const lines = Object.entries(sc.role_model_overrides).map(([role, model]) => `${role}=${model}`).join('\n');
          setRoleModelOverrides(lines);
        }
      }
    } catch (err) {
      setError(err.message);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  const workerBackendSettings = useWorkerBackendSettings({
    activeTab,
    agentsMemorySubTab,
    data,
    projectKey,
    showToast,
    fetchState,
  });
  const {
    panelGlobalMode,
    setPanelGlobalMode,
    panelRoleOverrides,
    panelRoleModels,
    workerRoutingMode,
    setWorkerRoutingMode,
    simpleRoutingBackends,
    setSimpleRoutingBackends,
    customModelSelected,
    configuringRoles,
    setConfiguringRoles,
    backendsError,
    backendsSuccess,
    savingBackends,
    discoveredAgents,
    loadingDiscoveredAgents,
    safeDiscoveredAgents,
    roleBackendOptions,
    workerRoles,
    discoveredAgentByBackend,
    usedBackends,
    getBackendOptionLabel,
    seedWorkerBackendSettings,
    handleAgentChange,
    handleApplySimpleRouting,
    handleModelDropdownChange,
    handleCustomModelChange,
    handleSaveWorkerBackends,
  } = workerBackendSettings;

  const approvalEditor = useApprovalEditor({
    projectKey,
    showToast,
    fetchState,
    setActiveTab,
    setActionLoading,
  });
  const {
    editingArtifact,
    setEditingArtifact,
    editorContent,
    setEditorContent,
    refineLoading,
    proposedRevision,
    openQuestions,
    openQuestionsAnswers,
    setOpenQuestionsAnswers,
    fetchingOpenQuestions,
    answeringOpenQuestions,
    handleEditArtifact,
    handleOpenQuestionsFromApproval,
    handleSaveArtifact,
    handleApplyAnswers,
    handleRefineArtifact,
    handleAcceptRefinement,
    handleDiscardRefinement,
  } = approvalEditor;

  // Auto-refresh timer when there is an active run
  useEffect(() => {
    let interval = null;
    if (projectKey && data && data.active_run) {
      interval = setInterval(() => {
        fetchState(projectKey, false);
      }, 5000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchState is intentionally omitted so polling follows active run state only.
  }, [projectKey, data]);

  // Fetch state on mount and projectKey changes
  useEffect(() => {
    Promise.resolve().then(() => {
      fetchState(projectKey, true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchState is intentionally omitted so loading fetches only when the project key changes.
  }, [projectKey]);

  // Lazy fetch workflow graph data when the Workflow Graph tab is opened
  useEffect(() => {
    if (activeTab === 'workflow' && projectKey) {
      fetchWorkflowGraph(projectKey);
    }
  }, [activeTab, fetchWorkflowGraph, projectKey]);

  // Lazy fetch memory data when the Memory tab is opened
  useEffect(() => {
    if ((activeTab === 'memory' || (activeTab === 'agents_memory' && agentsMemorySubTab === 'memory')) && projectKey) {
      fetchMemory(projectKey);
    }
  }, [activeTab, agentsMemorySubTab, fetchMemory, projectKey]);

  // Lazy fetch devloop data when the Dev Loop tab is opened
  useEffect(() => {
    if (activeTab === 'devloop') {
      fetchDevLoop(projectKey);
    }
  }, [activeTab, fetchDevLoop, projectKey]);

  // POST action handlers
  const handleTerminate = async (key) => {
    if (!window.confirm(`Are you sure you want to terminate the active run for project ${key}?`)) return;
    setActionLoading(true);
    try {
      const response = await fetch('/api/terminate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: key }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to terminate run');
      showToast(`Terminated run for project ${key}`);
      fetchState(key);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteProject = async (key) => {
    if (!window.confirm(`Delete project ${key} from GoodTeam? This removes the local project config and GoodTeam state only. It will not delete referenced repos.`)) return;
    setActionLoading(true);
    try {
      const response = await fetch('/api/project-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: key }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to delete project');
      showToast(`Deleted project ${key}`);
      if (projectKey === key) {
        updateUrl('');
      } else {
        fetchState(projectKey);
      }
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRetryBlocked = async () => {
    setActionLoading(true);
    try {
      const response = await fetch('/api/retry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: projectKey }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to retry blocked workers');
      showToast('Retrying blocked workers...');
      fetchState(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
    }
  };

  const handleCreateConfig = async () => {
    setActionLoading(true);
    showToast('Creating local project config...');
    try {
      const parsedOverrides = parseRoleModelOverridesMap(roleModelOverrides);
      const customDeliveryPayload = buildCustomDeliveryPayload(deliveryMode, deliveryStandards);

      const response = await fetch('/api/project-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_name: projectName,
          project_key: startProjectKey || projectKey,
          project_root: projectRoot,
          task_sync: taskSync ? [taskSync] : [],
          plane_api_host_url: planeApiHost,
          plane_workspace_slug: planeWorkspaceSlug,
          plane_project_id: planeProjectId,
          plane_api_key_env: planeApiKeyEnv,
          plane_mcp_server: planeMcpServer,
          worker_backend_mode: workerBackendMode,
          deployment_backend_mode: deploymentBackendMode,
          staging_ssh_target: stagingSshTarget,
          staging_remote_dir: stagingRemoteDir,
          staging_git_ref: stagingGitRef,
          staging_public_url: stagingPublicUrl,
          staging_deploy_command: stagingDeployCommand,
          staging_healthcheck_command: stagingHealthcheckCommand,
          staging_smoke_command: stagingSmokeCommand,
          delivery_contract_required: deliveryContractRequired,
          delivery_contract_minimum_evidence: deliveryContractMinEvidence,
          delivery_contract_fatal_patterns: deliveryContractFatalPatterns,
          delivery_contract_acceptance: deliveryContractAcceptance,
          model_profile: modelProfile,
          delivery_mode: deliveryMode,
          ...customDeliveryPayload,
          role_model_overrides: parsedOverrides,
          ...(isNewProjectMode ? {
            init_git: initGit,
            create_remote: createRemote,
            remote_private: remotePrivate
          } : {})
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to create config');
      setProjectConfig(res.project_config);
      showToast('Local config created successfully');

      if (res.git) {
        const warnings = res.git.warnings || [];
        const remoteCreated = res.git.remote_created;
        setGitWarnings(warnings);
        setGitRemoteCreated(!!remoteCreated);
        if (warnings.length > 0 || remoteCreated) {
          setShowGitNotice(true);
        } else {
          setShowGitNotice(false);
        }
        if (warnings.length > 0) {
          showToast(`Git warnings: ${warnings.join(', ')}`, true);
        } else if (remoteCreated) {
          showToast('GitHub remote repository created successfully!');
        }
      } else {
        setShowGitNotice(false);
        setGitWarnings([]);
        setGitRemoteCreated(false);
      }

      fetchState(projectKey || res.project_key);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
    }
  };

  const handleStartWorkflow = async () => {
    if (!idea.trim()) {
      showToast('Workflow goal is required', true);
      return;
    }
    setStartingWorkflow(true);
    setActionLoading(true);
    showToast('Starting workflow...');
    try {
      const overrides = parseRoleModelOverrideLines(roleModelOverrides);
      const customDeliveryPayload = buildCustomDeliveryPayload(deliveryMode, deliveryStandards);

      const response = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idea: idea.trim(),
          project_key: startProjectKey || projectKey,
          model_profile: modelProfile,
          delivery_mode: deliveryMode,
          ...customDeliveryPayload,
          source_type: startMode,
          project_config: projectConfig,
          role_model_overrides: overrides,
          dry_run: dryRun,
          ...(isNewProjectMode ? {
            init_git: initGit,
            create_remote: createRemote,
            remote_private: remotePrivate
          } : {})
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to start workflow');

      showToast(dryRun ? 'Dry run simulation finished' : 'Workflow started!');

      if (res.git) {
        const warnings = res.git.warnings || [];
        const remoteCreated = res.git.remote_created;
        setGitWarnings(warnings);
        setGitRemoteCreated(!!remoteCreated);
        if (warnings.length > 0 || remoteCreated) {
          setShowGitNotice(true);
        } else {
          setShowGitNotice(false);
        }
        if (warnings.length > 0) {
          showToast(`Git warnings: ${warnings.join(', ')}`, true);
        } else if (remoteCreated) {
          showToast('GitHub remote repository created successfully!');
        }
      } else {
        setShowGitNotice(false);
        setGitWarnings([]);
        setGitRemoteCreated(false);
      }

      if (!dryRun) {
        setIdea('');
        const pk = startProjectKey || projectKey || res.project_key;
        updateUrl(pk);
        setNewProjectModalOpen(false);
      } else {
        showToast(`[Dry Run Preview] Next node: ${res.next_node}`);
      }
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
      setStartingWorkflow(false);
    }
  };

  const handleDispatchTrack = async (e) => {
    if (e && e.preventDefault) {
      e.preventDefault();
    }
    if (!dispatchName.trim()) {
      setDispatchError('Track name is required');
      return;
    }
    if (!dispatchTask.trim()) {
      setDispatchError('Task description is required');
      return;
    }

    const finalRole = dispatchRole === 'custom' ? dispatchCustomRole.trim() : dispatchRole;
    if (!finalRole) {
      setDispatchError('Role is required');
      return;
    }

    const finalAdapter = dispatchAdapter === 'custom' ? dispatchCustomAdapter.trim() : dispatchAdapter;
    if (!finalAdapter) {
      setDispatchError('Adapter is required');
      return;
    }

    setDispatchLoading(true);
    setDispatchError(null);
    setDispatchSuccess(null);

    try {
      const body = {
        name: dispatchName.trim(),
        role: finalRole,
        adapter: finalAdapter,
        task: dispatchTask.trim(),
      };
      if (dispatchConstraints.trim()) {
        body.constraints = dispatchConstraints.trim();
      }
      if (dispatchVerify.trim()) {
        body.verify = dispatchVerify.trim();
      }

      const response = await fetch('/api/dispatch-track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to dispatch track');
      }

      // Safeguard response shape
      const successData = {
        run_id: res?.run_id || '',
        name: res?.name || '',
        branch: res?.branch || '',
        worktree: res?.worktree || '',
        status: res?.status || 'started',
      };
      setDispatchSuccess(successData);

      resetDispatchForm();

      showToast('Track dispatched successfully!');

      // Refresh the Dev Loop panel
      fetchDevLoop(projectKey);
    } catch (err) {
      setDispatchError(err.message);
      showToast(err.message, true);
    } finally {
      setDispatchLoading(false);
    }
  };

  const handleSmokeRun = async () => {
    setSmokeRunLoading(true);
    try {
      const response = await fetch('/api/smoke-run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          suite: 'self',
          cycles: 1,
          dry_run: true,
          worker_mode: 'fake',
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to start smoke run');
      }
      showToast(`Smoke run started: ${res.run_id || 'queued'}`);
      setTimeout(() => fetchState(projectKey, false), 600);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setSmokeRunLoading(false);
    }
  };

  const handleApproveDecision = async (approvalKey, decision, debateRef = '', note = '') => {
    setApprovingAction(`${approvalKey}:${decision}`);
    setActionLoading(true);
    try {
      const body = {
        project_key: projectKey,
        approval: approvalKey,
        decision,
      };
      if (debateRef) {
        body.debate_ref = debateRef;
      }
      if (decision === 'changes_requested') {
        body.note = note;
      }
      const response = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to submit decision');
      showToast(`Decision '${decision}' submitted for ${approvalKey.toUpperCase()}`);
      
      // Reset note states on success
      setChangesRequestedNote('');
      setShowChangesRequestedNoteKey(null);
      
      fetchState(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
      setApprovingAction(null);
    }
  };

  const handleGeneratePrd = async () => {
    if (!projectKey) return;
    setGeneratingPrd(true);
    showToast('Generating PRD...');
    try {
      const response = await fetch('/api/generate-prd', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to generate PRD');
      showToast('PRD generated successfully');
      fetchState(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setGeneratingPrd(false);
    }
  };

  const newProjectModal = (
    <NewProjectModalContainer
      actionLoading={actionLoading}
      closeNewProjectModal={closeNewProjectModal}
      createRemote={createRemote}
      deliveryContractAcceptance={deliveryContractAcceptance}
      deliveryContractFatalPatterns={deliveryContractFatalPatterns}
      deliveryContractMinEvidence={deliveryContractMinEvidence}
      deliveryContractRequired={deliveryContractRequired}
      deliveryMode={deliveryMode}
      deliveryStandards={deliveryStandards}
      deploymentBackendMode={deploymentBackendMode}
      dryRun={dryRun}
      handleCreateConfig={handleCreateConfig}
      handleStartWorkflow={handleStartWorkflow}
      idea={idea}
      initGit={initGit}
      isNewProjectMode={isNewProjectMode}
      modelProfile={modelProfile}
      newProjectModalOpen={newProjectModalOpen}
      openDirectoryPicker={openDirectoryPicker}
      planeApiHost={planeApiHost}
      planeApiKeyEnv={planeApiKeyEnv}
      planeMcpServer={planeMcpServer}
      planeProjectId={planeProjectId}
      planeWorkspaceSlug={planeWorkspaceSlug}
      projectConfig={projectConfig}
      projectName={projectName}
      projectRoot={projectRoot}
      remotePrivate={remotePrivate}
      roleModelOverrides={roleModelOverrides}
      setCreateRemote={setCreateRemote}
      setDeliveryContractAcceptance={setDeliveryContractAcceptance}
      setDeliveryContractFatalPatterns={setDeliveryContractFatalPatterns}
      setDeliveryContractMinEvidence={setDeliveryContractMinEvidence}
      setDeliveryContractRequired={setDeliveryContractRequired}
      setDeliveryMode={setDeliveryMode}
      setDeliveryStandards={setDeliveryStandards}
      setDeploymentBackendMode={setDeploymentBackendMode}
      setDryRun={setDryRun}
      setIdea={setIdea}
      setInitGit={setInitGit}
      setModelProfile={setModelProfile}
      setPlaneApiHost={setPlaneApiHost}
      setPlaneApiKeyEnv={setPlaneApiKeyEnv}
      setPlaneMcpServer={setPlaneMcpServer}
      setPlaneProjectId={setPlaneProjectId}
      setPlaneWorkspaceSlug={setPlaneWorkspaceSlug}
      setProjectConfig={setProjectConfig}
      setProjectName={setProjectName}
      setProjectRoot={setProjectRoot}
      setRemotePrivate={setRemotePrivate}
      setRoleModelOverrides={setRoleModelOverrides}
      setShowConfigDrawer={setShowConfigDrawer}
      setStagingDeployCommand={setStagingDeployCommand}
      setStagingGitRef={setStagingGitRef}
      setStagingHealthcheckCommand={setStagingHealthcheckCommand}
      setStagingPublicUrl={setStagingPublicUrl}
      setStagingRemoteDir={setStagingRemoteDir}
      setStagingSmokeCommand={setStagingSmokeCommand}
      setStagingSshTarget={setStagingSshTarget}
      setStartMode={setStartMode}
      setStartProjectKey={setStartProjectKey}
      setTaskSync={setTaskSync}
      setWorkerBackendMode={setWorkerBackendMode}
      showConfigDrawer={showConfigDrawer}
      stagingDeployCommand={stagingDeployCommand}
      stagingGitRef={stagingGitRef}
      stagingHealthcheckCommand={stagingHealthcheckCommand}
      stagingPublicUrl={stagingPublicUrl}
      stagingRemoteDir={stagingRemoteDir}
      stagingSmokeCommand={stagingSmokeCommand}
      stagingSshTarget={stagingSshTarget}
      startingWorkflow={startingWorkflow}
      startMode={startMode}
      startProjectKey={startProjectKey}
      taskSync={taskSync}
      workerBackendMode={workerBackendMode}
    />
  );

  // Render project overview (Multi-project control room)
  if (!projectKey) {
    return (
      <ProjectsHomePage
        actionLoading={actionLoading}
        darkMode={darkMode}
        data={data}
        directoryPickerModal={directoryPickerModal}
        error={error}
        handleDeleteProject={handleDeleteProject}
        handleTerminate={handleTerminate}
        languageSwitcher={(
          <LanguageSwitcher
            activeValue={languagePreference}
            onChange={setLanguagePreference}
            options={languageOptions}
            size="wide"
          />
        )}
        loading={loading}
        message={message}
        newProjectModal={newProjectModal}
        setDarkMode={setDarkMode}
        setNewProjectModalOpen={openNewProjectModal}
        setStartMode={setStartMode}
        setTheme={setTheme}
        t={t}
        theme={theme}
        updateUrl={updateUrl}
      />
    );
  }
  // Dashboard Detailed View for a single project
  return (
    <ProjectDashboard
      actionLoading={actionLoading}
      activeTab={activeTab}
      agentsMemorySubTab={agentsMemorySubTab}
      answeringOpenQuestions={answeringOpenQuestions}
      approvingAction={approvingAction}
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
      darkMode={darkMode}
      data={data}
      debateRun={debateRun}
      deliveryMode={deliveryMode}
      deliveryStandards={deliveryStandards}
      devLoopData={devLoopData}
      devLoopError={devLoopError}
      directoryPickerModal={directoryPickerModal}
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
      error={error}
      fetchingOpenQuestions={fetchingOpenQuestions}
      gateDebateProjectKey={gateDebateProjectKey}
      gateDebateRunId={gateDebateRunId}
      generatingPrd={generatingPrd}
      getBackendOptionLabel={getBackendOptionLabel}
      getMissionSummary={getMissionSummary}
      getResolvingDebateRef={getResolvingDebateRef}
      gitRemoteCreated={gitRemoteCreated}
      gitWarnings={gitWarnings}
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
      idea={idea}
      isNewProjectMode={isNewProjectMode}
      languageOptions={languageOptions}
      languagePreference={languagePreference}
      loading={loading}
      loadingDevLoop={loadingDevLoop}
      loadingDiscoveredAgents={loadingDiscoveredAgents}
      loadingGraph={loadingGraph}
      loadingMemory={loadingMemory}
      memoryData={memoryData}
      memoryError={memoryError}
      memoryEventBrowsers={memoryEventBrowsers}
      memoryEventDetail={memoryEventDetail}
      message={message}
      messageFeed={messageFeed}
      messageFeedProjectKey={messageFeedProjectKey}
      messageFeedRunId={messageFeedRunId}
      missionExpanded={missionExpanded}
      mobileMenuOpen={mobileMenuOpen}
      modelProfile={modelProfile}
      newProjectModal={newProjectModal}
      openQuestions={openQuestions}
      openQuestionsAnswers={openQuestionsAnswers}
      panelGlobalMode={panelGlobalMode}
      panelRoleModels={panelRoleModels}
      panelRoleOverrides={panelRoleOverrides}
      projectKey={projectKey}
      proposedRevision={proposedRevision}
      refineLoading={refineLoading}
      roleBackendOptions={roleBackendOptions}
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
      setDarkMode={setDarkMode}
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
      setLanguagePreference={setLanguagePreference}
      setMemoryEventDetail={setMemoryEventDetail}
      setMissionExpanded={setMissionExpanded}
      setMobileMenuOpen={setMobileMenuOpen}
      setModelProfile={setModelProfile}
      setOpenQuestionsAnswers={setOpenQuestionsAnswers}
      setPanelGlobalMode={setPanelGlobalMode}
      setShowChangesRequestedNoteKey={setShowChangesRequestedNoteKey}
      setShowGitNotice={setShowGitNotice}
      setSimpleRoutingBackends={setSimpleRoutingBackends}
      setStartMode={setStartMode}
      setTheme={setTheme}
      setWorkerRoutingMode={setWorkerRoutingMode}
      showChangesRequestedNoteKey={showChangesRequestedNoteKey}
      showGitNotice={showGitNotice}
      simpleRoutingBackends={simpleRoutingBackends}
      smokeRunLoading={smokeRunLoading}
      startMode={startMode}
      startingWorkflow={startingWorkflow}
      t={t}
      targetMessageFeedKey={targetMessageFeedKey}
      theme={theme}
      updateUrl={updateUrl}
      usedBackends={usedBackends}
      visibleGateDebate={visibleGateDebate}
      workerRoles={workerRoles}
      workerRoutingMode={workerRoutingMode}
    />
  );
}
