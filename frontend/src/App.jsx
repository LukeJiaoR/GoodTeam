import { useState, useEffect, useRef } from 'react';
import {
  LayoutDashboard,
  Users,
  Activity,
  Kanban,
  PlaySquare,
  CheckCircle2,
  Settings,
  Sun,
  Moon,
  Menu,
  X,
  RefreshCw,
  Check,
  AlertTriangle,
  Folder,
  CheckCircle,
  FileCode,
  MessageSquare,
  GitBranch,
  GitPullRequest
} from 'lucide-react';
import FilePath from './components/FilePath';
import LanguageSwitcher from './components/LanguageSwitcher';
import NewProjectModal from './components/NewProjectModal';
import ProjectsHomePage from './components/ProjectsHomePage';
import Spinner from './components/Spinner';
import { useAppearancePreferences } from './hooks/useAppearancePreferences';
import { useDirectoryPicker } from './hooks/useDirectoryPicker';
import { useProjectUrl } from './hooks/useProjectUrl';
import { getBadgeStyle, getStatusLabel } from './utils/status';
import { formatHumanTime } from './utils/time';
import { computeLineDiff, suggestedAnswersForQuestion } from './utils/approvals';
import {
  LANGUAGE_PREFERENCE_AUTO,
} from './i18n';
import WorkflowGraphTab from './components/tabs/WorkflowGraphTab';
import RolesTab from './components/tabs/RolesTab';
import ArtifactsTab from './components/tabs/ArtifactsTab';
import ActionsTab from './components/tabs/ActionsTab';
import AgileBoardTab from './components/tabs/AgileBoardTab';
import SmokeRunsTab from './components/tabs/SmokeRunsTab';
import WorkersTab from './components/tabs/WorkersTab';
import ApprovalsTab from './components/tabs/ApprovalsTab';
import DevLoopTab from './components/tabs/DevLoopTab';
import SystemTab from './components/tabs/SystemTab';
import MemoryTab from './components/tabs/MemoryTab';
import OverviewTab from './components/tabs/OverviewTab';
import AgentsMemoryTab from './components/tabs/AgentsMemoryTab';
import MessagesTab from './components/tabs/MessagesTab';
import {
  DEFAULT_BACKEND_OPTIONS,
  DEFAULT_WORKER_ROLES,
  FALLBACK_AGENT_MODES,
  MODEL_UNAVAILABLE_BACKENDS,
  WORKER_ROUTING_BUCKETS,
  canonicalBackendForOption,
} from './constants/workers';

const EMPTY_GATE_DEBATE = { key: '', messages: [], loading: false, error: '' };
const EMPTY_MESSAGE_FEED = { key: '', messages: [], loading: false, error: '' };

export default function App() {
  const { projectKey, updateUrl } = useProjectUrl();
  const [activeTab, setActiveTab] = useState('overview');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  // Worker backend settings states
  const [panelGlobalMode, setPanelGlobalMode] = useState('codex');
  const [panelRoleOverrides, setPanelRoleOverrides] = useState({});
  const [panelRoleModels, setPanelRoleModels] = useState({});
  const [workerRoutingMode, setWorkerRoutingMode] = useState('simple');
  const [simpleRoutingBackends, setSimpleRoutingBackends] = useState({});
  const [customModelSelected, setCustomModelSelected] = useState({});
  const [configuringRoles, setConfiguringRoles] = useState({});
  const [backendsError, setBackendsError] = useState(null);
  const [backendsSuccess, setBackendsSuccess] = useState(null);
  const [savingBackends, setSavingBackends] = useState(false);
  const [discoveredAgents, setDiscoveredAgents] = useState(null);
  const [loadingDiscoveredAgents, setLoadingDiscoveredAgents] = useState(false);
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

  // Form states for starting runs
  const [idea, setIdea] = useState('');
  const [projectName, setProjectName] = useState('');
  const [startProjectKey, setStartProjectKey] = useState('');
  const [modelProfile, setModelProfile] = useState('balanced');
  const [deliveryMode, setDeliveryMode] = useState('init_project');
  const [deliveryStandards, setDeliveryStandards] = useState('');
  const [roleModelOverrides, setRoleModelOverrides] = useState('');
  const [projectRoot, setProjectRoot] = useState('');
  const [projectConfig, setProjectConfig] = useState('');
  const [taskSync, setTaskSync] = useState('');
  const [workerBackendMode, setWorkerBackendMode] = useState('codex');
  const [deploymentBackendMode, setDeploymentBackendMode] = useState('');
  const [startMode, setStartMode] = useState('existing_project'); // existing_project or new_project
  const [dryRun, setDryRun] = useState(false);
  const isNewProjectMode = startMode === 'new_project' || startMode === 'new';

  const [initGit, setInitGit] = useState(true);
  const [createRemote, setCreateRemote] = useState(false);
  const [remotePrivate, setRemotePrivate] = useState(true);
  const [gitWarnings, setGitWarnings] = useState([]);
  const [gitRemoteCreated, setGitRemoteCreated] = useState(false);
  const [showGitNotice, setShowGitNotice] = useState(false);

  // Dispatch track form states
  const [dispatchName, setDispatchName] = useState('');
  const [dispatchRole, setDispatchRole] = useState('backend');
  const [dispatchCustomRole, setDispatchCustomRole] = useState('');
  const [dispatchAdapter, setDispatchAdapter] = useState('codex');
  const [dispatchCustomAdapter, setDispatchCustomAdapter] = useState('');
  const [dispatchTask, setDispatchTask] = useState('');
  const [dispatchConstraints, setDispatchConstraints] = useState('');
  const [dispatchVerify, setDispatchVerify] = useState('');
  const [dispatchLoading, setDispatchLoading] = useState(false);
  const [dispatchError, setDispatchError] = useState(null);
  const [dispatchSuccess, setDispatchSuccess] = useState(null);

  // Adapter states
  const [planeApiHost, setPlaneApiHost] = useState('https://api.plane.so/');
  const [planeWorkspaceSlug, setPlaneWorkspaceSlug] = useState('');
  const [planeProjectId, setPlaneProjectId] = useState('');
  const [planeApiKeyEnv, setPlaneApiKeyEnv] = useState('PLANE_API_KEY');
  const [planeMcpServer, setPlaneMcpServer] = useState('plane');

  // Deployment states
  const [stagingSshTarget, setStagingSshTarget] = useState('');
  const [stagingRemoteDir, setStagingRemoteDir] = useState('');
  const [stagingGitRef, setStagingGitRef] = useState('');
  const [stagingPublicUrl, setStagingPublicUrl] = useState('');
  const [stagingDeployCommand, setStagingDeployCommand] = useState('');
  const [stagingHealthcheckCommand, setStagingHealthcheckCommand] = useState('');
  const [stagingSmokeCommand, setStagingSmokeCommand] = useState('');
  const [deliveryContractRequired, setDeliveryContractRequired] = useState(false);
  const [deliveryContractMinEvidence, setDeliveryContractMinEvidence] = useState('infrastructure');
  const [deliveryContractFatalPatterns, setDeliveryContractFatalPatterns] = useState('');
  const [deliveryContractAcceptance, setDeliveryContractAcceptance] = useState('');

  // Form submit button loading states
  const [actionLoading, setActionLoading] = useState(false);
  const [generatingPrd, setGeneratingPrd] = useState(false);
  const [startingWorkflow, setStartingWorkflow] = useState(false);
  const [smokeRunLoading, setSmokeRunLoading] = useState(false);
  const [approvingAction, setApprovingAction] = useState(null);

  // Changes requested note states
  const [changesRequestedNote, setChangesRequestedNote] = useState('');
  const [showChangesRequestedNoteKey, setShowChangesRequestedNoteKey] = useState(null);

  // Approval review artifact editor state
  const [editingArtifact, setEditingArtifact] = useState(null); // { key, content }
  const [editorContent, setEditorContent] = useState('');
  const [refineLoading, setRefineLoading] = useState(false);
  const [proposedRevision, setProposedRevision] = useState(null); // { proposed_path, content, diff_summary }
  const [openQuestions, setOpenQuestions] = useState([]);
  const [openQuestionsAnswers, setOpenQuestionsAnswers] = useState({});
  const [fetchingOpenQuestions, setFetchingOpenQuestions] = useState(false);
  const [answeringOpenQuestions, setAnsweringOpenQuestions] = useState(null); // string (question text) or null
  const [gateDebate, setGateDebate] = useState(EMPTY_GATE_DEBATE);
  const [debateRun, setDebateRun] = useState({ key: '', loading: false, error: '' });
  const [messageFeed, setMessageFeed] = useState(EMPTY_MESSAGE_FEED);

  // Memory visualization states
  const [memoryData, setMemoryData] = useState(null);
  const [loadingMemory, setLoadingMemory] = useState(false);
  const [memoryError, setMemoryError] = useState(null);
  const [memoryEventDetail, setMemoryEventDetail] = useState(null);
  const [memoryEventBrowsers, setMemoryEventBrowsers] = useState({});
  const memoryBrowseTimers = useRef({});
  const memoryBrowseRequestSeq = useRef({});

  // Cross-project memory search states
  const [crossRole, setCrossRole] = useState('implementation');
  const [crossQuery, setCrossQuery] = useState('');
  const [crossScope, setCrossScope] = useState('all');
  const [crossLimit, setCrossLimit] = useState(5);
  const [crossLoading, setCrossLoading] = useState(false);
  const [crossError, setCrossError] = useState(null);
  const [crossResults, setCrossResults] = useState(null);
  const [allProjectKeys, setAllProjectKeys] = useState([]);
  const crossSearchRequestSeq = useRef(0);

  // Workflow Graph states
  const [graphData, setGraphData] = useState(null);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [graphError, setGraphError] = useState(null);
  const graphRequestSeq = useRef(0);

  // Dev Loop states
  const [devLoopData, setDevLoopData] = useState(null);
  const [loadingDevLoop, setLoadingDevLoop] = useState(false);
  const [devLoopError, setDevLoopError] = useState(null);
  const devLoopRequestSeq = useRef(0);
  const [copiedText, setCopiedText] = useState(null);
  const [copyFailedText, setCopyFailedText] = useState(null);

  const { directoryPickerModal, openDirectoryPicker } = useDirectoryPicker(setProjectRoot);

  const markLandCommandCopied = (command) => {
    setCopyFailedText(prev => prev === command ? null : prev);
    setCopiedText(command);
    setTimeout(() => {
      setCopiedText(prev => prev === command ? null : prev);
    }, 2000);
  };

  const markLandCommandCopyFailed = (command) => {
    setCopiedText(prev => prev === command ? null : prev);
    setCopyFailedText(command);
    setTimeout(() => {
      setCopyFailedText(prev => prev === command ? null : prev);
    }, 2000);
  };

  const copyLandCommand = (command) => {
    if (!command) return;

    const clipboard = navigator.clipboard;
    if (clipboard?.writeText) {
      clipboard.writeText(command)
        .then(() => markLandCommandCopied(command))
        .catch(() => markLandCommandCopyFailed(command));
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = command;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();

    try {
      const copied = typeof document.execCommand === 'function' && document.execCommand('copy');
      if (copied) {
        markLandCommandCopied(command);
      } else {
        markLandCommandCopyFailed(command);
      }
    } catch {
      markLandCommandCopyFailed(command);
    } finally {
      document.body.removeChild(textarea);
    }
  };

  const gateDebateReviews = data?.approval_reviews || [];
  const actionableGateReview = gateDebateReviews.find(r => r.is_actionable) || gateDebateReviews[0];
  const gateDebateRunId = data?.state?.run_id || '';
  const gateDebateProjectKey = projectKey || data?.state?.project_key || '';
  const messageFeedProjectKey = projectKey || data?.state?.project_key || '';
  const messageFeedRunId = data?.state?.run_id || '';
  const targetMessageFeedKey = activeTab === 'messages' && messageFeedProjectKey && messageFeedRunId
    ? `${messageFeedProjectKey}:${messageFeedRunId}`
    : '';
  const targetGateDebateKey = activeTab === 'approvals' && actionableGateReview?.is_actionable && gateDebateProjectKey && gateDebateRunId
    ? `${gateDebateProjectKey}:${gateDebateRunId}:${actionableGateReview.approval}`
    : '';
  const visibleGateDebate = gateDebate.key === targetGateDebateKey ? gateDebate : EMPTY_GATE_DEBATE;
  // Reset Git toggles/notices when the new project modal is opened
  useEffect(() => {
    if (newProjectModalOpen) {
      setInitGit(true);
      setCreateRemote(false);
      setRemotePrivate(true);
      setGitWarnings([]);
      setGitRemoteCreated(false);
      setShowGitNotice(false);
    }
  }, [newProjectModalOpen]);

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
        setProjectRoot(repos.root?.path || repos.docs?.path || repos.backend?.path || repos.frontend?.path || Object.values(repos)[0]?.path || '');

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
          setPanelGlobalMode(sc.worker_backend?.mode || 'codex');
          setPanelRoleOverrides(sc.project_report?.role_worker_backends || {});

          const initialModels = sc.role_model_overrides || {};
          setPanelRoleModels(initialModels);
          setBackendsError(null);
          setBackendsSuccess(null);

          const customSelected = {};
          if (discoveredAgents) {
            const agentMap = {};
            discoveredAgents.forEach(a => {
              if (a && a.backend) {
                agentMap[a.backend] = a;
                if (a.aliases) {
                  a.aliases.forEach(alias => {
                    agentMap[alias] = a;
                  });
                }
              }
            });
            const roles = sc.worker_roles || DEFAULT_WORKER_ROLES;
            roles.forEach(role => {
              const currentModel = initialModels[role];
              const currentBackend = sc.project_report?.role_worker_backends?.[role] || sc.worker_backend?.mode || 'codex';
              const fallbackAgent = FALLBACK_AGENT_MODES[currentBackend];
              const agent = currentBackend ? agentMap[currentBackend] || (fallbackAgent ? { backend: currentBackend, ...fallbackAgent } : null) : null;
              if (agent && currentModel) {
                const models = agent.models || FALLBACK_AGENT_MODES[agent.backend]?.models || ["auto"];
                if (!models.includes(currentModel)) {
                  customSelected[role] = true;
                }
              }
            });
          }
          setCustomModelSelected(customSelected);
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

  const fetchGateDebate = async ({ key, runId, gate }) => {
    const debateKey = `${key}:${runId}:${gate}`;
    setGateDebate({ key: debateKey, messages: [], loading: true, error: '' });
    try {
      const params = new URLSearchParams({ project: key, run: runId, gate });
      const response = await fetch(`/api/messages?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to fetch gate debate');
      }
      setGateDebate({ key: debateKey, messages: payload.messages || [], loading: false, error: '' });
    } catch (err) {
      setGateDebate({ key: debateKey, messages: [], loading: false, error: err.message });
    }
  };

  const fetchMessageFeed = async ({ key, runId }) => {
    const feedKey = `${key}:${runId}`;
    setMessageFeed({ key: feedKey, messages: [], loading: true, error: '' });
    try {
      const params = new URLSearchParams({ project: key, run: runId });
      const response = await fetch(`/api/messages?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to fetch message feed');
      }
      setMessageFeed({ key: feedKey, messages: payload.messages || [], loading: false, error: '' });
    } catch (err) {
      setMessageFeed({ key: feedKey, messages: [], loading: false, error: err.message });
    }
  };

  const handleRunDebate = async (approvalKey) => {
    const key = projectKey || data?.state?.project_key || '';
    const runId = data?.state?.run_id || '';
    const debateKey = `${key}:${runId}:${approvalKey}`;

    if (!key || !runId || !approvalKey) {
      setDebateRun({ key: debateKey, loading: false, error: 'Missing project, run, or gate for debate.' });
      return;
    }

    setDebateRun({ key: debateKey, loading: true, error: '' });
    try {
      const response = await fetch('/api/debate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: key,
          run: runId,
          gate: approvalKey,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to run debate');
      }
      await fetchGateDebate({ key, runId, gate: approvalKey });
      setDebateRun((current) => (
        current.key === debateKey ? { key: debateKey, loading: false, error: '' } : current
      ));
    } catch (err) {
      setDebateRun((current) => (
        current.key === debateKey ? { key: debateKey, loading: false, error: err.message } : current
      ));
    }
  };

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
    fetchState(projectKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchState is intentionally omitted so loading fetches only when the project key changes.
  }, [projectKey]);

  const fetchMemory = async (key) => {
    setLoadingMemory(true);
    setMemoryError(null);
    try {
      const response = await fetch(`/api/memory?project=${encodeURIComponent(key)}`);
      if (!response.ok) {
        const errPayload = await response.json().catch(() => ({}));
        throw new Error(errPayload.error || `Failed to fetch memory (HTTP ${response.status})`);
      }
      const payload = await response.json();
      setMemoryData(payload);
    } catch (err) {
      console.error('Error fetching memory:', err);
      setMemoryError(err.message);
    } finally {
      setLoadingMemory(false);
    }
  };

  const handleOpenMemoryEvent = async (eventId) => {
    if (!eventId || !projectKey) return;

    setMemoryEventDetail({
      id: eventId,
      loading: true,
      error: null,
      data: null,
    });

    try {
      const response = await fetch(`/api/memory/event?project=${encodeURIComponent(projectKey)}&id=${encodeURIComponent(eventId)}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch memory event (HTTP ${response.status})`);
      }

      setMemoryEventDetail((current) => (
        current?.id === eventId
          ? { id: eventId, loading: false, error: null, data: payload }
          : current
      ));
    } catch (err) {
      console.error('Error fetching memory event:', err);
      setMemoryEventDetail((current) => (
        current?.id === eventId
          ? { id: eventId, loading: false, error: "couldn't load this event", data: null }
          : current
      ));
    }
  };

  const fetchMemoryEvents = async (roleKey, searchQuery = '') => {
    if (!roleKey || !projectKey) return;
    const requestSeq = (memoryBrowseRequestSeq.current[roleKey] || 0) + 1;
    memoryBrowseRequestSeq.current[roleKey] = requestSeq;

    setMemoryEventBrowsers((current) => ({
      ...current,
      [roleKey]: {
        ...(current[roleKey] || {}),
        open: true,
        loading: true,
        error: null,
      },
    }));

    try {
      const params = new URLSearchParams({
        project: projectKey,
        role: roleKey,
        limit: '50',
      });
      if (searchQuery.trim()) {
        params.set('q', searchQuery.trim());
      }
      const response = await fetch(`/api/memory/events?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch memory events (HTTP ${response.status})`);
      }
      setMemoryEventBrowsers((current) => {
        if (memoryBrowseRequestSeq.current[roleKey] !== requestSeq) return current;
        return {
          ...current,
          [roleKey]: {
            ...(current[roleKey] || {}),
            open: true,
            loading: false,
            error: null,
            events: Array.isArray(payload.events) ? payload.events : [],
          },
        };
      });
    } catch (err) {
      console.error('Error fetching memory events:', err);
      setMemoryEventBrowsers((current) => {
        if (memoryBrowseRequestSeq.current[roleKey] !== requestSeq) return current;
        return {
          ...current,
          [roleKey]: {
            ...(current[roleKey] || {}),
            open: true,
            loading: false,
            error: err.message,
            events: [],
          },
        };
      });
    }
  };

  const handleToggleMemoryEvents = (roleKey) => {
    const current = memoryEventBrowsers[roleKey] || {};
    if (current.open) {
      setMemoryEventBrowsers((browsers) => ({
        ...browsers,
        [roleKey]: {
          ...current,
          open: false,
        },
      }));
      return;
    }
    const searchQuery = current.query || '';
    setMemoryEventBrowsers((browsers) => ({
      ...browsers,
      [roleKey]: {
        ...current,
        open: true,
        query: searchQuery,
        events: current.events || [],
      },
    }));
    fetchMemoryEvents(roleKey, searchQuery);
  };

  const handleMemoryEventSearchChange = (roleKey, value) => {
    setMemoryEventBrowsers((current) => ({
      ...current,
      [roleKey]: {
        ...(current[roleKey] || {}),
        open: true,
        query: value,
      },
    }));
    if (memoryBrowseTimers.current[roleKey]) {
      clearTimeout(memoryBrowseTimers.current[roleKey]);
    }
    memoryBrowseTimers.current[roleKey] = setTimeout(() => {
      fetchMemoryEvents(roleKey, value);
    }, 300);
  };

  const fetchWorkflowGraph = async (key) => {
    const seq = graphRequestSeq.current + 1;
    graphRequestSeq.current = seq;

    setLoadingGraph(true);
    setGraphError(null);
    try {
      const response = await fetch(`/api/workflow/graph?project=${encodeURIComponent(key)}`);
      const payload = await response.json().catch(() => ({}));

      if (graphRequestSeq.current !== seq) return;

      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch workflow graph (HTTP ${response.status})`);
      }
      setGraphData(payload);
    } catch (err) {
      if (graphRequestSeq.current === seq) {
        console.error('Error fetching workflow graph:', err);
        setGraphError(err.message);
      }
    } finally {
      if (graphRequestSeq.current === seq) {
        setLoadingGraph(false);
      }
    }
  };

  // Lazy fetch workflow graph data when the Workflow Graph tab is opened
  useEffect(() => {
    if (activeTab === 'workflow' && projectKey) {
      fetchWorkflowGraph(projectKey);
    }
  }, [activeTab, projectKey]);

  // Lazy fetch memory data when the Memory tab is opened
  useEffect(() => {
    if ((activeTab === 'memory' || (activeTab === 'agents_memory' && agentsMemorySubTab === 'memory')) && projectKey) {
      fetchMemory(projectKey);
    }
  }, [activeTab, agentsMemorySubTab, projectKey]);

  const fetchDevLoop = async (key) => {
    const seq = devLoopRequestSeq.current + 1;
    devLoopRequestSeq.current = seq;

    setLoadingDevLoop(true);
    setDevLoopError(null);
    try {
      const url = key ? `/api/devloop?project=${encodeURIComponent(key)}` : '/api/devloop';
      const response = await fetch(url);
      const payload = await response.json().catch(() => ({}));

      if (devLoopRequestSeq.current !== seq) return;

      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch dev loop (HTTP ${response.status})`);
      }
      setDevLoopData(payload);
    } catch (err) {
      if (devLoopRequestSeq.current === seq) {
        console.error('Error fetching dev loop:', err);
        setDevLoopError(err.message);
      }
    } finally {
      if (devLoopRequestSeq.current === seq) {
        setLoadingDevLoop(false);
      }
    }
  };

  // Lazy fetch devloop data when the Dev Loop tab is opened
  useEffect(() => {
    if (activeTab === 'devloop') {
      fetchDevLoop(projectKey);
    }
  }, [activeTab, projectKey]);

  useEffect(() => {
    if (activeTab === 'memory' || (activeTab === 'agents_memory' && agentsMemorySubTab === 'memory')) {
      fetch('/api/state')
        .then((res) => {
          if (!res.ok) throw new Error('Failed to load project keys');
          return res.json();
        })
        .then((payload) => {
          if (payload && Array.isArray(payload.project_items)) {
            const keys = payload.project_items.map((item) => item.project_key).filter(Boolean);
            setAllProjectKeys(keys);
          }
        })
        .catch((err) => {
          console.error('Error fetching all project keys:', err);
        });
    }
  }, [activeTab, agentsMemorySubTab]);

  const handleCrossSearch = async (e) => {
    if (e) e.preventDefault();
    if (!crossRole.trim() || !crossQuery.trim()) {
      setCrossError('Role and query are required.');
      return;
    }

    const seq = crossSearchRequestSeq.current + 1;
    crossSearchRequestSeq.current = seq;

    setCrossLoading(true);
    setCrossError(null);
    setCrossResults(null);

    try {
      let scopeVal = crossScope.trim();
      if (!scopeVal || scopeVal.toLowerCase() === 'all') {
        if (allProjectKeys.length > 0) {
          scopeVal = allProjectKeys.join(',');
        } else if (projectKey) {
          scopeVal = projectKey;
        } else {
          scopeVal = 'all';
        }
      }

      const params = new URLSearchParams({
        role: crossRole.trim(),
        q: crossQuery.trim(),
        scope: scopeVal,
        k: String(crossLimit),
      });

      const response = await fetch(`/api/memory/cross?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));

      if (crossSearchRequestSeq.current !== seq) return;

      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch cross-project memory (HTTP ${response.status})`);
      }

      const results = Array.isArray(payload.results) ? payload.results : [];
      setCrossResults(results);
    } catch (err) {
      if (crossSearchRequestSeq.current === seq) {
        console.error('Error in cross-project memory search:', err);
        setCrossError(err.message);
      }
    } finally {
      if (crossSearchRequestSeq.current === seq) {
        setCrossLoading(false);
      }
    }
  };

  useEffect(() => () => {
    Object.values(memoryBrowseTimers.current).forEach((timer) => clearTimeout(timer));
  }, []);

  useEffect(() => {
    if (!targetMessageFeedKey || messageFeed.key === targetMessageFeedKey) return;
    Promise.resolve().then(() => {
      fetchMessageFeed({ key: messageFeedProjectKey, runId: messageFeedRunId });
    });
  }, [targetMessageFeedKey, messageFeed.key, messageFeedProjectKey, messageFeedRunId]);

  useEffect(() => {
    if (!targetGateDebateKey || gateDebate.key === targetGateDebateKey) return;
    Promise.resolve().then(() => {
      fetchGateDebate({ key: gateDebateProjectKey, runId: gateDebateRunId, gate: actionableGateReview.approval });
    });
  }, [targetGateDebateKey, gateDebate.key, gateDebateProjectKey, gateDebateRunId, actionableGateReview?.approval]);

  const getResolvingDebateRef = (approvalKey) => {
    const runId = data?.state?.run_id || '';
    if (!runId || !visibleGateDebate.messages.length || !visibleGateDebate.key.endsWith(`:${approvalKey}`)) return '';
    const resolving = [...visibleGateDebate.messages].reverse().find((item) => ['assert', 'propose'].includes(item.kind))
      || visibleGateDebate.messages[visibleGateDebate.messages.length - 1];
    return resolving?.id ? `messages/${runId}.jsonl#${resolving.id}` : '';
  };

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
      const parsedOverrides = Object.fromEntries(
        roleModelOverrides
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => line.split('=', 2))
          .filter((parts) => parts.length === 2 && parts[0] && parts[1])
      );
      const customDeliveryPayload = deliveryMode === 'custom'
        ? { delivery_standards: deliveryStandards }
        : {};

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
      const overrides = roleModelOverrides
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      const customDeliveryPayload = deliveryMode === 'custom'
        ? { delivery_standards: deliveryStandards }
        : {};

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

      // Clear form on success
      setDispatchName('');
      setDispatchTask('');
      setDispatchConstraints('');
      setDispatchVerify('');
      setDispatchCustomRole('');
      setDispatchCustomAdapter('');

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

  const handleEditArtifact = async (artifactOrReview) => {
    const artifactName = typeof artifactOrReview === 'string'
      ? artifactOrReview
      : (artifactOrReview?.artifact || '');
    if (!artifactName) return;

    setActionLoading(true);
    try {
      const response = await fetch(`/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(artifactName)}`);
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || '暂无该产物');
      }
      setEditingArtifact(artifactName);
      setEditorContent(res.content || '');
      setActiveTab('approvals');
    } catch (err) {
      const errMsg = err.message.includes('File not found') || err.message.includes('No such file') || err.message.includes('does not exist')
        ? '暂无该产物'
        : err.message;
      showToast(errMsg, true);
    } finally {
      setActionLoading(false);
    }
  };

  const handleOpenQuestionsFromApproval = async (review) => {
    await handleEditArtifact(review);
    if (review?.artifact) {
      fetchOpenQuestions(projectKey, review.artifact);
    }
  };

  const handleSaveArtifact = async () => {
    if (!editingArtifact) return;
    setActionLoading(true);
    try {
      const response = await fetch('/api/artifact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
          content: editorContent,
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to save artifact');
      showToast(`Artifact ${editingArtifact.toUpperCase()} saved successfully`);
      setEditingArtifact(null);
      fetchState(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
    }
  };

  const fetchOpenQuestions = async (projKey, artKey) => {
    if (!projKey || !artKey) return;
    setFetchingOpenQuestions(true);
    try {
      const response = await fetch(`/api/open-questions?project=${encodeURIComponent(projKey)}&artifact=${encodeURIComponent(artKey)}`);
      const res = await response.json();
      if (response.ok) {
        setOpenQuestions(res.questions || []);
        setOpenQuestionsAnswers(prev => {
          const next = {};
          (res.questions || []).forEach(q => {
            next[q] = prev[q] !== undefined ? prev[q] : '';
          });
          return next;
        });
      } else {
        console.error(res.error || 'Failed to fetch open questions');
      }
    } catch (err) {
      console.error(err);
    } finally {
      setFetchingOpenQuestions(false);
    }
  };

  const handleApplyAnswers = async (singleQuestion) => {
    if (!projectKey || !editingArtifact) return;
    
    let answersToSend = [];
    if (singleQuestion) {
      const ans = openQuestionsAnswers[singleQuestion];
      if (ans && ans.trim()) {
        answersToSend = [{ question: singleQuestion, answer: ans }];
      }
    } else {
      answersToSend = Object.entries(openQuestionsAnswers)
        .filter(([, ans]) => ans && ans.trim())
        .map(([q, ans]) => ({ question: q, answer: ans }));
    }

    if (answersToSend.length === 0) return;

    setAnsweringOpenQuestions(singleQuestion || true);
    try {
      const response = await fetch('/api/answer-open-questions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
          answers: answersToSend,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to apply answers with AI');
      }

      // Fetch the proposed content
      const proposedPath = res.proposed_path;
      const contentResponse = await fetch(`/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(proposedPath)}`);
      const contentRes = await contentResponse.json();
      if (!contentResponse.ok) {
        throw new Error(contentRes.error || 'Failed to fetch proposed artifact content');
      }

      setProposedRevision({
        proposed_path: proposedPath,
        content: contentRes.content || '',
        diff_summary: res.diff_summary || { added_lines: 0, removed_lines: 0 }
      });
      showToast('Answers applied with AI. Please review the proposed revision.');
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setAnsweringOpenQuestions(null);
    }
  };

  useEffect(() => {
    if (!editingArtifact || !projectKey) {
      setOpenQuestions([]);
      setOpenQuestionsAnswers({});
      return;
    }
    setOpenQuestionsAnswers({});
    fetchOpenQuestions(projectKey, editingArtifact);
  }, [editingArtifact, projectKey]);

  const handleRefineArtifact = async () => {
    if (!editingArtifact || !editorContent) return;
    setRefineLoading(true);
    try {
      const response = await fetch('/api/refine-artifact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
          content: editorContent,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to refine artifact');
      }

      // Fetch the proposed content
      const proposedPath = res.proposed_path;
      const contentResponse = await fetch(`/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(proposedPath)}`);
      const contentRes = await contentResponse.json();
      if (!contentResponse.ok) {
        throw new Error(contentRes.error || 'Failed to fetch proposed artifact content');
      }

      setProposedRevision({
        proposed_path: proposedPath,
        content: contentRes.content || '',
        diff_summary: res.diff_summary || { added_lines: 0, removed_lines: 0 }
      });
      showToast('Refinement generated. Please review the proposal.');
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setRefineLoading(false);
    }
  };

  const handleAcceptRefinement = async () => {
    if (!editingArtifact || !proposedRevision) return;
    setRefineLoading(true);
    try {
      const response = await fetch('/api/accept-refinement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to accept refinement');
      }

      showToast(`Refinement accepted. Saved to ${editingArtifact.toUpperCase()}.`);
      
      // Load the now-current artifact into the editor
      setEditorContent(proposedRevision.content);
      setProposedRevision(null);

      // fetchState to update UI
      await fetchState(projectKey);

      // Re-fetch open questions
      await fetchOpenQuestions(projectKey, editingArtifact);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setRefineLoading(false);
    }
  };

  const handleDiscardRefinement = async () => {
    if (!editingArtifact || !proposedRevision) return;
    setRefineLoading(true);
    try {
      const response = await fetch('/api/discard-refinement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to discard refinement');
      }

      showToast('Refinement discarded. Keeping your original edits.');
      setProposedRevision(null);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setRefineLoading(false);
    }
  };

  const handleAgentChange = (role, newAgent) => {
    setPanelRoleOverrides(prev => ({
      ...prev,
      [role]: newAgent
    }));

    const effectiveAgent = newAgent || panelGlobalMode;

    if (MODEL_UNAVAILABLE_BACKENDS.includes(effectiveAgent)) {
      setPanelRoleModels(prev => ({
        ...prev,
        [role]: ''
      }));
      setCustomModelSelected(prev => ({
        ...prev,
        [role]: false
      }));
    } else {
      const agent = discoveredAgentByBackend[effectiveAgent];
      const defaultModel = agent?.model_default || FALLBACK_AGENT_MODES[effectiveAgent]?.model_default || 'auto';
      setPanelRoleModels(prev => ({
        ...prev,
        [role]: defaultModel
      }));
      setCustomModelSelected(prev => ({
        ...prev,
        [role]: false
      }));
    }
  };

  const handleApplySimpleRouting = () => {
    const nextOverrides = { ...panelRoleOverrides };
    WORKER_ROUTING_BUCKETS.forEach((bucket) => {
      const backend = simpleRoutingBackends[bucket.key] || '';
      bucket.roles.forEach((role) => {
        if (!workerRoles.includes(role)) return;
        if (backend) {
          nextOverrides[role] = backend;
        } else {
          delete nextOverrides[role];
        }
      });
    });
    setPanelRoleOverrides(nextOverrides);
    setWorkerRoutingMode('advanced');
    setBackendsSuccess('Simple routing applied. Review Advanced roles, then save.');
  };

  const handleModelDropdownChange = (role, val) => {
    if (val === '__custom__') {
      setCustomModelSelected(prev => ({
        ...prev,
        [role]: true
      }));
      setPanelRoleModels(prev => ({
        ...prev,
        [role]: ''
      }));
    } else {
      setCustomModelSelected(prev => ({
        ...prev,
        [role]: false
      }));
      setPanelRoleModels(prev => ({
        ...prev,
        [role]: val
      }));
    }
  };

  const handleCustomModelChange = (role, val) => {
    setPanelRoleModels(prev => ({
      ...prev,
      [role]: val
    }));
  };

  useEffect(() => {
    if (discoveredAgents && data?.start_context?.role_model_overrides) {
      const roleModels = data.start_context.role_model_overrides;
      const customSelected = {};
      const agentMap = {};
      discoveredAgents.forEach(a => {
        if (a && a.backend) {
          agentMap[a.backend] = a;
          if (a.aliases) {
            a.aliases.forEach(alias => {
              agentMap[alias] = a;
            });
          }
        }
      });
      const roles = data.start_context.worker_roles || DEFAULT_WORKER_ROLES;
      roles.forEach(role => {
        const currentModel = roleModels[role];
        const currentBackend = panelRoleOverrides[role] || data.start_context.project_report?.role_worker_backends?.[role] || panelGlobalMode;
        const fallbackAgent = FALLBACK_AGENT_MODES[currentBackend];
        const agent = currentBackend ? agentMap[currentBackend] || (fallbackAgent ? { backend: currentBackend, ...fallbackAgent } : null) : null;
        if (agent && currentModel) {
          const models = agent.models || FALLBACK_AGENT_MODES[agent.backend]?.models || ["auto"];
          if (!models.includes(currentModel)) {
            customSelected[role] = true;
          }
        }
      });
      setCustomModelSelected(prev => ({ ...customSelected, ...prev }));
    }
  }, [discoveredAgents, data, panelRoleOverrides, panelGlobalMode]);

  const handleSaveWorkerBackends = async () => {
    setSavingBackends(true);
    setBackendsError(null);
    setBackendsSuccess(null);
    try {
      // Build workers object containing ONLY explicit overrides for managed roles (exclude empty strings or "default")
      const workers = {};
      const role_models = {};
      const managedRoles = data?.start_context?.worker_roles || DEFAULT_WORKER_ROLES;
      managedRoles.forEach((role) => {
        const backend = panelRoleOverrides[role];
        if (backend && backend !== 'default' && backend !== '') {
          workers[role] = backend;
        }

        const effectiveBackend = backend && backend !== 'default' && backend !== '' ? backend : panelGlobalMode;
        if (!MODEL_UNAVAILABLE_BACKENDS.includes(effectiveBackend)) {
          const agent = discoveredAgentByBackend[effectiveBackend];
          const defaultModel = agent?.model_default || FALLBACK_AGENT_MODES[effectiveBackend]?.model_default || 'auto';
          const modelVal = panelRoleModels[role];
          const trimmedModel = modelVal?.trim() || '';
          if (trimmedModel && trimmedModel !== defaultModel) {
            role_models[role] = trimmedModel;
          }
        }
      });

      const response = await fetch('/api/project-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project: projectKey,
          worker_backend_mode: panelGlobalMode,
          workers: workers,
          role_models: role_models,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to save worker backends');
      }
      setBackendsSuccess('Configurations saved successfully');
      showToast('Worker backends updated successfully');
      await fetchState(projectKey, true); // Fetch state with full screen loader
    } catch (err) {
      setBackendsError(err.message);
      showToast(err.message, true);
    } finally {
      setSavingBackends(false);
    }
  };

  const fetchDiscoveredAgents = async () => {
    setLoadingDiscoveredAgents(true);
    try {
      const response = await fetch('/api/agents');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Failed to fetch agents');
      setDiscoveredAgents(Array.isArray(payload?.agents) ? payload.agents : []);
    } catch {
      setDiscoveredAgents([]);
    } finally {
      setLoadingDiscoveredAgents(false);
    }
  };

  useEffect(() => {
    if ((activeTab === 'workers' || (activeTab === 'agents_memory' && agentsMemorySubTab === 'workers')) && discoveredAgents === null && !loadingDiscoveredAgents) {
      const timer = window.setTimeout(() => {
        fetchDiscoveredAgents();
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [activeTab, agentsMemorySubTab, discoveredAgents, loadingDiscoveredAgents]);

  // Returns first two sentences of the idea prompt for default display
  const getMissionSummary = (text) => {
    if (!text) return '';
    const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
    if (sentences.length <= 2) return text;
    return sentences.slice(0, 2).join(' ');
  };

  const newProjectModal = newProjectModalOpen && (
    <NewProjectModal
      actionLoading={actionLoading}
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
      onClose={() => setNewProjectModalOpen(false)}
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
        setNewProjectModalOpen={setNewProjectModalOpen}
        setStartMode={setStartMode}
        setTheme={setTheme}
        t={t}
        theme={theme}
        updateUrl={updateUrl}
      />
    );
  }
  // Dashboard Detailed View for a single project
  const state = data?.state;
  const startContext = data?.start_context || {};
  const backendOptions = startContext.worker_backend_options || DEFAULT_BACKEND_OPTIONS;
  const safeDiscoveredAgents = (discoveredAgents || []).reduce((acc, agent) => {
    const backend = canonicalBackendForOption(String(agent?.backend || '').trim());
    if (!backend || acc.seen.has(backend)) return acc;
    acc.seen.add(backend);
    acc.items.push({ ...agent, backend });
    return acc;
  }, { seen: new Set(), items: [] }).items;
  const discoveredBackendOptions = Array.from(new Set(
    safeDiscoveredAgents
      .map((agent) => agent?.backend)
      .filter(Boolean)
  ));
  const roleBackendOptions = Array.from(
    [...backendOptions, ...discoveredBackendOptions]
      .reduce((acc, option) => {
        const canonical = canonicalBackendForOption(String(option || '').trim());
        if (canonical) acc.add(canonical);
        return acc;
      }, new Set())
  );
  const workerRoles = startContext.worker_roles || DEFAULT_WORKER_ROLES;
  const discoveredAgentByBackend = {};
  safeDiscoveredAgents.forEach((agent) => {
    if (agent?.backend) {
      discoveredAgentByBackend[agent.backend] = agent;
    }
    if (Array.isArray(agent?.aliases)) {
      agent.aliases.forEach((alias) => {
        if (alias) {
          discoveredAgentByBackend[alias] = agent;
          discoveredAgentByBackend[canonicalBackendForOption(alias)] = agent;
        }
      });
    }
  });
  const getBackendOptionLabel = (backend) => {
    const baseLabel = backend === 'codex'
      ? 'Codex CLI'
      : backend === 'anthropic' || backend === 'claude'
        ? 'Claude CLI'
        : backend;
    const discovered = discoveredAgentByBackend[backend];
    if (!discovered) return baseLabel;
    return `${baseLabel} ${discovered.ready ? '✓ ready' : '⚠ not-ready'}`;
  };
  const usedBackends = Array.from(new Set([
    panelGlobalMode,
    ...workerRoles.map(role => panelRoleOverrides[role]).filter(b => b && b !== 'default' && b !== '')
  ])).filter(Boolean);

  const activeRun = data?.active_run || false;
  const roleObservations = data?.role_observations || [];
  const approvalReviews = data?.approval_reviews || [];
  const actionItems = data?.action_items || [];
  const smokeRunItems = data?.smoke_run_items || [];
  const agileBoard = data?.agile_board || { columns: {}, counts: {} };
  const workerJobs = data?.worker_jobs || [];

  const headerStatus = state?.next_node || state?.current_node || 'no_active_run';
  const headBadge = getBadgeStyle(headerStatus);

  // Approvals metrics
  const approvedCount = approvalReviews.filter(r => r.decision === 'approved').length;
  const actionableCount = approvalReviews.filter(r => r.is_actionable).length;
  const openQuestionsGateCount = openQuestions.length;
  const totalApprovalsCount = approvalReviews.length || 6;
  const approvalProgressPercent = Math.min(100, Math.round((approvedCount / totalApprovalsCount) * 100));
  const approvalBannerQuestions = openQuestionsGateCount > 0
    ? t('approvalBanner.questions', {
      count: openQuestionsGateCount,
      plural: openQuestionsGateCount === 1 ? '' : 's',
    })
    : '';
  const navGroups = [
    {
      title: t('nav.groups.work'),
      items: [
        { id: 'overview', label: t('nav.tabs.overview'), icon: LayoutDashboard },
        { id: 'approvals', label: t('nav.tabs.approvals'), icon: CheckCircle2 },
        { id: 'artifacts', label: t('nav.tabs.artifacts'), icon: FileCode },
        { id: 'actions', label: t('nav.tabs.actions'), icon: Activity },
        { id: 'board', label: t('nav.tabs.board'), icon: Kanban },
      ],
    },
    {
      title: t('nav.groups.observe'),
      items: [
        { id: 'workflow', label: t('nav.tabs.workflow'), icon: GitBranch },
        { id: 'devloop', label: t('nav.tabs.devloop'), icon: GitPullRequest },
        { id: 'messages', label: t('nav.tabs.messages'), icon: MessageSquare },
      ],
    },
    {
      title: t('nav.groups.inspect'),
      items: [
        { id: 'agents_memory', label: t('nav.tabs.agentsMemory'), icon: Users },
        { id: 'smoke', label: t('nav.tabs.smoke'), icon: PlaySquare },
      ],
    },
    {
      title: t('nav.groups.system'),
      items: [
        { id: 'system', label: t('nav.tabs.system'), icon: Settings },
      ],
    },
  ];
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

      {/* Sidebar - fixed desktop 220px wide, white, right border */}
      <aside className={`w-full lg:w-[220px] shrink-0 bg-surface-raised text-text-secondary flex flex-col border-r-2 border-border-subtle lg:min-h-screen ${mobileMenuOpen ? 'block' : 'hidden lg:flex'}`}>
        <div className="p-5 border-b-2 border-border-subtle flex justify-between items-center bg-surface-sunken">
          <div>
            <span className="text-2xs uppercase tracking-widest text-accent-text font-bold">GoodTeam OS</span>
            <h2 className="text-sm font-bold tracking-tight text-text-primary mt-0.5">{state?.project_name || startContext.project_name || t('chrome.dashboard')}</h2>
          </div>
          <button
            onClick={() => updateUrl('')}
            className="text-2xs text-text-secondary hover:text-accent border-2 border-border-subtle hover:border-accent px-2.5 py-0.5 rounded-lg transition-all font-bold uppercase cursor-pointer"
          >
            {t('chrome.exit')}
          </button>
        </div>

        {/* Project Selector Indicator */}
        <div className="px-4 py-3 bg-surface-sunken border-b-2 border-border-subtle flex items-center space-x-2 text-xs font-bold text-text-secondary">
          <Folder className="h-4 w-4 text-accent shrink-0" />
          <span className="font-mono text-text-primary select-all">{projectKey}</span>
        </div>

        {/* Tab buttons - grouped by priority */}
        <nav className="flex-grow py-4 space-y-4 overflow-y-auto">
          {navGroups.map((group) => (
            <div key={group.title} className="space-y-1">
              <div className="px-4 py-1 text-2xs font-bold tracking-widest text-text-muted uppercase">
                {group.title}
              </div>
              {group.items.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                const showBadge = tab.id === 'approvals' && actionableCount > 0;
                return (
                  <button
                    key={tab.id}
                    onClick={() => {
                      if (tab.id === 'agents_memory') {
                        setAgentsMemorySubTab('roles');
                      }
                      setActiveTab(tab.id);
                      setMobileMenuOpen(false);
                    }}
                    className={`w-full h-11 flex items-center justify-between px-4 transition-all cursor-pointer ${isActive ? 'bg-accent-bg border-l-[4px] border-accent text-accent-text font-bold' : 'hover:bg-surface-sunken border-l-[4px] border-transparent font-semibold text-text-secondary'}`}
                  >
                    <div className="flex items-center space-x-3 min-w-0">
                      <Icon className={`h-4.5 w-4.5 shrink-0 ${isActive ? 'text-accent-text' : 'text-text-muted'}`} />
                      <span className="text-2xs uppercase tracking-wider truncate">{tab.label}</span>
                    </div>
                    {showBadge && (
                      <span className="bg-status-danger-bg text-status-danger-text text-2xs font-bold px-1.5 py-0.5 rounded-full border border-status-danger/20 shrink-0">
                        {actionableCount}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Sidebar Footer */}
        <div className="p-4 border-t-2 border-border-subtle flex flex-col gap-2 text-xs bg-surface-sunken font-bold text-text-muted">
          <div className="flex items-center justify-between gap-2">
            <div className="flex bg-surface-raised p-0.5 rounded-lg border border-border-subtle select-none">
              <button
                type="button"
                onClick={() => setTheme('vivid')}
                className={`px-2 py-0.5 rounded text-2xs font-bold transition-all cursor-pointer ${theme === 'vivid' ? 'bg-surface-sunken shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}
              >
                {t('theme.vivid')}
              </button>
              <button
                type="button"
                onClick={() => setTheme('minimal')}
                className={`px-2 py-0.5 rounded text-2xs font-bold transition-all cursor-pointer ${theme === 'minimal' ? 'bg-surface-sunken shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}
              >
                {t('theme.minimal')}
              </button>
            </div>
            <button
              onClick={() => setDarkMode(!darkMode)}
              className="p-1.5 rounded-lg border-2 border-border-subtle hover:bg-surface-sunken transition-colors text-text-secondary cursor-pointer"
              aria-label={t('theme.toggle')}
            >
              {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
          <LanguageSwitcher
            activeValue={languagePreference}
            onChange={setLanguagePreference}
            options={languageOptions}
          />
        </div>
      </aside>

      {/* Top Bar for Mobile viewports */}
      <header className="lg:hidden h-[60px] shrink-0 bg-surface-raised text-text-primary flex items-center justify-between px-4 border-b-2 border-border-subtle shadow-sm">
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="p-1.5 rounded-xl border-2 border-border-subtle hover:bg-surface-sunken transition-colors cursor-pointer"
        >
          {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <span className="font-bold text-sm tracking-tight text-text-primary">{state?.project_name || t('chrome.dashboard')}</span>
        {headerStatus.toLowerCase() === 'complete' ? (
          <div className="bg-status-success-bg text-status-success-text font-bold text-xs px-2.5 py-1 rounded-full border-2 border-status-success/20 flex items-center space-x-1">
            <span>✅</span>
            <span>{t('status.complete')}</span>
          </div>
        ) : (
          <div className={`px-2.5 py-0.5 rounded-full text-2xs uppercase font-bold border ${activeRun ? 'bg-status-info-bg text-status-info-text border-status-info/30' : headBadge.bg + ' ' + headBadge.text + ' ' + headBadge.border}`}>
            {getStatusLabel(headerStatus, t)}
          </div>
        )}
      </header>

      {/* Content wrapper */}
      <div className="flex-grow flex flex-col min-w-0 min-h-screen">

        {/* Top Header Bar on Desktop - 60px high, white background, bottom border */}
        <header className="hidden lg:flex h-[60px] shrink-0 border-b-2 border-border-subtle bg-surface-raised items-center justify-between px-6 shadow-sm">
          <div className="flex items-center space-x-3">
            <div
              onClick={() => updateUrl('')}
              className="w-8 h-8 rounded-lg bg-accent border-2 border-accent-text flex items-center justify-center text-text-inverse font-bold text-lg shadow-button shrink-0 cursor-pointer hover:opacity-90"
            >
              G
            </div>
            <span onClick={() => updateUrl('')} className="font-bold text-lg tracking-tight text-text-primary cursor-pointer hover:text-accent transition-colors">GoodTeam</span>
            <span className="text-border-strong font-bold text-sm">|</span>
            <span className="text-text-secondary font-bold text-xs uppercase tracking-wider">
              {state?.project_name || startContext.project_name || t('chrome.dashboard')}
            </span>
          </div>

          <div className="flex items-center space-x-4">
            <span className="text-2xs font-mono font-bold text-text-muted uppercase hidden sm:inline">{t('chrome.runId')}: <code className="bg-surface-sunken border border-border-subtle px-2 py-0.5 rounded-lg text-text-secondary select-all font-mono">{state?.run_id || t('chrome.none')}</code></span>

            {headerStatus.toLowerCase() === 'complete' ? (
              <div className="bg-status-success-bg text-status-success-text font-bold text-base px-5 py-2.5 rounded-full border-2 border-status-success/30 flex items-center space-x-1.5 shadow-sm">
                <span>✅</span>
                <span>{t('status.complete')}</span>
              </div>
            ) : (
              <div className={`inline-flex items-center space-x-1.5 px-4 py-2 rounded-full text-xs font-semibold uppercase border ${headBadge.bg} ${headBadge.text} ${headBadge.border}`}>
                {activeRun && <span className="h-1.5 w-1.5 bg-status-info rounded-full animate-pulse" />}
                <span>{getStatusLabel(headerStatus, t)}</span>
              </div>
            )}
          </div>
        </header>

        {/* Persistent Approvals Banner */}
        {actionableCount > 0 && (
          <div className="bg-status-warning-bg border-b-2 border-status-warning/30 px-6 py-3 flex items-center justify-between text-xs font-bold text-status-warning-text animate-fade-in shrink-0">
            <div className="flex items-center space-x-2">
              <span className="h-2 w-2 rounded-full bg-status-warning animate-pulse shrink-0" />
              <span>{t('approvalBanner.message', {
                count: actionableCount,
                plural: actionableCount > 1 ? 's' : '',
                questions: approvalBannerQuestions,
              })}</span>
            </div>
            <button
              onClick={() => setActiveTab('approvals')}
              className="bg-surface-raised hover:bg-status-warning-bg border-2 border-status-warning/30 shadow-button text-status-warning-text font-bold px-3 py-1 rounded-lg uppercase tracking-wider transition-all duo-button-physics text-2xs cursor-pointer"
            >
              {t('approvalBanner.review')}
            </button>
          </div>
        )}

        {/* Git Creation Notice */}
        {showGitNotice && (
          <div className="bg-surface-raised border-b-2 border-border-subtle px-6 py-4 flex flex-col md:flex-row md:items-start justify-between gap-4 animate-fade-in shrink-0">
            <div className="flex-grow space-y-1">
              <div className="flex items-center space-x-2">
                <span className="p-1 bg-status-success-bg dark:bg-emerald-950/20 text-accent-text dark:text-emerald-400 rounded-lg">
                  <CheckCircle className="h-4 w-4" />
                </span>
                <span className="font-extrabold text-sm text-text-primary">Git Repository Initialized</span>
              </div>
              {gitRemoteCreated && (
                <p className="text-xs text-text-secondary pl-8">
                  GitHub remote repository was successfully created and initial commit was pushed!
                </p>
              )}
              {gitWarnings.length > 0 && (
                <div className="space-y-1 mt-2 pl-8">
                  <p className="text-xs font-bold text-status-warning-text flex items-center gap-1">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Git Warnings:
                  </p>
                  <ul className="list-disc pl-5 text-2xs text-text-muted space-y-0.5">
                    {gitWarnings.map((warning, i) => (
                      <li key={i}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <button
              onClick={() => setShowGitNotice(false)}
              className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-secondary font-bold px-3 py-1.5 rounded-xl text-2xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer shrink-0"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Global Toast */}
        {message && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-2xl shadow-xl text-xs font-bold border-2 flex items-center space-x-2 animate-fade-in-down ${message.isError ? 'bg-status-danger-bg text-status-danger-text border-status-danger' : 'bg-status-success-bg text-accent-text border-accent'}`}>
            {message.isError ? <AlertTriangle className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            <span>{message.text}</span>
          </div>
        )}

        {memoryEventDetail && (
          <div className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/50 px-4 py-6 backdrop-blur-sm">
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="memory-event-detail-title"
              className="w-full max-w-2xl bg-white dark:bg-zinc-950 border-2 border-border-subtle dark:border-zinc-800 shadow-card dark:shadow-card rounded-2xl overflow-hidden"
            >
              <div className="px-5 py-4 bg-surface-base dark:bg-zinc-900/80 border-b-2 border-border-subtle dark:border-zinc-800 flex items-start justify-between gap-4">
                <div>
                  <p className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">Memory Event</p>
                  <h3 id="memory-event-detail-title" className="font-mono text-sm font-extrabold text-accent-text dark:text-emerald-400 mt-1 select-all">
                    {memoryEventDetail.id}
                  </h3>
                </div>
                <button
                  type="button"
                  onClick={() => setMemoryEventDetail(null)}
                  className="bg-white dark:bg-zinc-800 hover:bg-status-danger-bg border-2 border-border-subtle dark:border-zinc-800 shadow-button dark:shadow-button text-zinc-550 hover:text-status-danger p-2 rounded-2xl transition-all duo-button-physics"
                  aria-label="Close memory event detail"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="p-5 max-h-[70vh] overflow-y-auto">
                {memoryEventDetail.loading ? (
                  <div className="flex items-center justify-center p-10 text-zinc-500 font-bold">
                    <RefreshCw className="h-5 w-5 animate-spin mr-2 text-accent" />
                    <span>Loading full memory event...</span>
                  </div>
                ) : memoryEventDetail.error ? (
                  <div className="bg-status-warning-bg dark:bg-amber-950/20 border-2 border-status-warning/30 p-4 rounded-2xl text-status-warning-text dark:text-amber-300 text-xs font-bold flex items-center space-x-2">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>{memoryEventDetail.error}</span>
                  </div>
                ) : memoryEventDetail.data ? (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-2xs font-bold">
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3">
                        <span className="block uppercase tracking-wider text-text-muted mb-1">Role</span>
                        <span className="text-zinc-800 dark:text-zinc-200 uppercase">{memoryEventDetail.data.role || 'not recorded'}</span>
                      </div>
                      <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-100 dark:border-blue-900/50 rounded-xl p-3">
                        <span className="block uppercase tracking-wider text-blue-400 mb-1">Horizon</span>
                        <span className="text-blue-700 dark:text-blue-300 uppercase">{memoryEventDetail.data.horizon || 'not recorded'}</span>
                      </div>
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3">
                        <span className="block uppercase tracking-wider text-text-muted mb-1">Timestamp</span>
                        <span className="font-mono text-zinc-700 dark:text-zinc-300">{memoryEventDetail.data.timestamp || 'not recorded'}</span>
                      </div>
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3 min-w-0">
                        <span className="block uppercase tracking-wider text-text-muted mb-1">Source</span>
                        <span className="font-mono text-zinc-700 dark:text-zinc-300 break-all">{memoryEventDetail.data.source || 'not recorded'}</span>
                      </div>
                    </div>
                    {hasMemorySupersedeInfo && (
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3 text-2xs font-bold space-y-2">
                        {memoryEventDetail.data.is_superseded && (
                          <span className="inline-flex items-center bg-amber-50 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded uppercase text-2xs border border-amber-100 dark:border-amber-900/50">
                            Superseded
                          </span>
                        )}
                        {memorySupersedes && (
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="uppercase tracking-wider text-text-muted">Supersedes:</span>
                            {renderMemoryEventLink(memorySupersedes)}
                          </div>
                        )}
                        {memorySupersededBy.length > 0 && (
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="uppercase tracking-wider text-text-muted">Superseded by:</span>
                            {memorySupersededBy.map((eventId) => (
                              <span key={eventId} className="flex items-center gap-1">
                                {renderMemoryEventLink(eventId)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    <div>
                      <span className="block text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-2">Full Event Text</span>
                      <p className="text-xs font-mono leading-relaxed text-zinc-800 dark:text-zinc-200 bg-zinc-50/70 dark:bg-zinc-900/50 p-4 rounded-xl border border-dashed border-zinc-200 dark:border-zinc-800 whitespace-pre-wrap select-all">
                        {memoryEventDetail.data.text || 'No event text recorded.'}
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        )}

        {newProjectModal}
        {directoryPickerModal}

        {/* Main Panel Content Area - padded 32px (#F7F7F7 canvas background) */}
        <main className="flex-grow p-8 overflow-y-auto max-w-7xl w-full mx-auto space-y-6">
          {loading ? (
            <Spinner label={t('chrome.loading')} />
          ) : error ? (
            <div className="bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl text-status-danger-text flex items-center space-x-3 shadow-xs">
              <AlertTriangle className="h-6 w-6 shrink-0" />
              <span className="font-bold text-sm">{error}</span>
            </div>
          ) : (
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
                  copyLandCommand={copyLandCommand}
                  copiedText={copiedText}
                  copyFailedText={copyFailedText}
                  FilePath={FilePath}
                  dispatchSuccess={dispatchSuccess}
                  dispatchError={dispatchError}
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
          )}
        </main>
      </div>
    </div>
  );
}
