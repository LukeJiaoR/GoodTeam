import { useState, useEffect, useCallback } from 'react';
import { AlertTriangle } from 'lucide-react';
import LanguageSwitcher from './components/LanguageSwitcher';
import NewProjectModalContainer from './components/NewProjectModalContainer';
import ProjectDashboard from './components/ProjectDashboard';
import ProjectsHomePage from './components/ProjectsHomePage';
import ReportBugModal from './components/ReportBugModal';
import { useAppearancePreferences } from './hooks/useAppearancePreferences';
import { useClipboardFeedback } from './hooks/useClipboardFeedback';
import { useDirectoryPicker } from './hooks/useDirectoryPicker';
import { useDispatchTrackForm } from './hooks/useDispatchTrackForm';
import { useGitInitAction } from './hooks/useGitInitAction';
import { useNewProjectForm } from './hooks/useNewProjectForm';
import { useProjectUrl } from './hooks/useProjectUrl';
import { useDevLoopData } from './hooks/useDevLoopData';
import { useMemoryData } from './hooks/useMemoryData';
import { useCrossProjectMemorySearch } from './hooks/useCrossProjectMemorySearch';
import { useApprovalEditor } from './hooks/useApprovalEditor';
import { useWorkerBackendSettings } from './hooks/useWorkerBackendSettings';
import { useMessageFeeds } from './hooks/useMessageFeeds';
import { useWorkflowGraphData } from './hooks/useWorkflowGraphData';
import { formatDeliveryContractAcceptance, formatDeliveryContractFatalPatterns } from './utils/deliveryContract';
import { buildCustomDeliveryPayload } from './utils/deliveryPayload';
import { getMissionSummary } from './utils/missionSummary';
import { resolveProjectRootFromRepos } from './utils/projectRoot';
import { FALLBACK_AGENT_MODES, INIT_WORKER_ROUTING_BUCKET_KEYS, WORKER_ROUTING_BUCKETS } from './constants/workers';
import {
  expandInitBucketModelOverrides,
  expandInitBucketWorkerOverrides,
  formatRoleModelOverridesMap,
  formatRoleModelOverridesText,
  parseRoleModelOverrideLines,
  parseRoleModelOverridesMap,
} from './utils/roleModelOverrides';
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

  // Report Bug modal state
  const [reportBugModalOpen, setReportBugModalOpen] = useState(false);
  const [reportBugTitle, setReportBugTitle] = useState('');
  const [reportBugDescription, setReportBugDescription] = useState('');
  const [reportBugSeverity, setReportBugSeverity] = useState('p2');
  const [reportBugError, setReportBugError] = useState(null);
  const [reportingBug, setReportingBug] = useState(false);


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
    initWorkerBucketSelections,
    setInitWorkerBucketSelections,
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
    contextSources,
    setContextSources,
    contextEstimate,
    setContextEstimate,
    previewingContext,
    setPreviewingContext,
    generatingContext,
    setGeneratingContext,
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
  const showToast = useCallback((msg, isError = false) => {
    setMessage({ text: msg, isError });
    setTimeout(() => setMessage(null), 5000);
  }, []);

  const gitInitAction = useGitInitAction({
    projectKey,
    fetchDevLoop,
    showToast,
  });
  const {
    devLoopCreateRemote,
    setDevLoopCreateRemote,
    devLoopRemotePrivate,
    setDevLoopRemotePrivate,
    gitInitLoading,
    gitInitWarnings,
    handleGitInit,
  } = gitInitAction;
  const { copiedText, copyFailedText, copyCommand } = useClipboardFeedback();

  const { directoryPickerModal, openDirectoryPicker } = useDirectoryPicker();

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
        const archivedIdea = String(sc.latest_archived_run?.idea || '').trim();
        const archivedSourceType = String(sc.latest_archived_run?.source_type || '').trim();
        if (!payload.active_run && archivedIdea) {
          setStartMode(archivedSourceType === 'existing_project' ? 'existing_project' : 'new_project');
          setIdea(previous => {
            return previous.trim() ? previous : archivedIdea;
          });
        }

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
          setDeliveryContractFatalPatterns(formatDeliveryContractFatalPatterns(sc.project_report?.project?.delivery_contract?.fatal_output_patterns || {}));
          // Format staging acceptance
          setDeliveryContractAcceptance(formatDeliveryContractAcceptance(sc.project_report?.project?.delivery_contract?.staging_acceptance || {}));
        }

        // Role model overrides text
        if (sc.role_model_overrides) {
          setRoleModelOverrides(formatRoleModelOverridesText(sc.role_model_overrides));
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
    fetchDiscoveredAgents,
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

  const initWorkerRoutingBuckets = WORKER_ROUTING_BUCKETS.filter((bucket) => (
    INIT_WORKER_ROUTING_BUCKET_KEYS.includes(bucket.key)
  ));
  const initBucketWorkers = expandInitBucketWorkerOverrides(
    initWorkerBucketSelections,
    initWorkerRoutingBuckets
  );
  const initBucketFallbackBackends = Object.fromEntries(
    initWorkerRoutingBuckets.map((bucket) => [bucket.key, workerBackendMode || panelGlobalMode || 'codex'])
  );
  const initBucketRoleModels = expandInitBucketModelOverrides(
    initWorkerBucketSelections,
    initWorkerRoutingBuckets,
    initBucketFallbackBackends
  );

  useEffect(() => {
    if (newProjectModalOpen && discoveredAgents === null && !loadingDiscoveredAgents) {
      fetchDiscoveredAgents();
    }
  }, [discoveredAgents, fetchDiscoveredAgents, loadingDiscoveredAgents, newProjectModalOpen]);

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
      fetchState(projectKey === key ? key : '');
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
      const parsedOverrides = {
        ...parseRoleModelOverridesMap(roleModelOverrides),
        ...initBucketRoleModels,
      };
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
          workers: initBucketWorkers,
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

  const cleanContextSources = () => (
    contextSources
      .map((source) => ({
        kind: source.kind,
        value: source.value.trim(),
      }))
      .filter((source) => source.value)
  );

  const handleContextEstimate = async () => {
    const targetProject = startProjectKey || projectKey;
    if (!targetProject) {
      showToast('Project key is required before previewing context cost', true);
      return;
    }
    setPreviewingContext(true);
    try {
      const response = await fetch('/api/context-estimate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: targetProject,
          sources: cleanContextSources(),
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to preview context cost');
      setContextEstimate(res);
      showToast(`Context preview ready: ~${res.estimate.est_tokens.toLocaleString()} tokens`);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setPreviewingContext(false);
    }
  };

  const handleContextGenerate = async () => {
    const targetProject = startProjectKey || projectKey;
    if (!targetProject) {
      showToast('Project key is required before generating context', true);
      return;
    }
    if (!contextEstimate) {
      showToast('Preview context cost before generating', true);
      return;
    }
    setGeneratingContext(true);
    try {
      const response = await fetch('/api/context-generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: targetProject,
          sources: cleanContextSources(),
          confirm: true,
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to generate context');
      showToast('Project context generated');
      fetchState(targetProject);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setGeneratingContext(false);
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
      const customDeliveryPayload = buildCustomDeliveryPayload(deliveryMode, deliveryStandards);
      const hasInitBucketOverrides = Object.keys(initBucketWorkers).length > 0 || Object.keys(initBucketRoleModels).length > 0;
      const mergedRoleModelOverrides = {
        ...parseRoleModelOverridesMap(roleModelOverrides),
        ...initBucketRoleModels,
      };
      const overrides = [
        ...parseRoleModelOverrideLines(roleModelOverrides),
        ...parseRoleModelOverrideLines(formatRoleModelOverridesMap(initBucketRoleModels)),
      ];
      let workflowProjectConfig = projectConfig;

      if (hasInitBucketOverrides && workflowProjectConfig) {
        const updateResponse = await fetch('/api/project-config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            action: 'update_worker_backends',
            project: startProjectKey || projectKey,
            worker_backend_mode: workerBackendMode,
            workers: initBucketWorkers,
            role_models: mergedRoleModelOverrides,
          }),
        });
        const updateResult = await updateResponse.json();
        if (!updateResponse.ok) throw new Error(updateResult.error || 'Failed to update worker config');
      }

      if (hasInitBucketOverrides && !workflowProjectConfig) {
        const configResponse = await fetch('/api/project-config', {
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
            workers: initBucketWorkers,
            role_model_overrides: mergedRoleModelOverrides,
            model_profile: modelProfile,
            delivery_mode: deliveryMode,
            ...customDeliveryPayload,
            ...(isNewProjectMode ? {
              init_git: initGit,
              create_remote: createRemote,
              remote_private: remotePrivate
            } : {})
          }),
        });
        const configResult = await configResponse.json();
        if (!configResponse.ok) throw new Error(configResult.error || 'Failed to create config');
        workflowProjectConfig = configResult.project_config;
        setProjectConfig(configResult.project_config);
      }

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
          project_config: workflowProjectConfig,
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
      if (decision === 'changes_requested' || decision === 'rejected') {
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

  const openReportBugModal = () => {
    setReportBugError(null);
    setReportBugModalOpen(true);
  };

  const closeReportBugModal = () => {
    setReportBugModalOpen(false);
  };

  const handleReportBug = async () => {
    if (!projectKey || !reportBugDescription.trim()) return;
    setReportingBug(true);
    setReportBugError(null);
    try {
      const response = await fetch('/api/report-bug', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          title: reportBugTitle,
          description: reportBugDescription,
          severity: reportBugSeverity,
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to report bug');
      // Reset the form and close the modal.
      setReportBugModalOpen(false);
      setReportBugTitle('');
      setReportBugDescription('');
      setReportBugSeverity('p2');
      showToast('Fix workflow started — review the fix plan');
      fetchState(projectKey);
      setActiveTab('approvals');
    } catch (err) {
      setReportBugError(err.message);
    } finally {
      setReportingBug(false);
    }
  };

  const reportBugModal = reportBugModalOpen ? (
    <ReportBugModal
      description={reportBugDescription}
      onClose={closeReportBugModal}
      onSubmit={handleReportBug}
      projectKey={projectKey}
      reportBugError={reportBugError}
      setDescription={setReportBugDescription}
      setSeverity={setReportBugSeverity}
      setTitle={setReportBugTitle}
      severity={reportBugSeverity}
      submitting={reportingBug}
      title={reportBugTitle}
    />
  ) : null;

  const newProjectModal = (
    <NewProjectModalContainer
      actionLoading={actionLoading}
      closeNewProjectModal={closeNewProjectModal}
      createRemote={createRemote}
      deliveryContractAcceptance={deliveryContractAcceptance}
      deliveryContractFatalPatterns={deliveryContractFatalPatterns}
      deliveryContractMinEvidence={deliveryContractMinEvidence}
      deliveryContractRequired={deliveryContractRequired}
      contextEstimate={contextEstimate}
      contextSources={contextSources}
      deliveryMode={deliveryMode}
      deliveryStandards={deliveryStandards}
      deploymentBackendMode={deploymentBackendMode}
      discoveredAgents={discoveredAgents}
      dryRun={dryRun}
      fallbackAgentModes={FALLBACK_AGENT_MODES}
      generatingContext={generatingContext}
      handleCreateConfig={handleCreateConfig}
      handleContextEstimate={handleContextEstimate}
      handleContextGenerate={handleContextGenerate}
      handleStartWorkflow={handleStartWorkflow}
      idea={idea}
      initGit={initGit}
      initWorkerBucketSelections={initWorkerBucketSelections}
      initWorkerRoutingBuckets={initWorkerRoutingBuckets}
      isNewProjectMode={isNewProjectMode}
      loadingDiscoveredAgents={loadingDiscoveredAgents}
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
      safeDiscoveredAgents={safeDiscoveredAgents}
      setCreateRemote={setCreateRemote}
      setDeliveryContractAcceptance={setDeliveryContractAcceptance}
      setDeliveryContractFatalPatterns={setDeliveryContractFatalPatterns}
      setDeliveryContractMinEvidence={setDeliveryContractMinEvidence}
      setDeliveryContractRequired={setDeliveryContractRequired}
      setContextEstimate={setContextEstimate}
      setContextSources={setContextSources}
      setDeliveryMode={setDeliveryMode}
      setDeliveryStandards={setDeliveryStandards}
      setDeploymentBackendMode={setDeploymentBackendMode}
      setDryRun={setDryRun}
      setIdea={setIdea}
      setInitGit={setInitGit}
      setInitWorkerBucketSelections={setInitWorkerBucketSelections}
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
      previewingContext={previewingContext}
      startingWorkflow={startingWorkflow}
      startMode={startMode}
      startProjectKey={startProjectKey}
      taskSync={taskSync}
      t={t}
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

  // Styled dashboard-level error page for unknown project key
  if (projectKey && error && (error.includes('Project config file not found') || error.includes('Project not found') || error.includes('not find a local project config'))) {
    return (
      <div className={`min-h-screen flex flex-col items-center justify-center p-6 ${darkMode ? 'bg-surface-sunken text-text-secondary dark' : 'bg-surface-sunken text-text-secondary'}`}>
        <div className="w-full max-w-lg bg-surface-raised border-2 border-border-subtle shadow-card rounded-3xl p-8 space-y-6 animate-fade-in physics-card">
          <div className="flex items-center space-x-4">
            <div className="p-3 bg-status-danger-bg text-status-danger rounded-2xl border border-status-danger/20">
              <AlertTriangle className="h-8 w-8 text-status-danger-text" />
            </div>
            <div>
              <span className="text-2xs font-extrabold text-status-danger uppercase tracking-widest text-status-danger-text">Project Error</span>
              <h1 className="text-xl font-black text-text-primary mt-0.5">Project Not Found</h1>
            </div>
          </div>
          
          <div className="text-xs leading-relaxed text-text-secondary space-y-3 font-semibold">
            <p>
              GoodTeam could not find a local project config for key: <code className="px-2 py-0.5 bg-surface-sunken border border-border-subtle rounded-md text-text-primary font-mono select-all font-bold">{projectKey}</code>.
            </p>
            <p className="text-text-muted">
              The project key might be misspelled, or the configuration file does not exist on this machine.
            </p>
          </div>

          <div className="border-t-2 border-border-subtle pt-6 flex flex-col sm:flex-row gap-3">
            <button
              onClick={() => updateUrl('')}
              className="flex-1 bg-accent text-accent-text border-2 border-accent shadow-button font-black px-6 py-3 rounded-2xl text-xs uppercase tracking-wider text-center transition-all duo-button-physics cursor-pointer"
            >
              Back to Projects
            </button>
            <button
              onClick={() => updateUrl('?project=')}
              className="flex-1 bg-surface-raised border-2 border-border-subtle shadow-button text-text-secondary font-black px-6 py-3 rounded-2xl text-xs uppercase tracking-wider text-center transition-all duo-button-physics cursor-pointer hover:bg-surface-sunken"
            >
              Open Overview
            </button>
          </div>
        </div>
      </div>
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
      devLoopCreateRemote={devLoopCreateRemote}
      devLoopRemotePrivate={devLoopRemotePrivate}
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
      gitInitLoading={gitInitLoading}
      gitInitWarnings={gitInitWarnings}
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
      handleGitInit={handleGitInit}
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
      onReportBug={openReportBugModal}
      openQuestions={openQuestions}
      openQuestionsAnswers={openQuestionsAnswers}
      panelGlobalMode={panelGlobalMode}
      panelRoleModels={panelRoleModels}
      panelRoleOverrides={panelRoleOverrides}
      projectKey={projectKey}
      projectRoot={projectRoot}
      proposedRevision={proposedRevision}
      refineLoading={refineLoading}
      reportBugModal={reportBugModal}
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
      setDevLoopCreateRemote={setDevLoopCreateRemote}
      setDevLoopRemotePrivate={setDevLoopRemotePrivate}
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
