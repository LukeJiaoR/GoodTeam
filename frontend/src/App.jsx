import { useState, useEffect, useRef } from 'react';
import {
  LayoutDashboard,
  Users,
  FileText,
  Activity,
  Kanban,
  PlaySquare,
  Cpu,
  CheckCircle2,
  Settings,
  Sun,
  Moon,
  Menu,
  X,
  Trash2,
  Play,
  RefreshCw,
  Edit,
  Save,
  Check,
  AlertTriangle,
  Folder,
  ArrowUp,
  ChevronDown,
  Info,
  ExternalLink,
  Lock,
  Unlock,
  CheckCircle,
  XCircle,
  Clock,
  FileCode,
  Brain,
  MessageSquare,
  Search,
  GitBranch,
  GitPullRequest,
  Copy
} from 'lucide-react';
import MessageFeed from './components/MessageFeed';

const EMPTY_GATE_DEBATE = { key: '', messages: [], loading: false, error: '' };
const EMPTY_MESSAGE_FEED = { key: '', messages: [], loading: false, error: '' };
const DELIVERY_MODE_OPTIONS = [
  { value: 'mvp', label: 'MVP / validation slice' },
  { value: 'init_project', label: 'Web product starter' },
  { value: 'multi_service', label: 'Multi-service product' },
  { value: 'static_site', label: 'Static site / landing' },
  { value: 'cli_tool', label: 'CLI tool' },
  { value: 'library', label: 'Library / SDK' },
  { value: 'data_pipeline', label: 'Data pipeline / ETL' },
  { value: 'mobile_app', label: 'Mobile app' },
  { value: 'feature_increment', label: 'Feature on existing system' },
  { value: 'custom', label: 'Custom…' },
];

// Build graph layout and compute ranks for DAG columns
function computeGraphLayout(rawNodes, rawEdges) {
  const nodes = Array.isArray(rawNodes) ? rawNodes : [];
  const edges = Array.isArray(rawEdges) ? rawEdges : [];
  const allNodeNames = new Set(nodes.map(n => n.name));
  for (const edge of edges) {
    allNodeNames.add(edge.from);
    allNodeNames.add(edge.to);
  }

  const nodeMap = {};
  for (const name of allNodeNames) {
    const originalNode = nodes.find(n => n.name === name);
    nodeMap[name] = {
      name,
      gate: originalNode?.gate || '',
      runner: originalNode?.runner || '',
      kind: originalNode?.kind || 'stage',
      incoming: [],
      outgoing: [],
      rank: 0,
      index: 0
    };
  }

  for (const edge of edges) {
    if (nodeMap[edge.from] && nodeMap[edge.to]) {
      nodeMap[edge.from].outgoing.push(edge);
      nodeMap[edge.to].incoming.push(edge);
    }
  }

  // To be safe against cycles, compute ranks using Longest Path from source nodes (DAG-based),
  // with an iteration limit to break cycle back-edges.
  const maxIterations = 1000;
  let iterations = 0;
  let changed = true;

  while (changed && iterations < maxIterations) {
    changed = false;
    iterations++;
    for (const edge of edges) {
      const fromNode = nodeMap[edge.from];
      const toNode = nodeMap[edge.to];
      if (fromNode && toNode) {
        if (fromNode.rank + 1 > toNode.rank && toNode.rank < Object.keys(nodeMap).length) {
          toNode.rank = fromNode.rank + 1;
          changed = true;
        }
      }
    }
  }

  // Group nodes by rank
  const columns = {};
  for (const node of Object.values(nodeMap)) {
    if (!columns[node.rank]) {
      columns[node.rank] = [];
    }
    node.index = columns[node.rank].length;
    columns[node.rank].push(node);
  }

  return { nodeMap, columns };
}

const drawEdgePath = (u, v) => {
  const nodeWidth = 200;
  const nodeHeight = 90;
  const columnWidth = 270;
  const rowHeight = 150;
  const paddingX = 40;
  const paddingY = 40;

  const uLeft = paddingX + u.rank * columnWidth;
  const uTop = paddingY + u.index * rowHeight;
  const vLeft = paddingX + v.rank * columnWidth;
  const vTop = paddingY + v.index * rowHeight;

  if (v.rank > u.rank) {
    // Forward edge
    const x1 = uLeft + nodeWidth;
    const y1 = uTop + nodeHeight / 2;
    const x2 = vLeft - 8;
    const y2 = vTop + nodeHeight / 2;
    const controlX = (x1 + x2) / 2;
    return {
      path: `M ${x1} ${y1} C ${controlX} ${y1}, ${controlX} ${y2}, ${x2} ${y2}`,
      midX: controlX,
      midY: (y1 + y2) / 2
    };
  } else if (v.rank === u.rank) {
    // Same column edge
    const x1 = uLeft + nodeWidth;
    const y1 = uTop + nodeHeight / 2;
    const x2 = vLeft - 8;
    const y2 = vTop + nodeHeight / 2;
    const controlX = x1 + 40;
    return {
      path: `M ${x1} ${y1} C ${controlX} ${y1}, ${x2 - 40} ${y2}, ${x2} ${y2}`,
      midX: controlX - 20,
      midY: (y1 + y2) / 2
    };
  } else {
    // Back edge
    const x1 = uLeft;
    const y1 = uTop + nodeHeight / 2;
    const x2 = vLeft - 8;
    const y2 = vTop + nodeHeight / 2;
    const controlX1 = x1 - 50;
    const controlX2 = x2 - 50;
    return {
      path: `M ${x1} ${y1} C ${controlX1} ${y1}, ${controlX2} ${y2}, ${x2} ${y2}`,
      midX: (controlX1 + controlX2) / 2,
      midY: (y1 + y2) / 2
    };
  }
};

// Semantic status badges mapping with 15% transparent backgrounds and solid text colors
function getBadgeStyle(status) {
  const norm = String(status || '').toLowerCase().trim();

  if (['complete', 'approved', 'done', 'pass', 'success', 'active', 'running'].includes(norm)) {
    return {
      bg: 'bg-[#E8F9D9]',
      text: 'text-[#3D8B00]',
      border: 'border-[#58CC02]/30',
      icon: CheckCircle
    };
  }
  if (['artifact ready', 'artifact_ready', 'ready'].includes(norm)) {
    return {
      bg: 'bg-[#D0F0FF]',
      text: 'text-[#0A7BAF]',
      border: 'border-[#1CB0F6]/30',
      icon: RefreshCw
    };
  }
  if (['pending', 'blocked', 'changes_requested', 'changes requested', 'review-needed', 'review needed', 'await_prd_approval', 'await_rfc_approval', 'await_staging_approval', 'await_security_approval', 'await_release_approval', 'await_takeover_approval'].includes(norm)) {
    return {
      bg: 'bg-[#FFF0D4]',
      text: 'text-[#B36B00]',
      border: 'border-[#FF9600]/30',
      icon: Clock
    };
  }
  if (['failed', 'error', 'rejected'].includes(norm)) {
    return {
      bg: 'bg-[#FFE2E2]',
      text: 'text-[#EA2B2B]',
      border: 'border-[#FF4B4B]/30',
      icon: XCircle
    };
  }
  // Default neutral / system / no activity
  return {
    bg: 'bg-[#F1F1F1]',
    text: 'text-[#777777]',
    border: 'border-[#AFAFAF]/30',
    icon: Info
  };
}

function getStatusLabel(status) {
  const norm = String(status || '').toLowerCase().trim();
  if (norm === 'approved') return '✓ Approved';
  if (norm === 'pending') return '⏳ Pending';
  if (norm === 'complete') return '✓ Complete';
  if (norm === 'active' || norm === 'running') return 'Active';
  if (norm === 'artifact ready' || norm === 'artifact_ready') return 'Artifact Ready';
  if (norm === 'no activity' || norm === 'no_activity' || norm === 'no_active_run') return 'No Activity';
  return status.replace(/_/g, ' ');
}

// Custom file path component: displays truncated path segments, hover reveals full path tooltip
function FilePath({ path, segments = 1 }) {
  if (!path) return <span className="text-[#AFAFAF] dark:text-zinc-550 italic text-xs">not available</span>;

  const pathStr = String(path);
  const parts = pathStr.split(/[/\\]/);

  let displayName = pathStr;
  if (parts.length > 0) {
    if (segments === 1) {
      displayName = parts[parts.length - 1];
    } else if (segments === 2) {
      displayName = parts.slice(-2).join('/');
    }
  }

  return (
    <span className="group relative inline-flex max-w-full font-mono text-[11px] leading-none">
      <code className="bg-[#F1F1F1] text-[#777777] border border-[#E5E5E5] px-2 py-0.5 rounded-lg break-all cursor-help hover:bg-zinc-200 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-700 transition-colors">
        {displayName}
      </code>
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 rounded-xl bg-zinc-950 px-3 py-2 text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100 dark:bg-black border border-zinc-800 shadow-xl max-w-xs whitespace-normal break-all font-mono leading-relaxed">
        {pathStr}
      </span>
    </span>
  );
}

// Spinner component
function Spinner() {
  return (
    <div className="flex justify-center items-center py-16">
      <RefreshCw className="h-10 w-10 text-[#58CC02] animate-spin" />
    </div>
  );
}

// Humanize datetime strings to "May 27, 02:44"
function formatHumanTime(timestamp) {
  if (!timestamp) return '';
  try {
    const d = new Date(timestamp);
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[d.getMonth()];
    const day = d.getDate();
    const hour = String(d.getHours()).padStart(2, '0');
    const minute = String(d.getMinutes()).padStart(2, '0');
    return `${month} ${day}, ${hour}:${minute}`;
  } catch {
    return timestamp;
  }
}

const DEFAULT_BACKEND_OPTIONS = ["fake", "codex", "anthropic", "noop", "human"];
const DEFAULT_WORKER_ROLES = ["ui", "implementation", "qa"];
const MODEL_UNAVAILABLE_BACKENDS = ["fake", "noop", "human", "agy", "antigravity"];

const FALLBACK_AGENT_MODES = {
  codex: {
    models: ["gpt-5.3-codex", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    model_default: "gpt-5.3-codex"
  },
  anthropic: {
    models: ["claude-sonnet-4-6", "claude-3-5-sonnet-latest", "claude-3-opus-latest", "claude-3-5-haiku-latest"],
    model_default: "claude-sonnet-4-6"
  },
  claude: {
    models: ["claude-sonnet-4-6", "claude-3-5-sonnet-latest", "claude-3-opus-latest", "claude-3-5-haiku-latest"],
    model_default: "claude-sonnet-4-6"
  },
  gemini: {
    models: ["auto", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash-exp"],
    model_default: "auto"
  },
  agy: {
    models: ["auto"],
    model_default: "auto"
  },
  antigravity: {
    models: ["auto"],
    model_default: "auto"
  }
};

export default function App() {
  // Sync project key from URL search query parameter '?project='
  const getUrlProject = () => {
    const params = new URLSearchParams(window.location.search);
    return params.get('project') || '';
  };

  const [projectKey, setProjectKey] = useState(getUrlProject());
  const [activeTab, setActiveTab] = useState('overview');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  // Worker backend settings states
  const [panelGlobalMode, setPanelGlobalMode] = useState('codex');
  const [panelRoleOverrides, setPanelRoleOverrides] = useState({});
  const [panelRoleModels, setPanelRoleModels] = useState({});
  const [customModelSelected, setCustomModelSelected] = useState({});
  const [configuringRoles, setConfiguringRoles] = useState({});
  const [backendsError, setBackendsError] = useState(null);
  const [backendsSuccess, setBackendsSuccess] = useState(null);
  const [savingBackends, setSavingBackends] = useState(false);
  const [discoveredAgents, setDiscoveredAgents] = useState(null);
  const [loadingDiscoveredAgents, setLoadingDiscoveredAgents] = useState(false);


  // Theme state
  const [darkMode, setDarkMode] = useState(() => {
    const saved = localStorage.getItem('color-scheme');
    if (saved) return saved === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

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
  const [approvingAction, setApprovingAction] = useState(null);

  // Approval review artifact editor state
  const [editingArtifact, setEditingArtifact] = useState(null); // { key, content }
  const [editorContent, setEditorContent] = useState('');
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

  // Directory picker states
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerTarget, setPickerTarget] = useState(null); // 'root'
  const [pickerPath, setPickerPath] = useState('');
  const [pickerParent, setPickerParent] = useState(null);
  const [pickerEntries, setPickerEntries] = useState([]);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [pickerError, setPickerError] = useState(null);
  const [newFolderName, setNewFolderName] = useState('');
  const [newFolderError, setNewFolderError] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);

  const fetchDirectory = async (path) => {
    setPickerLoading(true);
    setPickerError(null);
    try {
      const url = path ? `/api/fs/list?path=${encodeURIComponent(path)}` : '/api/fs/list';
      const response = await fetch(url);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || data.message || `HTTP error! status: ${response.status}`);
      }
      setPickerPath(data.path);
      setPickerParent(data.parent);
      setPickerEntries(data.entries || []);
    } catch (err) {
      setPickerError(err.message || 'Failed to read directory');
    } finally {
      setPickerLoading(false);
    }
  };

  const openDirectoryPicker = (target, currentPath) => {
    setPickerTarget(target);
    setPickerOpen(true);
    setNewFolderName('');
    setNewFolderError('');
    fetchDirectory(currentPath || '');
  };

  const handleCreateFolder = async (e) => {
    if (e?.preventDefault) {
      e.preventDefault();
    }

    const name = newFolderName.trim();
    if (!name) {
      setNewFolderError('Folder name is required.');
      return;
    }

    setCreatingFolder(true);
    setNewFolderError('');
    try {
      const response = await fetch('/api/fs/mkdir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent: pickerPath, name }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || payload.message || `Failed to create folder (HTTP ${response.status})`);
      }

      setNewFolderName('');
      await fetchDirectory(payload.path || pickerPath);
    } catch (err) {
      setNewFolderError(err.message || 'Failed to create folder.');
    } finally {
      setCreatingFolder(false);
    }
  };

  const handleSelectFolder = () => {
    if (pickerTarget === 'root') {
      setProjectRoot(pickerPath);
    }
    setPickerOpen(false);
  };

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



  // Sync state when URL updates
  useEffect(() => {
    const handlePopState = () => {
      const pk = getUrlProject();
      setProjectKey(pk);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Update theme classes on document element
  useEffect(() => {
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (darkMode) {
      document.documentElement.classList.add('dark');
      if (meta) meta.content = 'dark';
      localStorage.setItem('color-scheme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      if (meta) meta.content = 'light';
      localStorage.setItem('color-scheme', 'light');
    }
  }, [darkMode]);

  const updateUrl = (key) => {
    const newUrl = key ? `${window.location.pathname}?project=${key}` : window.location.pathname;
    window.history.pushState({ project: key }, '', newUrl);
    setProjectKey(key);
  };

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
    if (activeTab === 'memory' && projectKey) {
      fetchMemory(projectKey);
    }
  }, [activeTab, projectKey]);

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
    if (activeTab === 'memory') {
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
  }, [activeTab]);

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
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to create config');
      setProjectConfig(res.project_config);
      showToast('Local config created successfully');
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
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to start workflow');

      showToast(dryRun ? 'Dry run simulation finished' : 'Workflow started!');
      if (!dryRun) {
        setIdea('');
        const pk = startProjectKey || projectKey || res.project_key;
        updateUrl(pk);
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

  const handleApproveDecision = async (approvalKey, decision, debateRef = '') => {
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
      const response = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to submit decision');
      showToast(`Decision '${decision}' submitted for ${approvalKey.toUpperCase()}`);
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
    if (activeTab === 'workers' && discoveredAgents === null && !loadingDiscoveredAgents) {
      const timer = window.setTimeout(() => {
        fetchDiscoveredAgents();
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [activeTab, discoveredAgents, loadingDiscoveredAgents]);

  // Returns first two sentences of the idea prompt for default display
  const getMissionSummary = (text) => {
    if (!text) return '';
    const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
    if (sentences.length <= 2) return text;
    return sentences.slice(0, 2).join(' ');
  };

  const directoryPickerOverlay = pickerOpen && (
    <div className="fixed inset-0 z-45 flex items-center justify-center bg-zinc-950/50 px-4 py-6 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dir-picker-title"
        className="w-full max-w-2xl bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_8px_0_#D1D1D1] dark:shadow-[0_8px_0_#1a1a1a] rounded-2xl overflow-hidden flex flex-col max-h-[85vh]"
      >
        {/* Header */}
        <div className="px-5 py-4 bg-[#F7F7F7] dark:bg-zinc-900/80 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex items-start justify-between gap-4 shrink-0">
          <div>
            <p className="text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">Directory Browser</p>
            <h3 id="dir-picker-title" className="font-mono text-xs font-extrabold text-[#3D8B00] dark:text-emerald-400 mt-1">
              Selecting Project Root Directory
            </h3>
          </div>
          <button
            type="button"
            onClick={() => setPickerOpen(false)}
            className="bg-white dark:bg-zinc-850 hover:bg-[#FFE2E2] border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_3px_0_#D1D1D1] dark:shadow-[0_3px_0_#1a1a1a] text-zinc-550 hover:text-[#FF4B4B] p-2 rounded-2xl transition-all duo-button-physics cursor-pointer"
            aria-label="Close directory browser"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Navigation Bar / Breadcrumb */}
        <div className="px-5 py-3 bg-[#F9F9F9] dark:bg-zinc-900/40 border-b border-[#E5E5E5] dark:border-zinc-800 flex items-center gap-3 shrink-0">
          <button
            type="button"
            disabled={pickerParent === null || pickerLoading}
            onClick={() => fetchDirectory(pickerParent)}
            className="flex items-center gap-1 bg-white dark:bg-zinc-850 border-2 border-[#E5E5E5] dark:border-zinc-800 disabled:opacity-50 disabled:pointer-events-none hover:bg-zinc-50 dark:hover:bg-zinc-800 shadow-[0_2px_0_#D1D1D1] dark:shadow-[0_2px_0_#1a1a1a] text-zinc-700 dark:text-zinc-300 font-bold px-3 py-1.5 rounded-xl text-xs transition-all duo-button-physics cursor-pointer shrink-0"
          >
            <ArrowUp className="h-3 w-3" /> Up
          </button>
          <div className="flex-grow font-mono text-[10px] text-zinc-600 dark:text-zinc-400 bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 px-3 py-1.5 rounded-xl overflow-x-auto whitespace-nowrap scrollbar-thin select-all">
            {pickerPath || '/'}
          </div>
        </div>

        {/* New Folder */}
        <form
          onSubmit={handleCreateFolder}
          className="px-5 py-3 bg-white dark:bg-zinc-950 border-b border-[#E5E5E5] dark:border-zinc-800 shrink-0"
        >
          <div className="flex flex-col sm:flex-row gap-2">
            <label className="sr-only" htmlFor="new-folder-name">New folder name</label>
            <input
              id="new-folder-name"
              type="text"
              value={newFolderName}
              onChange={(e) => {
                setNewFolderName(e.target.value);
                if (newFolderError) setNewFolderError('');
              }}
              placeholder="New folder name"
              disabled={pickerLoading || creatingFolder}
              className="flex-1 min-w-0 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 text-xs font-mono focus:outline-none focus:border-[#58CC02] disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={pickerLoading || creatingFolder || !newFolderName.trim()}
              className="bg-white dark:bg-zinc-850 hover:bg-[#E8F9D9] hover:text-[#3D8B00] border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_2px_0_#D1D1D1] dark:shadow-[0_2px_0_#1a1a1a] text-zinc-700 dark:text-zinc-300 font-bold px-3 py-2 rounded-xl text-xs transition-all duo-button-physics disabled:opacity-50 disabled:pointer-events-none cursor-pointer shrink-0 inline-flex items-center justify-center gap-1.5"
            >
              {creatingFolder ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Folder className="h-3.5 w-3.5" />}
              <span>New folder</span>
            </button>
          </div>
          {newFolderError && (
            <div className="mt-2 bg-[#FFF0D4] dark:bg-amber-950/20 border border-[#FF9600]/40 px-3 py-2 rounded-xl text-[#B36B00] dark:text-amber-300 text-[10px] font-bold flex items-center gap-2">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>{newFolderError}</span>
            </div>
          )}
        </form>

        {/* Body */}
        <div className="p-5 overflow-y-auto flex-grow min-h-0 bg-white dark:bg-zinc-950">
          {pickerLoading ? (
            <div className="flex items-center justify-center p-10 text-zinc-500 font-bold">
              <RefreshCw className="h-5 w-5 animate-spin mr-2 text-[#58CC02]" />
              <span>Loading directory...</span>
            </div>
          ) : pickerError ? (
            <div className="bg-[#FFF0D4] dark:bg-amber-950/20 border-2 border-[#FF9600]/30 p-4 rounded-2xl text-[#B36B00] dark:text-amber-300 text-xs font-bold flex items-center space-x-2">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <div className="flex-grow">
                <p className="font-bold">Error loading folder</p>
                <p className="font-normal font-mono text-[10px] mt-0.5">{pickerError}</p>
              </div>
            </div>
          ) : pickerEntries.length === 0 ? (
            <div className="text-center py-10 text-zinc-400 dark:text-zinc-500 font-bold text-xs">
              This directory is empty.
            </div>
          ) : (
            <div className="space-y-1">
              {pickerEntries.map((entry) => (
                <button
                  key={entry.path}
                  type="button"
                  onClick={() => fetchDirectory(entry.path)}
                  className="w-full flex items-center justify-between text-left px-3 py-2 rounded-xl hover:bg-zinc-50 dark:hover:bg-zinc-900/40 border border-transparent hover:border-zinc-200 dark:hover:border-zinc-800 transition-colors cursor-pointer group"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <Folder className="h-4 w-4 text-[#58CC02] dark:text-[#78D639] group-hover:scale-110 transition-transform shrink-0" />
                    <span className="font-mono text-xs text-zinc-700 dark:text-zinc-300 truncate select-all">{entry.name}</span>
                  </div>
                  {entry.is_git_repo && (
                    <span className="flex items-center gap-0.5 bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400 text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded-md border border-blue-100 dark:border-blue-900/40 shrink-0">
                      <GitBranch className="h-3 w-3 inline" /> git
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 bg-[#F7F7F7] dark:bg-zinc-900/80 border-t-2 border-[#E5E5E5] dark:border-zinc-800 flex justify-between items-center gap-3 shrink-0">
          <button
            type="button"
            onClick={() => setPickerOpen(false)}
            className="bg-white dark:bg-zinc-850 hover:bg-zinc-50 dark:hover:bg-zinc-800 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_3px_0_#D1D1D1] dark:shadow-[0_3px_0_#1a1a1a] text-zinc-700 dark:text-zinc-300 font-bold px-4 py-2 rounded-2xl text-xs transition-all duo-button-physics cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={pickerLoading || !!pickerError}
            onClick={handleSelectFolder}
            className="bg-[#58CC02] hover:bg-[#4EB302] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-[#FFFFFF] font-bold px-5 py-2 rounded-2xl text-xs disabled:opacity-50 disabled:pointer-events-none transition-all duo-button-physics cursor-pointer"
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );

  // Render project overview (Multi-project control room)
  if (!projectKey) {
    const projectItems = data?.project_items || [];
    const legacyState = data?.legacy_state;

    return (
      <>
        <div className="min-h-screen bg-[#F7F7F7] dark:bg-zinc-900 text-zinc-900 dark:text-zinc-150 transition-colors flex flex-col font-sans antialiased">
          {/* Header - 60px height, white background, bottom border */}
          <header className="h-[60px] border-b-2 border-[#E5E5E5] dark:border-zinc-800 bg-white dark:bg-zinc-950 px-6 flex items-center justify-between shadow-sm shrink-0">
          <div className="flex items-center space-x-3">
            <div className="p-1.5 bg-[#58CC02] border-2 border-[#3D8B00] text-white rounded-xl shadow-[0_2px_0_#3D8B00]">
              <LayoutDashboard className="h-5 w-5" />
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider font-bold text-[#AFAFAF]">Multi-project control room</span>
              <h1 className="text-base font-extrabold tracking-tight text-zinc-800 dark:text-zinc-200">GoodTeam Projects</h1>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={() => setDarkMode(!darkMode)}
              className="bg-white dark:bg-zinc-850 p-2 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_3px_0_#D1D1D1] dark:shadow-[0_3px_0_#1a1a1a] text-zinc-650 dark:text-zinc-350 rounded-2xl transition-all duo-button-physics"
              aria-label="Toggle theme"
            >
              {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </header>

        {/* Global Toast */}
        {message && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-2xl shadow-xl text-xs font-bold border-2 flex items-center space-x-2 animate-fade-in-down ${message.isError ? 'bg-[#FFE2E2] text-[#EA2B2B] border-[#FF4B4B]' : 'bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]'}`}>
            {message.isError ? <AlertTriangle className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            <span>{message.text}</span>
          </div>
        )}

        <main className="max-w-6xl mx-auto w-full px-6 py-8 flex-grow space-y-8">
          {/* Legacy notice */}
          {legacyState && (
            <div className="bg-[#FFF0D4] dark:bg-amber-950/20 border-2 border-[#FF9600]/30 p-5 rounded-2xl flex flex-col md:flex-row md:items-center justify-between text-[#B36B00] dark:text-amber-300 text-xs gap-4 shadow-[0_4px_0_#FFE2BE] dark:shadow-none">
              <div>
                <span className="font-extrabold text-sm block mb-1">Legacy Active Run Detected</span>
                <span>Active path: <code className="bg-white/60 dark:bg-amber-900/40 px-1.5 py-0.5 rounded-lg border border-amber-250 dark:border-amber-900 font-mono">{legacyState.project_key}</code> currently at <code className="bg-white/60 dark:bg-amber-900/40 px-1.5 py-0.5 rounded-lg border border-amber-250 dark:border-amber-900 font-mono">{legacyState.next_node || legacyState.current_node}</code></span>
              </div>
              <button
                onClick={() => updateUrl(legacyState.project_key)}
                className="bg-[#FF9600] border-2 border-[#B36B00] shadow-[0_3px_0_#B36B00] text-white font-bold px-4 py-1.5 rounded-2xl text-[10px] uppercase tracking-wider transition-all duo-button-physics shrink-0 self-start md:self-auto"
              >
                Resume Run
              </button>
            </div>
          )}

          {/* Project List */}
          {loading ? (
            <Spinner />
          ) : error ? (
            <div className="bg-[#FFE2E2] border-2 border-[#FF4B4B] p-6 rounded-2xl text-[#EA2B2B] flex items-center space-x-3">
              <AlertTriangle className="h-6 w-6 shrink-0" />
              <span className="font-bold text-sm">{error}</span>
            </div>
          ) : (
            <div className="space-y-6">
              <h2 className="text-sm font-extrabold uppercase tracking-widest text-[#AFAFAF]">Registered Projects ({projectItems.length})</h2>
              {projectItems.length === 0 ? (
                <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] p-12 text-center rounded-2xl text-zinc-500 max-w-2xl mx-auto">
                  <Folder className="h-12 w-12 mx-auto mb-4 text-[#AFAFAF]" />
                  <p className="font-bold text-zinc-700 dark:text-zinc-300 mb-1">No local project configs found</p>
                  <p className="text-xs">Create a project config with the form below or drop yaml file into <code className="bg-[#F1F1F1] dark:bg-zinc-900 px-1.5 py-0.5 rounded border border-[#E5E5E5] dark:border-zinc-850 text-xs">projects/*.local.yaml</code></p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {projectItems.map((item) => {
                    const isActive = item.status === 'active';
                    const activeBadge = getBadgeStyle(item.status);
                    return (
                      <article
                        key={item.project_key}
                        className={`bg-white dark:bg-zinc-950 border-2 rounded-2xl shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] overflow-hidden flex flex-col h-full group hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] dark:hover:shadow-[0_6px_0_#1a1a1a] transition-all duration-200 ${isActive ? 'border-[#58CC02]' : 'border-[#E5E5E5] dark:border-zinc-800'}`}
                      >
                        <div className="p-6 flex-grow">
                          <div className="flex items-start justify-between">
                            <div>
                              <h3 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200 group-hover:text-[#58CC02] dark:group-hover:text-[#58CC02] transition-colors">
                                {item.project_name}
                              </h3>
                              <p className="text-[10px] font-mono text-[#AFAFAF] mt-0.5 font-bold uppercase">
                                {item.project_key}
                              </p>
                            </div>
                            <span className={`inline-flex items-center space-x-1.5 px-3 py-1 rounded-full text-[10px] font-extrabold uppercase border ${activeBadge.bg} ${activeBadge.text} ${activeBadge.border}`}>
                              {isActive && <span className="h-1.5 w-1.5 bg-[#1CB0F6] rounded-full animate-pulse" />}
                              <span>{item.status}</span>
                            </span>
                          </div>

                          <dl className="mt-6 grid grid-cols-2 gap-x-4 gap-y-3 text-[11px] border-t-2 border-[#F1F1F1] dark:border-zinc-900 pt-4 font-semibold">
                            <div>
                              <dt className="text-[#AFAFAF] uppercase tracking-wider text-[9px] mb-0.5">Model Profile</dt>
                              <dd className="text-zinc-700 dark:text-zinc-300 capitalize font-bold">{item.model_profile}</dd>
                            </div>
                            <div>
                              <dt className="text-[#AFAFAF] uppercase tracking-wider text-[9px] mb-0.5">Delivery Mode</dt>
                              <dd className="text-[#58CC02] font-mono font-bold">{item.delivery_mode}</dd>
                            </div>
                            <div>
                              <dt className="text-[#AFAFAF] uppercase tracking-wider text-[9px] mb-0.5">Next Action</dt>
                              <dd className="text-zinc-700 dark:text-zinc-300 font-mono truncate font-bold">{item.next_node || 'no active run'}</dd>
                            </div>
                            <div>
                              <dt className="text-[#AFAFAF] uppercase tracking-wider text-[9px] mb-0.5">Last Active</dt>
                              <dd className="text-zinc-700 dark:text-zinc-300 truncate" title={item.last_action || 'none'}>
                                {item.last_action ? formatHumanTime(item.last_action) : 'none'}
                              </dd>
                            </div>
                            <div className="col-span-2">
                              <dt className="text-[#AFAFAF] uppercase tracking-wider text-[9px] mb-0.5">Config Path</dt>
                              <dd className="truncate text-zinc-500 font-mono text-[9px]" title={item.project_config}><FilePath path={item.project_config} segments={2} /></dd>
                            </div>
                          </dl>
                        </div>

                        <div className="bg-[#F7F7F7] dark:bg-zinc-950 px-6 py-4 border-t-2 border-[#E5E5E5] dark:border-zinc-900 flex justify-between items-center">
                          <button
                            onClick={() => handleDeleteProject(item.project_key)}
                            disabled={actionLoading}
                            className="bg-white hover:bg-[#FFE2E2] border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-[#FF4B4B] p-2 rounded-2xl transition-all duo-button-physics shrink-0"
                            title="Delete project config"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>

                          <div className="flex space-x-2">
                            {isActive && (
                              <button
                                onClick={() => handleTerminate(item.project_key)}
                                disabled={actionLoading}
                                className="bg-white hover:bg-amber-50 border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-[#FF9600] font-bold px-3 py-1.5 rounded-2xl text-[10px] uppercase tracking-wider transition-all duo-button-physics"
                              >
                                Terminate
                              </button>
                            )}
                            <button
                              onClick={() => updateUrl(item.project_key)}
                              className="bg-white border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-zinc-750 font-bold px-4 py-1.5 rounded-2xl text-[10px] uppercase tracking-wider transition-all duo-button-physics"
                            >
                              {isActive ? 'Resume' : 'Start'}
                            </button>
                            <button
                              onClick={() => updateUrl(item.project_key)}
                              className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold px-4 py-1.5 rounded-2xl text-[10px] uppercase tracking-wider transition-all duo-button-physics"
                            >
                              Open
                            </button>
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Start Workflow Console */}
          <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] rounded-2xl p-6 space-y-6">
            <div className="border-b-2 border-[#F1F1F1] dark:border-zinc-900 pb-4">
              <h2 className="text-lg font-extrabold text-zinc-800 dark:text-zinc-200">Start Workflow Console</h2>
              <p className="text-xs text-zinc-400 mt-1">Adopt an existing project repo structure or bootstrap a clean workspace scaffold.</p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              {/* Left Config Panel */}
              <div className="lg:col-span-2 space-y-6">
                <div className="flex bg-[#F1F1F1] dark:bg-zinc-900 p-1 rounded-xl w-fit">
                  <button
                    onClick={() => setStartMode('existing_project')}
                    className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${startMode === 'existing_project' ? 'bg-white dark:bg-zinc-850 shadow-sm text-[#3D8B00] dark:text-emerald-400' : 'text-[#AFAFAF] hover:text-zinc-800 dark:hover:text-zinc-200'}`}
                  >
                    Adopt Existing Project
                  </button>
                  <button
                    onClick={() => setStartMode('new_project')}
                    className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${isNewProjectMode ? 'bg-white dark:bg-zinc-850 shadow-sm text-[#3D8B00] dark:text-emerald-400' : 'text-[#AFAFAF] hover:text-zinc-800 dark:hover:text-zinc-200'}`}
                  >
                    Start New Project
                  </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-bold">
                  <div className="col-span-2">
                    <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Workflow Goal</label>
                    <textarea
                      value={idea}
                      onChange={(e) => setIdea(e.target.value)}
                      placeholder={startMode === 'existing_project' ? "Enter takeover goal (e.g. audit frontend files)..." : "Describe the new project and desired features..."}
                      className="w-full h-24 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 focus:outline-none focus:border-[#58CC02] font-mono leading-relaxed transition-colors resize-none"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Project Name</label>
                    <input
                      type="text"
                      value={projectName}
                      onChange={(e) => setProjectName(e.target.value)}
                      placeholder="e.g. ContentOps Studio"
                      className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-4 py-2.5 focus:outline-none focus:border-[#58CC02] transition-colors"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Project Key</label>
                    <input
                      type="text"
                      value={startProjectKey}
                      onChange={(e) => setStartProjectKey(e.target.value)}
                      placeholder="e.g. CONTENTOPS"
                      className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-4 py-2.5 focus:outline-none focus:border-[#58CC02] font-mono uppercase transition-colors"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Model Profile</label>
                    <select
                      value={modelProfile}
                      onChange={(e) => setModelProfile(e.target.value)}
                      className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-4 py-2.5 focus:outline-none focus:border-[#58CC02] transition-colors"
                    >
                      <option value="balanced">Balanced</option>
                      <option value="cheap">Cheap</option>
                      <option value="max_quality">Max Quality</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Delivery Mode</label>
                    <select
                      value={deliveryMode}
                      onChange={(e) => setDeliveryMode(e.target.value)}
                      className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-4 py-2.5 focus:outline-none focus:border-[#58CC02] transition-colors"
                    >
                      {DELIVERY_MODE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>

                  {deliveryMode === 'custom' && (
                    <div className="col-span-2">
                      <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Delivery Standards</label>
                      <textarea
                        value={deliveryStandards}
                        onChange={(e) => setDeliveryStandards(e.target.value)}
                        placeholder="每行一条交付标准"
                        className="w-full h-24 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 focus:outline-none focus:border-[#58CC02] font-mono leading-relaxed transition-colors resize-none"
                      />
                    </div>
                  )}

                  <div className="col-span-2 grid grid-cols-1 gap-4 border-2 border-[#E5E5E5] dark:border-zinc-800 p-5 rounded-2xl bg-[#F7F7F7] dark:bg-zinc-900/30">
                    <div className="font-extrabold text-[10px] text-[#AFAFAF] uppercase tracking-wider">Project Location</div>
                    <div>
                      <label className="block text-[10px] text-zinc-550 mb-1 uppercase">Project Root</label>
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={projectRoot}
                          onChange={(e) => setProjectRoot(e.target.value)}
                          placeholder="Absolute path to project root"
                          className="flex-1 min-w-0 bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs font-mono"
                        />
                        <button
                          type="button"
                          onClick={() => openDirectoryPicker('root', projectRoot)}
                          className="bg-white dark:bg-zinc-850 hover:bg-zinc-50 dark:hover:bg-zinc-800 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_2px_0_#D1D1D1] dark:shadow-[0_2px_0_#1a1a1a] text-zinc-700 dark:text-zinc-300 font-bold px-2.5 py-1.5 rounded-xl text-xs transition-all duo-button-physics shrink-0 cursor-pointer"
                        >
                          Browse…
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="col-span-2 border-t border-zinc-100 dark:border-zinc-900 my-4" />

                  {/* Toggle Advanced Button */}
                  <div className="col-span-2">
                    <button
                      type="button"
                      onClick={() => setShowConfigDrawer(!showConfigDrawer)}
                      className="text-xs font-bold text-[#3D8B00] hover:text-[#58CC02] transition-colors flex items-center space-x-1"
                    >
                      <span>{showConfigDrawer ? 'Hide Advanced Config' : 'Show Advanced Config (Task Backend, Deployment)'}</span>
                      <ChevronDown className={`h-4 w-4 transition-transform ${showConfigDrawer ? 'rotate-180' : ''}`} />
                    </button>
                  </div>

                  {showConfigDrawer && (
                    <>
                      <div className="col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 border-2 border-[#E5E5E5] dark:border-zinc-800 p-5 rounded-2xl bg-[#F7F7F7] dark:bg-zinc-900/30">
                        <div className="col-span-2 font-extrabold text-[10px] text-[#AFAFAF] uppercase tracking-wider">Services Backend</div>
                        <div>
                          <label className="block text-[10px] text-zinc-550 mb-1 uppercase">Task Sync</label>
                          <select
                            value={taskSync}
                            onChange={(e) => setTaskSync(e.target.value)}
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs"
                          >
                            <option value="">Local only</option>
                            <option value="plane">Plane Workspace</option>
                            <option value="linear">Linear Workspace</option>
                            <option value="github_issues">GitHub Issues</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-zinc-550 mb-1 uppercase">Worker Execution</label>
                          <select
                            value={workerBackendMode}
                            onChange={(e) => setWorkerBackendMode(e.target.value)}
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs"
                          >
                            <option value="codex">Codex CLI / Autopilot</option>
                            <option value="manual">Human Interactive</option>
                            <option value="noop">Dry-run only</option>
                          </select>
                        </div>

                        {taskSync === 'plane' && (
                          <div className="col-span-2 grid grid-cols-2 gap-3 border-2 border-[#E5E5E5] dark:border-zinc-800 p-4 rounded-xl bg-white dark:bg-zinc-950 mt-2">
                            <div className="col-span-2 font-extrabold text-[9px] text-[#AFAFAF] uppercase tracking-wider">Plane Credentials</div>
                            <div className="col-span-2">
                              <label className="block text-[9px] text-zinc-500 mb-1">API Host</label>
                              <input type="text" value={planeApiHost} onChange={(e) => setPlaneApiHost(e.target.value)} className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-205 dark:border-zinc-800 rounded px-2 py-1 text-xs font-mono" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Workspace Slug</label>
                              <input type="text" value={planeWorkspaceSlug} onChange={(e) => setPlaneWorkspaceSlug(e.target.value)} placeholder="e.g. leader_kanban" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 text-xs font-mono" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Project ID</label>
                              <input type="text" value={planeProjectId} onChange={(e) => setPlaneProjectId(e.target.value)} placeholder="uuid" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 text-xs font-mono" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">API Key Env</label>
                              <input type="text" value={planeApiKeyEnv} onChange={(e) => setPlaneApiKeyEnv(e.target.value)} className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 text-xs font-mono" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">MCP Server</label>
                              <input type="text" value={planeMcpServer} onChange={(e) => setPlaneMcpServer(e.target.value)} className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 text-xs font-mono" />
                            </div>
                          </div>
                        )}
                      </div>

                      <div className="col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 border-2 border-[#E5E5E5] dark:border-zinc-800 p-5 rounded-2xl bg-[#F7F7F7] dark:bg-zinc-900/30">
                        <div className="col-span-2 font-extrabold text-[10px] text-[#AFAFAF] uppercase tracking-wider">Deployment & Evidence Contract</div>
                        <div className="col-span-2">
                          <label className="block text-[10px] text-zinc-555 mb-1.5 uppercase">Deployment Backend</label>
                          <select
                            value={deploymentBackendMode}
                            onChange={(e) => setDeploymentBackendMode(e.target.value)}
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs font-semibold"
                          >
                            <option value="">None (skip automated deployments)</option>
                            <option value="ssh_compose">Docker Compose via SSH</option>
                          </select>
                        </div>

                        {deploymentBackendMode === 'ssh_compose' && (
                          <div className="col-span-2 grid grid-cols-2 gap-3 border-2 border-[#E5E5E5] dark:border-zinc-800 p-4 rounded-xl bg-white dark:bg-zinc-950 mt-2 text-xs">
                            <div className="col-span-2 font-extrabold text-[9px] text-[#AFAFAF] uppercase tracking-wider">SSH Deploy Configuration</div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Staging SSH Target</label>
                              <input type="text" value={stagingSshTarget} onChange={(e) => setStagingSshTarget(e.target.value)} placeholder="user@host" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Staging Remote Dir</label>
                              <input type="text" value={stagingRemoteDir} onChange={(e) => setStagingRemoteDir(e.target.value)} placeholder="/srv/app" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Staging Git Ref</label>
                              <input type="text" value={stagingGitRef} onChange={(e) => setStagingGitRef(e.target.value)} placeholder="main" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Staging Public URL</label>
                              <input type="text" value={stagingPublicUrl} onChange={(e) => setStagingPublicUrl(e.target.value)} placeholder="http://url:port" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>
                            <div className="col-span-2">
                              <label className="block text-[9px] text-zinc-500 mb-1">Deploy Command</label>
                              <input type="text" value={stagingDeployCommand} onChange={(e) => setStagingDeployCommand(e.target.value)} placeholder="docker compose up -d" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Healthcheck Command</label>
                              <input type="text" value={stagingHealthcheckCommand} onChange={(e) => setStagingHealthcheckCommand(e.target.value)} placeholder="curl -f /health" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>
                            <div>
                              <label className="block text-[9px] text-zinc-500 mb-1">Smoke Command</label>
                              <input type="text" value={stagingSmokeCommand} onChange={(e) => setStagingSmokeCommand(e.target.value)} placeholder="curl -f /" className="w-full bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 font-mono text-[11px]" />
                            </div>

                            <div className="col-span-2 border-t-2 border-[#F1F1F1] dark:border-zinc-900 my-1" />

                            <div className="col-span-2 flex items-center space-x-2">
                              <input type="checkbox" id="contract-req" checked={deliveryContractRequired} onChange={(e) => setDeliveryContractRequired(e.target.checked)} className="rounded text-[#58CC02] focus:ring-[#58CC02] h-4 w-4" />
                              <label htmlFor="contract-req" className="font-extrabold text-[10px] text-[#AFAFAF] uppercase tracking-wider">Require functional delivery evidence</label>
                            </div>
                            <div className="col-span-2">
                              <label className="block text-[9px] text-zinc-500 mb-1">Minimum Evidence Gate</label>
                              <select value={deliveryContractMinEvidence} onChange={(e) => setDeliveryContractMinEvidence(e.target.value)} className="bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1 text-xs">
                                <option value="infrastructure">Infrastructure Layer</option>
                                <option value="functional">Functional Application Layer</option>
                              </select>
                            </div>
                            <div className="col-span-2">
                              <label className="block text-[9px] text-zinc-500 mb-1">Fatal Output Log Patterns (line separate)</label>
                              <textarea value={deliveryContractFatalPatterns} onChange={(e) => setDeliveryContractFatalPatterns(e.target.value)} placeholder="e.g. migrate: executable file not found" className="w-full h-16 bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-2 font-mono text-xs resize-none" />
                            </div>
                            <div className="col-span-2">
                              <label className="block text-[9px] text-zinc-500 mb-1">Staging Acceptance Tests (id|type|command per line)</label>
                              <textarea value={deliveryContractAcceptance} onChange={(e) => setDeliveryContractAcceptance(e.target.value)} placeholder="critical-path|ssh_command|docker compose exec -T backend ./migrate status" className="w-full h-16 bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-2 font-mono text-xs resize-none" />
                            </div>
                          </div>
                        )}
                      </div>

                      <div className="col-span-2">
                        <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Role Model Overrides (one per line, role=model)</label>
                        <textarea
                          value={roleModelOverrides}
                          onChange={(e) => setRoleModelOverrides(e.target.value)}
                          placeholder="architect=codex:gpt-5.5&#10;implementation=codex:gpt-5.3-codex"
                          className="w-full h-20 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-3 font-mono leading-relaxed transition-colors resize-none"
                        />
                      </div>

                      <div className="col-span-2">
                        <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1.5">Local Project Config Path</label>
                        <input
                          type="text"
                          value={projectConfig}
                          onChange={(e) => setProjectConfig(e.target.value)}
                          placeholder="projects/project-key.local.yaml"
                          className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-4 py-2.5 focus:outline-none focus:border-[#58CC02] font-mono transition-colors"
                        />
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Right Status Panel */}
              <div className="bg-[#F7F7F7] dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-5 flex flex-col justify-between h-fit self-start space-y-6">
                <div>
                  <h4 className="font-extrabold text-[10px] uppercase tracking-wider text-[#AFAFAF] mb-4">Launch Check</h4>
                  <div className="space-y-3.5 text-xs font-semibold">
                    <div className="flex justify-between items-center border-b border-zinc-200 dark:border-zinc-850 pb-2">
                      <span className="text-zinc-550">Project Target</span>
                      <span className="font-mono text-zinc-700 dark:text-zinc-300 truncate max-w-[150px] font-bold">{startProjectKey || 'GTO'}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-zinc-200 dark:border-zinc-850 pb-2">
                      <span className="text-zinc-550">Source Mode</span>
                      <span className="font-bold text-[#3D8B00]">{startMode === 'existing_project' ? 'Adoption' : 'New Project'}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-zinc-200 dark:border-zinc-850 pb-2">
                      <span className="text-zinc-550">Model Profile</span>
                      <span className="font-bold capitalize">{modelProfile}</span>
                    </div>
                    <div className="flex justify-between items-center border-b border-zinc-200 dark:border-zinc-850 pb-2">
                      <span className="text-zinc-550">Delivery Mode</span>
                      <span className="font-mono font-bold text-[#58CC02]">{deliveryMode}</span>
                    </div>
                    <div className="flex justify-between items-center pb-1">
                      <span className="text-zinc-555">Worker Backend</span>
                      <span className="font-bold text-zinc-700 dark:text-zinc-300 capitalize">{workerBackendMode}</span>
                    </div>
                  </div>
                </div>

                <div className="border-t-2 border-[#E5E5E5] dark:border-zinc-850 pt-4 space-y-4">
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="dry-run"
                      checked={dryRun}
                      onChange={(e) => setDryRun(e.target.checked)}
                      className="rounded text-[#58CC02] focus:ring-[#58CC02] h-4 w-4 cursor-pointer"
                    />
                    <label htmlFor="dry-run" className="text-xs font-bold text-zinc-650 dark:text-zinc-400 cursor-pointer">
                      Dry run simulation
                    </label>
                  </div>

                  <div className="bg-[#1e231f] text-[11px] text-[#9fbba6] font-mono p-3 rounded-xl border-2 border-[#324538] leading-relaxed select-all">
                    {startMode === 'existing_project'
                      ? "Adopt mode reads the project config first and generates takeover-context.md before PRD."
                      : "New project mode generates standard prd.md inside target artifacts folder."}
                  </div>

                  <div className="flex flex-col space-y-2">
                    <button
                      type="button"
                      onClick={handleCreateConfig}
                      disabled={actionLoading}
                      className="w-full bg-white dark:bg-zinc-850 hover:bg-[#E8F9D9] hover:text-[#3D8B00] border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_3px_0_#D1D1D1] dark:shadow-[0_3px_0_#1a1a1a] text-zinc-700 dark:text-zinc-300 font-bold py-2 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics shrink-0"
                    >
                      Create Local Config
                    </button>
                    <button
                      type="button"
                      onClick={handleStartWorkflow}
                      disabled={actionLoading || startingWorkflow}
                      className="w-full bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_4px_0_#3D8B00] text-white font-bold py-3 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center space-x-2"
                    >
                      {startingWorkflow ? (
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Play className="h-3.5 w-3.5 fill-current" />
                      )}
                      <span>{startingWorkflow ? 'Starting...' : (dryRun ? 'Preview Workflow' : (startMode === 'existing_project' ? 'Start Adoption' : 'Start New Project'))}</span>
                    </button>
                    {startingWorkflow && (
                      <p className="text-[10px] text-zinc-500 text-center mt-2 animate-pulse">
                        Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
          </main>
        </div>
        {directoryPickerOverlay}
      </>
    );
  }

  // Dashboard Detailed View for a single project
  const state = data?.state;
  const startContext = data?.start_context || {};
  const backendOptions = startContext.worker_backend_options || DEFAULT_BACKEND_OPTIONS;
  const safeDiscoveredAgents = (discoveredAgents || []).reduce((acc, agent) => {
    const backend = String(agent?.backend || '').trim();
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
  const roleBackendOptions = Array.from(new Set([
    ...backendOptions,
    ...discoveredBackendOptions,
  ]));
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
  const totalApprovalsCount = approvalReviews.length || 6;
  const approvalProgressPercent = Math.min(100, Math.round((approvedCount / totalApprovalsCount) * 100));
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
      className="bg-[#E8F9D9] hover:bg-[#D7F6BF] dark:bg-emerald-950/30 dark:hover:bg-emerald-900/40 text-[#3D8B00] dark:text-emerald-400 px-2 py-0.5 rounded font-mono border-2 border-[#58CC02]/30 shadow-[0_2px_0_#A7D98A] dark:shadow-[0_2px_0_#12381f] transition-all duo-button-physics focus:outline-none focus:ring-2 focus:ring-[#58CC02]/40"
      aria-label={`Open full memory event ${eventId}`}
    >
      {eventId}
    </button>
  );

  return (
    <div className="min-h-screen bg-[#F7F7F7] dark:bg-zinc-900 text-zinc-900 dark:text-zinc-150 transition-colors flex flex-col lg:flex-row font-sans antialiased">

      {/* Sidebar - fixed desktop 220px wide, white, right border */}
      <aside className={`w-full lg:w-[220px] shrink-0 bg-white dark:bg-zinc-950 text-zinc-700 dark:text-zinc-400 flex flex-col border-r-2 border-[#E5E5E5] dark:border-zinc-800 lg:min-h-screen ${mobileMenuOpen ? 'block' : 'hidden lg:flex'}`}>
        <div className="p-5 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex justify-between items-center bg-zinc-50/50 dark:bg-black/30">
          <div>
            <span className="text-[10px] uppercase tracking-widest text-[#3D8B00] dark:text-emerald-500 font-extrabold">GoodTeam OS</span>
            <h2 className="text-sm font-extrabold tracking-tight text-zinc-800 dark:text-zinc-200 mt-0.5">{state?.project_name || startContext.project_name || 'Dashboard'}</h2>
          </div>
          <button
            onClick={() => updateUrl('')}
            className="text-[10px] text-zinc-550 hover:text-[#3D8B00] border-2 border-[#E5E5E5] dark:border-zinc-800 hover:border-[#58CC02] px-2.5 py-0.5 rounded-xl transition-all font-bold uppercase"
          >
            Exit
          </button>
        </div>

        {/* Project Selector Indicator */}
        <div className="px-4 py-3 bg-[#F1F1F1] dark:bg-zinc-900 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex items-center space-x-2 text-xs font-bold text-zinc-650">
          <Folder className="h-4 w-4 text-[#3D8B00] shrink-0" />
          <span className="font-mono text-zinc-800 dark:text-zinc-200 select-all">{projectKey}</span>
        </div>

        {/* Tab buttons - active: bg #E8F9D9, left border 4px #58CC02, text #3D8B00, font-weight 700 */}
        <nav className="flex-grow py-4 space-y-1 overflow-y-auto">
          {[
            { id: 'overview', label: 'Overview', icon: LayoutDashboard },
            { id: 'devloop', label: 'Dev Loop', icon: GitPullRequest },
            { id: 'workflow', label: 'Workflow Graph', icon: GitBranch },
            { id: 'roles', label: 'Roles', icon: Users },
            { id: 'artifacts', label: 'Artifacts', icon: FileCode },
            { id: 'actions', label: 'Actions', icon: Activity },
            { id: 'board', label: 'Agile Board', icon: Kanban },
            { id: 'smoke', label: 'Smoke Runs', icon: PlaySquare },
            { id: 'workers', label: 'Workers', icon: Cpu },
            { id: 'memory', label: 'Memory', icon: Brain },
            { id: 'messages', label: 'Messages', icon: MessageSquare },
            { id: 'approvals', label: 'Approvals', icon: CheckCircle2 },
            { id: 'system', label: 'System', icon: Settings },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id);
                  setMobileMenuOpen(false);
                }}
                className={`w-full h-14 flex items-center space-x-3 px-4 transition-all ${isActive ? 'bg-[#E8F9D9] dark:bg-emerald-950/20 border-l-[4px] border-[#58CC02] text-[#3D8B00] dark:text-emerald-400 font-bold' : 'hover:bg-zinc-50 dark:hover:bg-zinc-900 border-l-[4px] border-transparent font-semibold text-zinc-550'}`}
              >
                <Icon className={`h-4.5 w-4.5 shrink-0 ${isActive ? 'text-[#3D8B00]' : 'text-zinc-400'}`} />
                <span className="text-xs uppercase tracking-wider tracking-wide">{tab.label}</span>
              </button>
            );
          })}
        </nav>

        {/* Sidebar Footer */}
        <div className="p-4 border-t-2 border-[#E5E5E5] dark:border-zinc-800 flex items-center justify-between text-xs bg-zinc-50/50 dark:bg-black/30 font-bold text-[#AFAFAF]">
          <span className="font-mono">v0.1.0</span>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className="p-1.5 rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 hover:bg-[#F1F1F1] dark:hover:bg-zinc-900 transition-colors text-zinc-500 dark:text-zinc-400"
            aria-label="Toggle theme"
          >
            {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </aside>

      {/* Top Bar for Mobile viewports */}
      <header className="lg:hidden h-[60px] shrink-0 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-white flex items-center justify-between px-4 border-b-2 border-[#E5E5E5] dark:border-zinc-800 shadow-sm">
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="p-1.5 rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 hover:bg-zinc-50 transition-colors"
        >
          {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <span className="font-extrabold text-sm tracking-tight">{state?.project_name || 'Dashboard'}</span>
        {headerStatus.toLowerCase() === 'complete' ? (
          <div className="bg-[#E8F9D9] text-[#3D8B00] font-[800] text-[12px] px-2.5 py-1 rounded-full border-2 border-[#58CC02]/20 flex items-center space-x-1">
            <span>✅</span>
            <span>COMPLETE</span>
          </div>
        ) : (
          <div className={`px-2.5 py-0.5 rounded-full text-[9px] uppercase font-extrabold border ${headBadge.bg} ${headBadge.text} ${headBadge.border}`}>
            {getStatusLabel(headerStatus)}
          </div>
        )}
      </header>

      {/* Content wrapper */}
      <div className="flex-grow flex flex-col min-w-0 min-h-screen">

        {/* Top Header Bar on Desktop - 60px high, white background, bottom border */}
        <header className="hidden lg:flex h-[60px] shrink-0 border-b-2 border-[#E5E5E5] dark:border-zinc-850 bg-white dark:bg-zinc-950 items-center justify-between px-6 shadow-sm">
          <div className="flex items-center space-x-3">
            <div
              onClick={() => updateUrl('')}
              className="w-8 h-8 rounded-xl bg-[#58CC02] border-2 border-[#3D8B00] flex items-center justify-center text-white font-black text-lg shadow-[0_2.5px_0_#3D8B00] shrink-0 cursor-pointer"
            >
              G
            </div>
            <span onClick={() => updateUrl('')} className="font-extrabold text-lg tracking-tight text-zinc-900 dark:text-white cursor-pointer hover:text-[#58CC02] transition-colors">GoodTeam</span>
            <span className="text-zinc-300 dark:text-zinc-700 font-bold text-sm">|</span>
            <span className="text-zinc-500 dark:text-zinc-400 font-bold text-xs uppercase tracking-wider">
              {state?.project_name || startContext.project_name || 'Dashboard'}
            </span>
          </div>

          <div className="flex items-center space-x-4">
            <span className="text-[10px] font-mono font-bold text-[#AFAFAF] uppercase hidden sm:inline">Run ID: <code className="bg-[#F1F1F1] dark:bg-zinc-900 border border-[#E5E5E5] dark:border-zinc-800 px-2 py-0.5 rounded-lg text-zinc-650 dark:text-zinc-400 select-all font-mono">{state?.run_id || 'none'}</code></span>

            {headerStatus.toLowerCase() === 'complete' ? (
              <div className="bg-[#E8F9D9] text-[#3D8B00] font-[800] text-[16px] px-5 py-2.5 rounded-full border-2 border-[#58CC02]/30 flex items-center space-x-1.5 shadow-[0_2px_0_#3D8B00]/10">
                <span>✅</span>
                <span>COMPLETE</span>
              </div>
            ) : (
              <div className={`inline-flex items-center space-x-1.5 px-4 py-2 rounded-full text-xs font-extrabold uppercase border ${headBadge.bg} ${headBadge.text} ${headBadge.border}`}>
                {activeRun && <span className="h-1.5 w-1.5 bg-[#1CB0F6] rounded-full animate-pulse" />}
                <span>{getStatusLabel(headerStatus)}</span>
              </div>
            )}
          </div>
        </header>

        {/* Global Toast */}
        {message && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-2xl shadow-xl text-xs font-bold border-2 flex items-center space-x-2 animate-fade-in-down ${message.isError ? 'bg-[#FFE2E2] text-[#EA2B2B] border-[#FF4B4B]' : 'bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]'}`}>
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
              className="w-full max-w-2xl bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_8px_0_#D1D1D1] dark:shadow-[0_8px_0_#1a1a1a] rounded-2xl overflow-hidden"
            >
              <div className="px-5 py-4 bg-[#F7F7F7] dark:bg-zinc-900/80 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex items-start justify-between gap-4">
                <div>
                  <p className="text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">Memory Event</p>
                  <h3 id="memory-event-detail-title" className="font-mono text-sm font-extrabold text-[#3D8B00] dark:text-emerald-400 mt-1 select-all">
                    {memoryEventDetail.id}
                  </h3>
                </div>
                <button
                  type="button"
                  onClick={() => setMemoryEventDetail(null)}
                  className="bg-white dark:bg-zinc-850 hover:bg-[#FFE2E2] border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_3px_0_#D1D1D1] dark:shadow-[0_3px_0_#1a1a1a] text-zinc-550 hover:text-[#FF4B4B] p-2 rounded-2xl transition-all duo-button-physics"
                  aria-label="Close memory event detail"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="p-5 max-h-[70vh] overflow-y-auto">
                {memoryEventDetail.loading ? (
                  <div className="flex items-center justify-center p-10 text-zinc-500 font-bold">
                    <RefreshCw className="h-5 w-5 animate-spin mr-2 text-[#58CC02]" />
                    <span>Loading full memory event...</span>
                  </div>
                ) : memoryEventDetail.error ? (
                  <div className="bg-[#FFF0D4] dark:bg-amber-950/20 border-2 border-[#FF9600]/30 p-4 rounded-2xl text-[#B36B00] dark:text-amber-300 text-xs font-bold flex items-center space-x-2">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>{memoryEventDetail.error}</span>
                  </div>
                ) : memoryEventDetail.data ? (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[10px] font-bold">
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3">
                        <span className="block uppercase tracking-wider text-[#AFAFAF] mb-1">Role</span>
                        <span className="text-zinc-800 dark:text-zinc-200 uppercase">{memoryEventDetail.data.role || 'not recorded'}</span>
                      </div>
                      <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-100 dark:border-blue-900/50 rounded-xl p-3">
                        <span className="block uppercase tracking-wider text-blue-400 mb-1">Horizon</span>
                        <span className="text-blue-700 dark:text-blue-300 uppercase">{memoryEventDetail.data.horizon || 'not recorded'}</span>
                      </div>
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3">
                        <span className="block uppercase tracking-wider text-[#AFAFAF] mb-1">Timestamp</span>
                        <span className="font-mono text-zinc-700 dark:text-zinc-300">{memoryEventDetail.data.timestamp || 'not recorded'}</span>
                      </div>
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3 min-w-0">
                        <span className="block uppercase tracking-wider text-[#AFAFAF] mb-1">Source</span>
                        <span className="font-mono text-zinc-700 dark:text-zinc-300 break-all">{memoryEventDetail.data.source || 'not recorded'}</span>
                      </div>
                    </div>
                    {hasMemorySupersedeInfo && (
                      <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3 text-[10px] font-bold space-y-2">
                        {memoryEventDetail.data.is_superseded && (
                          <span className="inline-flex items-center bg-amber-50 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded uppercase text-[9px] border border-amber-100 dark:border-amber-900/50">
                            Superseded
                          </span>
                        )}
                        {memorySupersedes && (
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="uppercase tracking-wider text-[#AFAFAF]">Supersedes:</span>
                            {renderMemoryEventLink(memorySupersedes)}
                          </div>
                        )}
                        {memorySupersededBy.length > 0 && (
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="uppercase tracking-wider text-[#AFAFAF]">Superseded by:</span>
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
                      <span className="block text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF] mb-2">Full Event Text</span>
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

        {directoryPickerOverlay}

        {/* Main Panel Content Area - padded 32px (#F7F7F7 canvas background) */}
        <main className="flex-grow p-8 overflow-y-auto max-w-7xl w-full mx-auto space-y-6">
          {loading ? (
            <Spinner />
          ) : error ? (
            <div className="bg-[#FFE2E2] border-2 border-[#FF4B4B] p-6 rounded-2xl text-[#EA2B2B] flex items-center space-x-3 shadow-xs">
              <AlertTriangle className="h-6 w-6 shrink-0" />
              <span className="font-bold text-sm">{error}</span>
            </div>
          ) : (
            <>
              {/* Tab: Workflow Graph */}
              {activeTab === 'workflow' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Workflow Graph</h2>
                    <p className="text-xs text-[#AFAFAF]">Visual topology of pipeline nodes, decision gates, transition edges, and active execution run path.</p>
                  </div>

                  {loadingGraph ? (
                    <Spinner />
                  ) : graphError ? (
                    <div className="bg-[#FFE2E2] border-2 border-[#FF4B4B] p-6 rounded-2xl text-[#EA2B2B] flex items-center space-x-3 shadow-xs">
                      <AlertTriangle className="h-6 w-6 shrink-0" />
                      <span className="font-bold text-sm">{graphError}</span>
                    </div>
                  ) : !graphData || (
                    (Array.isArray(graphData.nodes) ? graphData.nodes : []).length === 0 &&
                    (Array.isArray(graphData.edges) ? graphData.edges : []).length === 0
                  ) ? (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs">
                      No workflow graph nodes or edges recorded for this project yet.
                    </div>
                  ) : (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-6 overflow-x-auto relative">
                      {/* Active Run Status Header */}
                      {graphData.run && (
                        <div className="mb-6 bg-zinc-50 dark:bg-zinc-900/50 p-4 rounded-xl border border-zinc-200 dark:border-zinc-800 flex flex-wrap items-center justify-between gap-4 text-xs font-bold">
                          <div className="flex items-center space-x-2">
                            <span className="text-[#AFAFAF] uppercase tracking-wider">Active Run:</span>
                            <span className="font-mono text-zinc-850 dark:text-zinc-200">{graphData.run.run_id}</span>
                          </div>
                          <div className="flex flex-wrap gap-4">
                            {graphData.run.current_node && (
                              <div className="flex items-center space-x-1.5">
                                <span className="h-2 w-2 rounded-full bg-[#58CC02] animate-pulse" />
                                <span className="text-[#AFAFAF]">Current Node:</span>
                                <span className="text-[#3D8B00] font-mono">{graphData.run.current_node}</span>
                              </div>
                            )}
                            {graphData.run.next_node && (
                              <div className="flex items-center space-x-1.5">
                                <span className="h-2 w-2 rounded-full bg-[#1CB0F6] animate-pulse" />
                                <span className="text-[#AFAFAF]">Next Node:</span>
                                <span className="text-[#0A7BAF] font-mono">{graphData.run.next_node}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* DAG Canvas Layout */}
                      {(() => {
                        const safeNodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
                        const safeEdges = Array.isArray(graphData?.edges) ? graphData.edges : [];
                        const { nodeMap, columns } = computeGraphLayout(safeNodes, safeEdges);
                        const colIndices = Object.keys(columns).map(Number).sort((a, b) => a - b);

                        const nodeWidth = 200;
                        const nodeHeight = 90;
                        const columnWidth = 270;
                        const rowHeight = 150;
                        const paddingX = 40;
                        const paddingY = 40;

                        const numCols = colIndices.length || 1;
                        const maxNodesInAnyCol = Math.max(...Object.values(columns).map(cols => cols.length), 1);

                        const canvasWidth = numCols * columnWidth + paddingX * 2;
                        const canvasHeight = maxNodesInAnyCol * rowHeight + paddingY * 2;

                        const getEdgeColor = (type) => {
                          const norm = String(type || '').toLowerCase().trim();
                          if (norm === 'approved') return '#58CC02';
                          if (norm === 'changes_requested' || norm === 'blocked') return '#FF9600';
                          if (norm === 'rejected' || norm === 'error') return '#FF4B4B';
                          return '#AFAFAF';
                        };

                        const getEdgeMarker = (type) => {
                          const norm = String(type || '').toLowerCase().trim();
                          if (['approved', 'changes_requested', 'rejected', 'error', 'blocked'].includes(norm)) {
                            return `url(#arrow-${norm})`;
                          }
                          return 'url(#arrow-default)';
                        };

                        return (
                          <div
                            className="relative select-none"
                            style={{ width: `${canvasWidth}px`, height: `${canvasHeight}px` }}
                          >
                            {/* SVG Connection Lines */}
                            <svg
                              className="absolute inset-0 pointer-events-none z-0"
                              style={{ width: '100%', height: '100%' }}
                            >
                              <defs>
                                <marker id="arrow-approved" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
                                  <path d="M0,0 L0,6 L9,3 z" fill="#58CC02" />
                                </marker>
                                <marker id="arrow-changes_requested" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
                                  <path d="M0,0 L0,6 L9,3 z" fill="#FF9600" />
                                </marker>
                                <marker id="arrow-rejected" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
                                  <path d="M0,0 L0,6 L9,3 z" fill="#FF4B4B" />
                                </marker>
                                <marker id="arrow-error" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
                                  <path d="M0,0 L0,6 L9,3 z" fill="#FF4B4B" />
                                </marker>
                                <marker id="arrow-blocked" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
                                  <path d="M0,0 L0,6 L9,3 z" fill="#FF9600" />
                                </marker>
                                <marker id="arrow-default" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
                                  <path d="M0,0 L0,6 L9,3 z" fill="#AFAFAF" />
                                </marker>
                              </defs>

                              {safeEdges.map((edge, idx) => {
                                const fromNode = nodeMap[edge.from];
                                const toNode = nodeMap[edge.to];
                                if (!fromNode || !toNode) return null;

                                const { path } = drawEdgePath(fromNode, toNode);
                                const strokeColor = getEdgeColor(edge.type);
                                const marker = getEdgeMarker(edge.type);

                                return (
                                  <path
                                    key={idx}
                                    d={path}
                                    fill="none"
                                    stroke={strokeColor}
                                    strokeWidth="2.5"
                                    markerEnd={marker}
                                    className="transition-all duration-300 opacity-80"
                                  />
                                );
                              })}
                            </svg>

                            {/* Floating Edge Labels */}
                            {safeEdges.map((edge, idx) => {
                              const fromNode = nodeMap[edge.from];
                              const toNode = nodeMap[edge.to];
                              if (!fromNode || !toNode) return null;

                              const { midX, midY } = drawEdgePath(fromNode, toNode);
                              const color = getEdgeColor(edge.type);

                              return (
                                <div
                                  key={idx}
                                  className="absolute bg-white dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded-full px-2 py-0.5 text-[9px] font-bold shadow-xs select-none pointer-events-none z-10"
                                  style={{
                                    left: `${midX}px`,
                                    top: `${midY}px`,
                                    transform: 'translate(-50%, -50%)',
                                    color: color,
                                    borderColor: `${color}40`,
                                  }}
                                >
                                  {edge.type}
                                </div>
                              );
                            })}

                            {/* Node Cards */}
                            {Object.values(nodeMap).map((node) => {
                              const isCurrent = graphData.run && graphData.run.current_node === node.name;
                              const isNext = graphData.run && graphData.run.next_node === node.name;
                              const isApproved = graphData.run && graphData.run.approvals && graphData.run.approvals[node.gate] === 'approved';

                              const left = paddingX + node.rank * columnWidth;
                              const top = paddingY + node.index * rowHeight;

                              let cardBorder = 'border-[#E5E5E5] dark:border-zinc-800';
                              let shadowStyle = 'shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a]';
                              if (isCurrent) {
                                cardBorder = 'border-[#58CC02] dark:border-[#58CC02]';
                                shadowStyle = 'shadow-[0_4px_0_#58CC02]';
                              } else if (isNext) {
                                cardBorder = 'border-[#1CB0F6] dark:border-[#1CB0F6]';
                                shadowStyle = 'shadow-[0_4px_0_#1CB0F6]';
                              }

                              return (
                                <div
                                  key={node.name}
                                  className={`absolute bg-white dark:bg-zinc-950 border-2 rounded-2xl p-4 flex flex-col justify-between z-20 ${cardBorder} ${shadowStyle}`}
                                  style={{
                                    left: `${left}px`,
                                    top: `${top}px`,
                                    width: `${nodeWidth}px`,
                                    height: `${nodeHeight}px`,
                                  }}
                                >
                                  <div className="flex justify-between items-start gap-1">
                                    <span
                                      className="font-mono text-xs font-extrabold text-zinc-850 dark:text-zinc-200 truncate select-all"
                                      title={node.name}
                                    >
                                      {node.name}
                                    </span>

                                    <div className="flex items-center space-x-1 shrink-0">
                                      {isApproved && (
                                        <span className="bg-[#E8F9D9] text-[#3D8B00] border border-[#58CC02]/20 rounded-full p-0.5" title="Approved">
                                          <Check className="h-2.5 w-2.5" />
                                        </span>
                                      )}
                                      {isCurrent ? (
                                        <span className="h-2 w-2 rounded-full bg-[#58CC02] animate-pulse" title="Current Node" />
                                      ) : isNext ? (
                                        <span className="h-2 w-2 rounded-full bg-[#1CB0F6] animate-pulse" title="Next Node" />
                                      ) : null}
                                    </div>
                                  </div>

                                  <div className="space-y-1">
                                    {node.gate && (
                                      <div className="flex items-center text-[10px] text-zinc-550 font-bold">
                                        <Lock className="h-3 w-3 mr-1 text-[#AFAFAF]" />
                                        <span className="uppercase">{node.gate} Gate</span>
                                      </div>
                                    )}
                                    <div className="flex justify-between items-center text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">
                                      <span>{node.kind}</span>
                                      {node.runner && (
                                        <span className="font-mono text-[8px] max-w-[100px] truncate" title={node.runner}>
                                          {node.runner}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                </div>
              )}

              {/* Tab: Overview */}
              {activeTab === 'overview' && (
                <div className="space-y-6">

                  {/* KPI Cards (First row: 4 KPI Cards) */}
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
                    {[
                      { label: 'Run Status', val: headerStatus, isBadge: true },
                      { label: 'Total Roles', val: String(roleObservations.length || 0), isBadge: false },
                      { label: 'Approvals', val: `${approvedCount}/${totalApprovalsCount}`, isBadge: false },
                      { label: 'Artifacts', val: String(state?.artifacts ? Object.keys(state.artifacts).length : 0), isBadge: false },
                    ].map((sig, idx) => {
                      const statBadge = getBadgeStyle(sig.val);
                      return (
                        <div key={idx} className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] p-5 rounded-2xl hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200 flex flex-col justify-between h-28">
                          <span className="text-[10px] font-bold text-[#AFAFAF] uppercase tracking-wider block">{sig.label}</span>
                          {sig.isBadge ? (
                            <span className={`inline-block px-3 py-1 rounded-full text-xs font-extrabold uppercase border w-fit ${statBadge.bg} ${statBadge.text} ${statBadge.border}`}>
                              {getStatusLabel(sig.val)}
                            </span>
                          ) : (
                            <span className="text-3xl font-extrabold text-zinc-850 dark:text-zinc-150 leading-none">{sig.val}</span>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Mission Card (Second row: CURRENT MISSION with show more/less toggle) */}
                  {state && (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-6 space-y-4">
                      <h4 className="text-[10px] font-bold text-[#AFAFAF] uppercase tracking-wider">Current Mission</h4>
                      <p className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 leading-relaxed font-mono select-all">
                        {missionExpanded ? state.idea : getMissionSummary(state.idea)}
                      </p>

                      {state.idea && state.idea.match(/[^.!?]+[.!?]+/g)?.length > 2 && (
                        <button
                          type="button"
                          onClick={() => setMissionExpanded(!missionExpanded)}
                          className="text-xs font-bold text-[#3D8B00] hover:underline flex items-center space-x-1"
                        >
                          <span>{missionExpanded ? '收起全文 ▴' : '展开全文 ▾'}</span>
                        </button>
                      )}
                    </div>
                  )}

                  {/* Blockers alert */}
                  {state?.blockers && state.blockers.length > 0 && (
                    <div className="bg-[#FFE2E2] border-2 border-[#FF4B4B] p-5 rounded-2xl shadow-[0_4px_0_#FFD1D1] space-y-4">
                      <div className="flex items-start space-x-3">
                        <AlertTriangle className="h-5.5 w-5.5 text-[#FF4B4B] shrink-0 mt-0.5" />
                        <div>
                          <h4 className="font-extrabold text-[#EA2B2B] text-sm uppercase tracking-wide">Execution Blocked</h4>
                          <ul className="list-disc pl-5 mt-2 space-y-1 text-xs text-[#EA2B2B] font-mono leading-relaxed">
                            {state.blockers.map((b, idx) => <li key={idx}>{b}</li>)}
                          </ul>
                        </div>
                      </div>
                      <div className="flex justify-end pt-3 border-t-2 border-[#FF4B4B]/10">
                        <button
                          onClick={handleRetryBlocked}
                          disabled={actionLoading}
                          className="bg-[#FF9600] border-2 border-[#B36B00] shadow-[0_3px_0_#B36B00] text-white font-bold text-xs uppercase tracking-wider px-4 py-2 rounded-2xl transition-all duo-button-physics flex items-center space-x-2"
                        >
                          <RefreshCw className="h-3.5 w-3.5 animate-pulse" />
                          <span>Retry Blocked Workers</span>
                        </button>
                      </div>
                    </div>
                  )}

                  {/* CTA row (Third row: 2 CTA buttons) */}
                  <div className="flex space-x-4">
                    <button
                      onClick={() => updateUrl('')}
                      className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_4px_0_#3D8B00] text-white rounded-2xl font-bold uppercase tracking-wider text-xs px-5 py-3 transition-all duo-button-physics"
                    >
                      Open Project Overview
                    </button>
                    <button
                      onClick={() => setActiveTab('approvals')}
                      className="bg-white dark:bg-zinc-850 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] text-zinc-700 dark:text-zinc-350 rounded-2xl font-bold uppercase tracking-wider text-xs px-5 py-3 transition-all duo-button-physics"
                    >
                      Review Release Notes
                    </button>
                  </div>

                  {/* Configuration Details collapsible */}
                  {state && (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl overflow-hidden">
                      <div className="px-6 py-4 bg-zinc-50 dark:bg-zinc-900/50 border-b-2 border-[#E5E5E5] dark:border-zinc-800">
                        <h3 className="font-extrabold text-xs text-[#AFAFAF] uppercase tracking-wider">Run Environment Configuration</h3>
                      </div>
                      <div className="p-6">
                        <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4 text-xs font-semibold">
                          <div className="border-b border-zinc-100 dark:border-zinc-900 pb-2 flex justify-between items-center">
                            <dt className="text-[#AFAFAF] uppercase text-[10px]">State Config Path</dt>
                            <dd><FilePath path={state.context_packet_path} segments={1} /></dd>
                          </div>
                          <div className="border-b border-zinc-100 dark:border-zinc-900 pb-2 flex justify-between items-center">
                            <dt className="text-[#AFAFAF] uppercase text-[10px]">Local Task Ledger</dt>
                            <dd><FilePath path={state.artifacts?.task_ledger} segments={1} /></dd>
                          </div>
                          <div className="border-b border-zinc-100 dark:border-zinc-900 pb-2 flex justify-between items-center">
                            <dt className="text-[#AFAFAF] uppercase text-[10px]">Docs Repository</dt>
                            <dd><FilePath path={startContext.project_report?.project?.repos?.docs} segments={1} /></dd>
                          </div>
                          <div className="border-b border-zinc-100 dark:border-zinc-900 pb-2 flex justify-between items-center">
                            <dt className="text-[#AFAFAF] uppercase text-[10px]">Backend Repository</dt>
                            <dd><FilePath path={startContext.project_report?.project?.repos?.backend} segments={1} /></dd>
                          </div>
                          <div className="border-b border-zinc-100 dark:border-zinc-900 pb-2 flex justify-between items-center">
                            <dt className="text-[#AFAFAF] uppercase text-[10px]">Frontend Repository</dt>
                            <dd><FilePath path={startContext.project_report?.project?.repos?.frontend} segments={1} /></dd>
                          </div>
                          <div className="border-b border-zinc-100 dark:border-zinc-900 pb-2 flex justify-between items-center">
                            <dt className="text-[#AFAFAF] uppercase text-[10px]">Staging SSH Target</dt>
                            <dd className="font-mono text-zinc-700 dark:text-zinc-350">{startContext.deployment_backend?.staging_ssh_target || 'not configured'}</dd>
                          </div>
                        </dl>
                      </div>
                    </div>
                  )}

                  {/* Start another workflow console (Duo Card style) */}
                  {!activeRun && (
                    <details className="group border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] rounded-2xl overflow-hidden bg-white dark:bg-zinc-950">
                      <summary className="px-6 py-4 font-bold text-xs uppercase tracking-wider text-[#3D8B00] hover:bg-zinc-55 hover:bg-zinc-50 transition-colors cursor-pointer list-none flex justify-between items-center select-none">
                        <span>Start another run for this project</span>
                        <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180 text-zinc-500" />
                      </summary>
                      <div className="p-6 border-t-2 border-[#E5E5E5] dark:border-zinc-800 space-y-6">
                        <div className="flex bg-[#F1F1F1] dark:bg-zinc-900 p-1 rounded-xl w-fit text-xs mb-4">
                          <button onClick={() => setStartMode('existing_project')} className={`px-3 py-1.5 rounded-lg font-bold transition-all ${startMode === 'existing_project' ? 'bg-white dark:bg-zinc-850 shadow-sm text-[#3D8B00]' : 'text-[#AFAFAF] hover:text-zinc-800'}`}>Adopt Existing Project</button>
                          <button onClick={() => setStartMode('new_project')} className={`px-3 py-1.5 rounded-lg font-bold transition-all ${isNewProjectMode ? 'bg-white dark:bg-zinc-850 shadow-sm text-[#3D8B00]' : 'text-[#AFAFAF] hover:text-zinc-850'}`}>Start New Project</button>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-bold">
                          <div className="col-span-2">
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Goal / Idea</label>
                            <textarea value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="Explain the workflow target..." className="w-full h-20 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-3 font-mono leading-relaxed" />
                          </div>
                          <div>
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Model Profile</label>
                            <select value={modelProfile} onChange={(e) => setModelProfile(e.target.value)} className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-3 py-2">
                              <option value="balanced">Balanced</option>
                              <option value="cheap">Cheap</option>
                              <option value="max_quality">Max Quality</option>
                            </select>
                          </div>
                          <div>
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Delivery Mode</label>
                            <select value={deliveryMode} onChange={(e) => setDeliveryMode(e.target.value)} className="w-full bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl px-3 py-2">
                              {DELIVERY_MODE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                              ))}
                            </select>
                          </div>
                          {deliveryMode === 'custom' && (
                            <div className="col-span-2">
                              <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Delivery Standards</label>
                              <textarea
                                value={deliveryStandards}
                                onChange={(e) => setDeliveryStandards(e.target.value)}
                                placeholder="每行一条交付标准"
                                className="w-full h-20 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-3 font-mono leading-relaxed resize-none"
                              />
                            </div>
                          )}

                          <div className="col-span-2 border-t-2 border-[#E5E5E5] dark:border-zinc-850 my-2" />

                           <div className="col-span-2 flex items-center justify-between">
                            <div className="flex items-center space-x-2">
                              <input type="checkbox" id="run-dry" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="rounded text-[#58CC02] focus:ring-[#58CC02] h-4 w-4 cursor-pointer" />
                              <label htmlFor="run-dry" className="font-bold text-zinc-500 uppercase tracking-wider text-[10px]">Dry run simulation</label>
                            </div>
                            <div className="flex flex-col items-end">
                              <button
                                onClick={handleStartWorkflow}
                                disabled={actionLoading || startingWorkflow}
                                className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold px-6 py-2.5 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-2"
                              >
                                {startingWorkflow ? (
                                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <Play className="h-3.5 w-3.5 fill-current" />
                                )}
                                <span>{startingWorkflow ? 'Starting...' : (dryRun ? 'Preview Workflow' : 'Start Workflow')}</span>
                              </button>
                              {startingWorkflow && (
                                <span className="text-[10px] text-zinc-500 mt-2 text-right animate-pulse max-w-xs">
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
              )}

              {/* Tab: Roles */}
              {activeTab === 'roles' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Role Observability</h2>
                    <p className="text-xs text-[#AFAFAF]">Overview of AI agents configured for this workflow execution loop.</p>
                  </div>

                  {/* Grid layout for 9 roles: 3 cols wide, 2 cols medium, rounded-2xl white cards */}
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {roleObservations.map((role) => {
                      const badge = getBadgeStyle(role.status);
                      return (
                        <div
                          key={role.name}
                          className={`bg-white dark:bg-zinc-950 border-2 rounded-2xl shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] p-5 space-y-4 hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200 flex flex-col justify-between ${role.status === 'Waiting for approval' ? 'border-[#FF9600]' : role.status === 'Ready' ? 'border-[#58CC02]' : 'border-[#E5E5E5] dark:border-zinc-800'}`}
                        >
                          <div className="space-y-3">
                            <div className="flex justify-between items-start gap-2">
                              <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 leading-tight">{role.name}</h3>
                              <span className={`inline-block px-2.5 py-0.5 rounded-full text-[9px] font-extrabold border uppercase tracking-wider shrink-0 ${badge.bg} ${badge.text} ${badge.border}`}>
                                {getStatusLabel(role.status)}
                              </span>
                            </div>
                            <p className="text-xs font-semibold text-zinc-550 leading-relaxed h-12 overflow-y-auto">{role.description}</p>
                          </div>

                          <div className="border-t-2 border-[#F1F1F1] dark:border-zinc-900 pt-3 space-y-2 text-[10px] font-bold">
                            <div className="grid grid-cols-2 gap-x-2">
                              <div>
                                <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px] block mb-0.5">Model</span>
                                <span className="font-mono text-zinc-700 dark:text-zinc-300 truncate block">{role.model}</span>
                              </div>
                              <div>
                                <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px] block mb-0.5">Mode</span>
                                <span className="text-zinc-700 dark:text-zinc-300 capitalize">{role.mode}</span>
                              </div>
                            </div>

                            {role.artifact && (
                              <div className="pt-1 flex justify-between items-center gap-2">
                                <div>
                                  <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px] block">Target Artifact</span>
                                  <span className="text-[#58CC02] uppercase font-extrabold text-[10px]">{role.artifact}</span>
                                </div>
                                <FilePath path={role.artifact_path} segments={1} />
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Tab: Artifacts */}
              {activeTab === 'artifacts' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">State Artifacts</h2>
                    <p className="text-xs text-[#AFAFAF]">Durable ledger file records generated and updated by this run.</p>
                  </div>

                  {/* 4-column card grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                    {state?.artifacts && Object.entries(state.artifacts).map(([name, path]) => (
                      <div
                        key={name}
                        className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] p-4 rounded-2xl flex items-center justify-between hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200"
                      >
                        <div className="flex items-center space-x-3 overflow-hidden">
                          <FileText className="h-6 w-6 text-[#1CB0F6] shrink-0" />
                          <div className="overflow-hidden">
                            <span className="font-extrabold text-[9px] uppercase tracking-widest text-[#AFAFAF] block mb-0.5">{name}</span>
                            <FilePath path={path} segments={1} />
                          </div>
                        </div>
                        <button
                          onClick={() => handleEditArtifact(name)}
                          className="bg-zinc-50 hover:bg-[#D0F0FF] border-2 border-[#E5E5E5] hover:border-[#1CB0F6] p-1.5 rounded-xl transition-all duo-button-physics shrink-0 text-zinc-500 hover:text-[#0A7BAF]"
                          title="Open artifact"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                    {(!state?.artifacts || Object.keys(state.artifacts).length === 0) && (
                      <div className="col-span-4 border-2 border-dashed border-[#E5E5E5] rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs">
                        No artifacts recorded in this state yet.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tab: Actions */}
              {activeTab === 'actions' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Execution Logs</h2>
                    <p className="text-xs text-[#AFAFAF]">Chronological list of all worker dispatches, approvals, and commits.</p>
                  </div>

                  {/* Timeline layout: 2px vertical line on left */}
                  <div className="relative border-l-2 border-[#E5E5E5] dark:border-zinc-800 ml-4 pl-6 space-y-6">
                    {actionItems.map((action, idx) => {
                      // system = blue, human = green, error = red
                      const isHuman = action.actor === 'human';
                      const isSystem = action.actor === 'system';
                      const dotColor = isHuman ? 'bg-[#58CC02] border-[#3D8B00]' : isSystem ? 'bg-[#1CB0F6] border-[#0A7BAF]' : 'bg-[#FF4B4B] border-[#EA2B2B]';

                      return (
                        <div key={idx} className="relative group">
                          {/* Dot indicator node */}
                          <span className={`absolute -left-[31px] top-1.5 h-3.5 w-3.5 rounded-full border-2 ${dotColor}`} />

                          <article className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-5 hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200 space-y-2.5 max-w-3xl">
                            <div className="flex justify-between items-center gap-2">
                              <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 leading-tight">{action.title}</h3>
                              <span className={`inline-block text-[9px] px-2 py-0.5 rounded-full font-extrabold border uppercase tracking-wider leading-none ${isHuman ? 'bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]/30' : 'bg-[#D0F0FF] text-[#0A7BAF] border-[#1CB0F6]/30'}`}>
                                {action.actor}
                              </span>
                            </div>

                            <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[10px] text-zinc-500 font-semibold uppercase tracking-wider">
                              <span>Action: <span className="font-bold text-zinc-700 dark:text-zinc-300 font-mono text-[9px]">{action.action}</span></span>
                              {action.gate && <span>Gate: <span className="font-bold text-zinc-700 dark:text-zinc-300">{action.gate}</span></span>}
                              {action.decision && <span>Decision: <span className="font-bold text-zinc-700 dark:text-zinc-300">{action.decision}</span></span>}
                              {action.node_transition && <span>Node: <span className="font-bold text-zinc-700 dark:text-zinc-300 font-mono text-[9px]">{action.node_transition}</span></span>}
                            </div>

                            {action.artifact_path && (
                              <div className="pt-1.5 border-t border-[#F1F1F1] dark:border-zinc-900 flex items-center space-x-2 text-[10px] font-bold">
                                <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px]">{action.artifact || 'Artifact'}</span>
                                <FilePath path={action.artifact_path} segments={1} />
                              </div>
                            )}

                            <div className="text-[9px] font-mono font-bold text-[#AFAFAF] text-right">
                              {action.timestamp ? formatHumanTime(action.timestamp) : ''}
                            </div>
                          </article>
                        </div>
                      );
                    })}

                    {actionItems.length === 0 && (
                      <div className="border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs max-w-3xl">
                        No actions logged in this run.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tab: Agile Board */}
              {activeTab === 'board' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Agile Board</h2>
                    <p className="text-xs text-[#AFAFAF]">Real-time projection of task ledger task items.</p>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-4 gap-6 items-start font-semibold">
                    {['backlog', 'doing', 'blocked', 'done'].map((columnKey) => {
                      const columns = agileBoard.columns || {};
                      const counts = agileBoard.counts || {};
                      const columnTasks = columns[columnKey] || [];

                      const columnLabel = {
                        backlog: 'Backlog',
                        doing: 'Doing',
                        blocked: 'Blocked',
                        done: 'Done',
                      }[columnKey];

                      // Blocked: Orange, Done: Green, columns heads matching Duo opacity badges
                      const headStyles = {
                        backlog: 'bg-[#F1F1F1] text-[#777777] border-2 border-[#AFAFAF]/20',
                        doing: 'bg-[#D0F0FF] text-[#0A7BAF] border-2 border-[#1CB0F6]/20',
                        blocked: 'bg-[#FFF0D4] text-[#B36B00] border-2 border-[#FF9600]/20',
                        done: 'bg-[#E8F9D9] text-[#3D8B00] border-2 border-[#58CC02]/20',
                      }[columnKey];

                      return (
                        <section
                          key={columnKey}
                          className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-4 flex flex-col min-h-[420px]"
                        >
                          <div className={`flex justify-between items-center px-3 py-2 rounded-xl mb-4 font-extrabold text-xs uppercase tracking-wider leading-none ${headStyles}`}>
                            <span>{columnLabel}</span>
                            <span className="text-[10px] leading-none px-2 py-0.5 rounded-full bg-white/70 dark:bg-black/20 font-extrabold">
                              {counts[columnKey] || 0}
                            </span>
                          </div>

                          <div className="flex-grow space-y-3">
                            {columnTasks.map((task) => (
                              <article
                                key={task.id}
                                className="bg-[#F7F7F7] dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl p-3.5 shadow-xs space-y-3 hover:-translate-y-0.5 hover:shadow-sm transition-all group"
                              >
                                <div className="flex justify-between items-start gap-1">
                                  <span className="font-mono text-[9px] text-[#AFAFAF] uppercase tracking-wide font-extrabold select-all leading-none">{task.id}</span>
                                  <span className={`inline-block px-1.5 py-0.5 rounded-md text-[8px] font-extrabold uppercase leading-none ${task.priority === 'high' ? 'bg-[#FFE2E2] text-[#EA2B2B]' : 'bg-[#F1F1F1] text-[#777777]'}`}>
                                    {task.priority}
                                  </span>
                                </div>

                                <div>
                                  <h4 className="font-bold text-xs text-zinc-850 dark:text-zinc-200 leading-snug">{task.title}</h4>
                                  <p className="text-[10px] font-semibold text-zinc-500 mt-1 leading-relaxed">{task.description}</p>
                                </div>

                                {task.acceptance_criteria && task.acceptance_criteria.length > 0 && (
                                  <div className="pt-2 border-t border-zinc-200 dark:border-zinc-800 text-[9px] space-y-1">
                                    <span className="font-bold text-[#AFAFAF] block uppercase tracking-wider">Criteria</span>
                                    <ul className="list-disc pl-3 text-zinc-500 space-y-0.5 leading-snug">
                                      {task.acceptance_criteria.map((c, cIdx) => <li key={cIdx}>{c}</li>)}
                                    </ul>
                                  </div>
                                )}

                                <div className="flex justify-between items-center text-[9px] pt-1 leading-none font-bold">
                                  <span className="text-[#AFAFAF] uppercase">Actor: <span className="text-zinc-700 dark:text-zinc-300 font-extrabold">{task.role}</span></span>
                                  <span className="text-[#AFAFAF] font-mono">{task.updated_at ? new Date(task.updated_at).toLocaleDateString() : ''}</span>
                                </div>
                              </article>
                            ))}

                            {columnTasks.length === 0 && (
                              <div className="border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-xl p-6 text-center text-[#AFAFAF] text-[10px] flex flex-col items-center justify-center space-y-1 select-none">
                                <Info className="h-4 w-4 text-zinc-300" />
                                <span>暂无任务</span>
                              </div>
                            )}
                          </div>
                        </section>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Tab: Smoke Runs */}
              {activeTab === 'smoke' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Smoke Test Reports</h2>
                    <p className="text-xs text-[#AFAFAF]">Verify codebase status and health against configured suites.</p>
                  </div>

                  <div className="space-y-4 max-w-4xl">
                    {smokeRunItems.map((item, idx) => {
                      const isPass = item.status === 'pass';
                      const isFail = item.status === 'fail';
                      const cardBorder = isPass ? 'border-[#58CC02]' : isFail ? 'border-[#FF4B4B]' : 'border-[#E5E5E5]';
                      const badge = getBadgeStyle(item.status);

                      return (
                        <article
                          key={idx}
                          className={`bg-white dark:bg-zinc-950 border-2 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-5 hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 ${cardBorder}`}
                        >
                          <div className="space-y-2">
                            <div className="flex items-center space-x-2.5">
                              <span className={`h-2.5 w-2.5 rounded-full shrink-0 ${isPass ? 'bg-[#58CC02]' : isFail ? 'bg-[#FF4B4B] animate-pulse' : 'bg-zinc-400'}`} />
                              <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200">Cycle {item.cycle} - {item.project_key}</h3>
                              <span className={`inline-block text-[9px] px-2 py-0.5 rounded-full font-extrabold border uppercase tracking-wider leading-none ${badge.bg} ${badge.text} ${badge.border}`}>
                                {getStatusLabel(item.status)}
                              </span>
                            </div>

                            <dl className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-[10px] text-zinc-550 font-bold uppercase tracking-wider pt-1.5">
                              <div>
                                <dt className="text-[#AFAFAF] mb-0.5">Test Suite</dt>
                                <dd className="font-mono text-zinc-700 dark:text-zinc-300 font-extrabold text-[9px]">{item.suite}</dd>
                              </div>
                              {item.classification && (
                                <div>
                                  <dt className="text-[#AFAFAF] mb-0.5">Classification</dt>
                                  <dd className="font-mono text-[#EA2B2B] dark:text-red-400 font-extrabold">{item.classification}</dd>
                                </div>
                              )}
                              {item.next_task && (
                                <div className="col-span-2">
                                  <dt className="text-[#AFAFAF] mb-0.5">Recovery Task</dt>
                                  <dd className="text-zinc-700 dark:text-zinc-300 truncate max-w-xs font-sans normal-case text-xs font-semibold" title={item.next_task}>{item.next_task}</dd>
                                </div>
                              )}
                            </dl>

                            {item.report_path && (
                              <div className="pt-2 flex items-center space-x-2 text-[9px] font-bold">
                                <span className="text-[#AFAFAF] uppercase tracking-wider">Report:</span>
                                <FilePath path={item.report_path} segments={1} />
                              </div>
                            )}
                          </div>
                        </article>
                      );
                    })}

                    {smokeRunItems.length === 0 && (
                      <div className="bg-white dark:bg-zinc-950 border-2 border-zinc-200 dark:border-zinc-800 p-12 text-center rounded-2xl text-zinc-550 italic text-xs">
                        No smoke run test reports recorded yet.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tab: Workers */}
              {activeTab === 'workers' && (
                <div className="space-y-6">
                  {/* Worker Backends config panel */}
                  <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] p-6 rounded-2xl space-y-6">
                    <div className="border-b border-[#F1F1F1] dark:border-zinc-900 pb-3">
                      <h3 className="text-sm font-extrabold text-zinc-800 dark:text-zinc-200">Worker Routing Configuration</h3>
                      <p className="text-xs text-[#AFAFAF] mt-0.5">Configure which agent CLI execution backends are used for each project role.</p>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                      {/* Left Column: Form Controls */}
                      <div className="space-y-4">
                        {/* Global default selection */}
                        <div>
                          <label className="block text-[10px] text-zinc-550 mb-1.5 uppercase font-bold tracking-wider">Global Default Mode</label>
                          <select
                            value={panelGlobalMode}
                            onChange={(e) => setPanelGlobalMode(e.target.value)}
                            disabled={savingBackends || actionLoading}
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 text-xs font-bold focus:outline-none focus:border-[#58CC02] transition-colors"
                          >
                            {backendOptions.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt === 'codex' ? 'Codex CLI' : opt === 'anthropic' || opt === 'claude' ? 'Claude CLI' : opt}
                              </option>
                            ))}
                          </select>
                        </div>

                        {/* Role configurations */}
                        <div className="space-y-3">
                          <span className="block text-[10px] text-zinc-550 uppercase font-bold tracking-wider">Per-Role Routing Overrides</span>
                          {activeTab === 'workers' && loadingDiscoveredAgents && (
                            <p className="text-[10px] text-[#AFAFAF] italic">Loading discovered agents...</p>
                          )}
                          {discoveredAgents !== null && safeDiscoveredAgents.length > 0 && (
                            <div className="flex flex-wrap gap-1.5">
                              {safeDiscoveredAgents.map((agent) => (
                                <span
                                  key={agent.backend}
                                  className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[9px] font-extrabold uppercase tracking-wider ${
                                    agent.ready
                                      ? 'bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]/30'
                                      : 'bg-[#FFF0D4] text-[#B36B00] border-[#FF9600]/30'
                                  }`}
                                  title={agent.hint || ''}
                                >
                                  {agent.backend} {agent.ready ? '✓ ready' : '⚠ not-ready'}
                                </span>
                              ))}
                            </div>
                          )}
                          {workerRoles.map((role) => {
                            const isConfiguring = configuringRoles[role];
                            const selectedBackend = panelRoleOverrides[role] || '';
                            const effectiveBackend = selectedBackend || panelGlobalMode;
                            const selectedModel = panelRoleModels[role] || '';

                            const selectedAgent = effectiveBackend ? discoveredAgentByBackend[effectiveBackend] : null;
                            const isAgy = effectiveBackend === 'agy' || effectiveBackend === 'antigravity';
                            const isBuiltin = ['fake', 'noop', 'human'].includes(effectiveBackend);
                            const hasModel = effectiveBackend && !isAgy && !isBuiltin;

                            return (
                              <div key={role} className="border-b border-[#F1F1F1] dark:border-zinc-900 pb-3.5 pt-1">
                                <div className="flex items-center justify-between space-x-4">
                                  <div className="space-y-1">
                                    <span className="text-xs font-bold text-zinc-700 dark:text-zinc-300 capitalize">{role} Role</span>
                                    <div className="text-[11px] text-[#AFAFAF] dark:text-zinc-550 flex items-center space-x-1.5 flex-wrap">
                                      <span className="font-semibold text-zinc-600 dark:text-zinc-400">
                                        {selectedBackend ? getBackendOptionLabel(selectedBackend) : `Inherit default (${getBackendOptionLabel(panelGlobalMode)})`}
                                      </span>
                                      {hasModel && (
                                        <>
                                          <span className="text-zinc-300 dark:text-zinc-700">•</span>
                                          <span className="bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 px-1.5 py-0.5 rounded font-mono text-[10px]">
                                            {selectedModel || 'default'}
                                          </span>
                                        </>
                                      )}
                                      {isAgy && (
                                        <>
                                          <span className="text-zinc-300 dark:text-zinc-700">•</span>
                                          <span className="italic text-zinc-500 dark:text-zinc-500">model N/A</span>
                                        </>
                                      )}
                                    </div>
                                  </div>

                                  <button
                                    type="button"
                                    onClick={() => setConfiguringRoles(prev => ({ ...prev, [role]: !prev[role] }))}
                                    className={`px-3 py-1.5 border-2 rounded-xl text-xs font-bold transition-all flex items-center space-x-1 ${
                                      isConfiguring
                                        ? 'bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300'
                                        : 'bg-white dark:bg-zinc-900 border-[#E5E5E5] dark:border-zinc-800 hover:border-[#58CC02] text-zinc-700 dark:text-zinc-300'
                                    }`}
                                  >
                                    <span>{isConfiguring ? 'Close' : 'Configure'}</span>
                                  </button>
                                </div>

                                {isConfiguring && (
                                  <div className="mt-3 bg-zinc-50/50 dark:bg-zinc-900/10 border border-[#F1F1F1] dark:border-zinc-800/80 rounded-xl p-4 space-y-4">
                                    <div>
                                      <label className="block text-[10px] text-zinc-500 dark:text-zinc-400 mb-1.5 uppercase font-bold tracking-wider">
                                        Step 1: Select Agent Backend
                                      </label>
                                      <select
                                        value={selectedBackend}
                                        onChange={(e) => handleAgentChange(role, e.target.value)}
                                        disabled={savingBackends || actionLoading}
                                        className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 text-xs font-bold focus:outline-none focus:border-[#58CC02] transition-colors"
                                      >
                                        <option value="">Use default</option>
                                        {roleBackendOptions.map((opt) => (
                                          <option key={opt} value={opt}>
                                            {getBackendOptionLabel(opt)}
                                          </option>
                                        ))}
                                      </select>
                                      {selectedAgent && (
                                        <p className="mt-1 text-[10px] text-zinc-550 dark:text-zinc-500 font-medium leading-relaxed">
                                          {selectedAgent.ready ? '✓ Ready' : '⚠ Not ready'}
                                          {selectedAgent.hint ? ` - ${selectedAgent.hint}` : ''}
                                        </p>
                                      )}
                                    </div>

                                    {effectiveBackend && (
                                      <div className="border-t border-[#F1F1F1] dark:border-zinc-900/60 pt-3">
                                        {isAgy ? (
                                          <div className="text-[11px] text-zinc-550 dark:text-zinc-500 font-medium bg-zinc-100/50 dark:bg-zinc-900/40 border border-zinc-200/50 dark:border-zinc-800 px-3 py-2 rounded-lg italic">
                                            Model is not applicable for Antigravity (agy) CLI.
                                          </div>
                                        ) : isBuiltin ? (
                                          <div className="text-[11px] text-zinc-550 dark:text-zinc-500 font-medium bg-zinc-100/50 dark:bg-zinc-900/40 border border-zinc-200/50 dark:border-zinc-800 px-3 py-2 rounded-lg italic">
                                            Model selection not applicable for built-in backends.
                                          </div>
                                        ) : (
                                          <div className="space-y-3">
                                            <div>
                                              <label className="block text-[10px] text-zinc-550 dark:text-zinc-400 mb-1.5 uppercase font-bold tracking-wider">
                                                Step 2: Select Model
                                              </label>
                                              {(() => {
                                                const agentInfo = discoveredAgentByBackend[effectiveBackend];
                                                const modelsList = agentInfo?.models || FALLBACK_AGENT_MODES[effectiveBackend]?.models || ['auto'];
                                                const defaultModel = agentInfo?.model_default || FALLBACK_AGENT_MODES[effectiveBackend]?.model_default || 'auto';
                                                const isCustom = customModelSelected[role] || Boolean(selectedModel && !modelsList.includes(selectedModel));
                                                const dropdownVal = isCustom ? '__custom__' : (modelsList.includes(selectedModel) ? selectedModel : (selectedModel ? '__custom__' : defaultModel));

                                                return (
                                                  <div className="space-y-3">
                                                    <select
                                                      value={dropdownVal}
                                                      onChange={(e) => handleModelDropdownChange(role, e.target.value)}
                                                      disabled={savingBackends || actionLoading}
                                                      className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 text-xs font-bold focus:outline-none focus:border-[#58CC02] transition-colors"
                                                    >
                                                      {modelsList.map((m) => (
                                                        <option key={m} value={m}>
                                                          {m === defaultModel ? `${m} (Default)` : m}
                                                        </option>
                                                      ))}
                                                      <option value="__custom__">Custom...</option>
                                                    </select>

                                                    {isCustom && (
                                                      <div className="space-y-1">
                                                        <label className="block text-[9px] text-zinc-550 dark:text-zinc-400 uppercase font-bold tracking-wider">
                                                          Custom Model Name
                                                        </label>
                                                        <input
                                                          type="text"
                                                          placeholder="e.g. gpt-5.5-custom"
                                                          value={selectedModel}
                                                          onChange={(e) => handleCustomModelChange(role, e.target.value)}
                                                          disabled={savingBackends || actionLoading}
                                                          className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 text-xs font-bold font-mono focus:outline-none focus:border-[#58CC02] transition-colors"
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
                          })}
                        </div>

                        {/* Save Button & Messages */}
                        <div className="pt-2 space-y-3">
                          {backendsError && (
                            <div className="p-3 bg-[#FFE2E2] dark:bg-red-950/20 border border-[#FF4B4B]/30 rounded-xl text-xs font-bold text-[#EA2B2B] dark:text-red-400 flex items-center space-x-2">
                              <AlertTriangle className="h-4 w-4 shrink-0" />
                              <span>{backendsError}</span>
                            </div>
                          )}
                          {backendsSuccess && (
                            <div className="p-3 bg-[#E8F9D9] dark:bg-[#E8F9D9]/10 border border-[#58CC02]/30 rounded-xl text-xs font-bold text-[#3D8B00] dark:text-emerald-400 flex items-center space-x-2">
                              <CheckCircle2 className="h-4 w-4 shrink-0" />
                              <span>{backendsSuccess}</span>
                            </div>
                          )}
                          <div className="flex justify-end">
                            <button
                              onClick={handleSaveWorkerBackends}
                              disabled={savingBackends || actionLoading}
                              className="bg-[#58CC02] hover:bg-[#46A302] text-white border-b-4 border-[#3D8B00] active:border-b-0 active:translate-y-1 px-5 py-2 rounded-xl text-xs font-extrabold tracking-wide uppercase transition-all flex items-center space-x-2"
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

                      {/* Right Column: Preflight / Readiness Badges */}
                      <div className="space-y-4 border-t lg:border-t-0 lg:border-l border-[#F1F1F1] dark:border-zinc-900 lg:pl-8 pt-4 lg:pt-0">
                        <div className="font-extrabold text-[10px] text-[#AFAFAF] uppercase tracking-wider">Backend Readiness Preflight</div>

                        <div className="space-y-3.5">
                          {usedBackends.map((backend) => {
                            const preflight = startContext.project_report?.worker_backends_preflight?.[backend] || {};
                            const isReady = preflight.ready ?? false;
                            const badge = getBadgeStyle(isReady ? 'success' : 'error');
                            const Icon = badge.icon;

                            return (
                              <div key={backend} className="bg-zinc-50 dark:bg-zinc-900/30 border border-[#E5E5E5] dark:border-zinc-800 rounded-xl p-3.5 space-y-2">
                                <div className="flex justify-between items-center">
                                  <span className="text-xs font-extrabold text-zinc-800 dark:text-zinc-200 capitalize">
                                    {backend === 'codex' ? 'Codex CLI' : backend === 'anthropic' || backend === 'claude' ? 'Claude CLI' : backend}
                                    {backend === panelGlobalMode && <span className="text-[9px] text-[#AFAFAF] font-normal ml-1.5 lowercase">(global default)</span>}
                                  </span>

                                  <span className={`inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[9px] font-extrabold border uppercase tracking-wider ${badge.bg} ${badge.text} ${badge.border}`}>
                                    <Icon className="h-3 w-3" />
                                    <span>{isReady ? 'Ready' : 'Not Ready'}</span>
                                  </span>
                                </div>

                                {preflight.hint ? (
                                  <p className="text-[10px] text-zinc-550 dark:text-zinc-400 font-medium leading-relaxed bg-white dark:bg-zinc-950/40 p-2 rounded-lg border border-[#F1F1F1] dark:border-zinc-900 font-mono select-all">
                                    {preflight.hint}
                                  </p>
                                ) : (
                                  <p className="text-[10px] text-[#AFAFAF] italic font-medium leading-relaxed">
                                    No additional hints or warnings available.
                                  </p>
                                )}
                              </div>
                            );
                          })}
                          {usedBackends.length === 0 && (
                            <div className="text-xs text-[#AFAFAF] italic">
                              No backends are currently configured or in use.
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Worker Jobs</h2>
                    <p className="text-xs text-[#AFAFAF]">Live observation of background Codex CLI and agent executors.</p>
                  </div>

                  {/* Worker job list in a card grid */}
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {workerJobs.map((job) => {
                      const isCompleted = job.status === 'completed';
                      const isRunning = job.status === 'running';
                      const isZeroExit = String(job.summary || '').includes('exit code 0') || isCompleted; // check exit code

                      return (
                        <div
                          key={job.id}
                          className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] p-5 rounded-2xl hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200 space-y-3"
                        >
                          <div className="flex justify-between items-center border-b border-[#F1F1F1] dark:border-zinc-900 pb-2">
                            <div>
                              <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 leading-snug">{job.role}</h3>
                              <span className="text-[10px] text-[#AFAFAF] font-bold capitalize tracking-wide">{job.adapter} adapter</span>
                            </div>

                            <div className="flex items-center space-x-1.5 shrink-0">
                              {isCompleted ? (
                                <span className="bg-[#E8F9D9] text-[#3D8B00] border border-[#58CC02]/20 font-extrabold text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full flex items-center space-x-1 select-none">
                                  <Check className="h-3 w-3" />
                                  <span>Done</span>
                                </span>
                              ) : isRunning ? (
                                <span className="bg-[#D0F0FF] text-[#0A7BAF] border border-[#1CB0F6]/20 font-extrabold text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full flex items-center space-x-1 animate-pulse select-none">
                                  <RefreshCw className="h-2.5 w-2.5 animate-spin" />
                                  <span>Running</span>
                                </span>
                              ) : (
                                <span className="bg-[#FFF0D4] text-[#B36B00] border border-[#FF9600]/20 font-extrabold text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full select-none">
                                  {job.status}
                                </span>
                              )}
                            </div>
                          </div>

                          <div className="font-mono text-[10px] font-bold text-zinc-500 space-y-1.5">
                            <div className="flex justify-between items-center">
                              <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px]">Job ID</span>
                              <span className="select-all text-zinc-700 dark:text-zinc-300">{job.id.substring(0, 12)}...{job.id.slice(-6)}</span>
                            </div>
                            <div className="flex justify-between items-center">
                              <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px]">PID</span>
                              <span className="text-zinc-700 dark:text-zinc-300 font-bold">{job.pid || 'n/a'}</span>
                            </div>
                            <div className="flex justify-between items-center">
                              <span className="text-[#AFAFAF] uppercase tracking-wider text-[8px]">Heartbeat</span>
                              <span className="text-zinc-750 dark:text-zinc-300">{job.heartbeat_at ? new Date(job.heartbeat_at).toLocaleTimeString() : 'none'}</span>
                            </div>

                            <div className="border-t border-[#F1F1F1] dark:border-zinc-900 pt-2 font-sans normal-case text-xs font-semibold leading-relaxed flex items-start space-x-2 text-zinc-700 dark:text-zinc-300">
                              {isCompleted && isZeroExit ? (
                                <CheckCircle className="h-4 w-4 text-[#58CC02] shrink-0 mt-0.5" />
                              ) : isCompleted ? (
                                <XCircle className="h-4 w-4 text-[#FF4B4B] shrink-0 mt-0.5" />
                              ) : (
                                <Info className="h-4 w-4 text-[#1CB0F6] shrink-0 mt-0.5" />
                              )}
                              <span className="truncate" title={job.summary}>{job.summary || 'Executing task...'}</span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                    {workerJobs.length === 0 && (
                      <div className="col-span-3 border-2 border-dashed border-[#E5E5E5] rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs">
                        No background worker tasks active or completed.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tab: Approvals */}
              {activeTab === 'approvals' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Approval Workflows</h2>
                    <p className="text-xs text-[#AFAFAF]">Review generated artifacts and confirm project transitions.</p>
                  </div>

                  {/* Approvals top progress bar - height: 12px, border-radius: 6px, green bar */}
                  <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-5 space-y-3 max-w-2xl">
                    <div className="flex justify-between items-center text-xs font-extrabold text-[#AFAFAF] uppercase tracking-wider">
                      <span>Approval Pipeline Progress</span>
                      <span className="text-[#3D8B00] font-extrabold">{approvedCount} of {totalApprovalsCount} Gates</span>
                    </div>
                    <div className="w-full bg-[#E5E5E5] dark:bg-zinc-800 h-3 rounded-full overflow-hidden">
                      <div className="bg-[#58CC02] h-full rounded-full transition-all duration-500" style={{ width: `${approvalProgressPercent}%` }} />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
                    {/* Left list of approvals - lock icon on left, right badge */}
                    <div className="lg:col-span-1 space-y-4">
                      {approvalReviews.map((review) => {
                        const isActionable = review.is_actionable;
                        const isApproved = review.decision === 'approved';
                        const isPending = review.decision === 'pending';
                        const isApproveLoading = approvingAction === `${review.approval}:approved`;
                        const badge = getBadgeStyle(review.decision);

                        const cardBg = isPending
                          ? 'bg-[#FFFBF4] dark:bg-amber-950/10'
                          : 'bg-white dark:bg-zinc-950';
                        const cardBorder = isPending
                          ? 'border-[#FF9600]'
                          : isApproved
                            ? 'border-[#58CC02]'
                            : 'border-[#E5E5E5] dark:border-zinc-800';
                        const cardShadow = isPending
                          ? 'shadow-[0_4px_0_#FF9600] hover:shadow-[0_6px_0_#FF9600]'
                          : isApproved
                            ? 'shadow-[0_4px_0_#58CC02] hover:shadow-[0_6px_0_#58CC02]'
                            : 'shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] hover:shadow-[0_6px_0_#D1D1D1] dark:hover:shadow-[0_6px_0_#1a1a1a]';

                        return (
                          <article
                            key={review.approval}
                            onClick={() => handleEditArtifact(review)}
                            className={`${cardBg} ${cardBorder} ${cardShadow} border-2 rounded-2xl p-5 space-y-3.5 cursor-pointer hover:-translate-y-0.5 transition-all duration-200 flex flex-col justify-between`}
                          >
                            <div className="flex justify-between items-center">
                              <div className="flex items-center space-x-2">
                                {isApproved ? (
                                  <Unlock className="h-4 w-4 text-[#58CC02] shrink-0" />
                                ) : (
                                  <Lock className="h-4 w-4 text-[#AFAFAF] shrink-0" />
                                )}
                                <h3 className="font-extrabold text-xs uppercase tracking-wider text-zinc-700 dark:text-zinc-350">{review.approval} Review</h3>
                              </div>
                              <span className={`inline-block px-2.5 py-0.5 rounded-full text-[9px] font-extrabold border capitalize tracking-wider leading-none ${badge.bg} ${badge.text} ${badge.border}`}>
                                {getStatusLabel(review.decision)}
                              </span>
                            </div>

                            <dl className="grid grid-cols-2 gap-x-2 text-[10px] text-zinc-550 pt-2 border-t border-[#F1F1F1] dark:border-zinc-900 font-bold">
                              <div>
                                <dt className="text-[#AFAFAF] uppercase text-[8px] mb-0.5">Artifact</dt>
                                <dd className="text-zinc-800 dark:text-zinc-200 uppercase">{review.artifact}</dd>
                              </div>
                              <div>
                                <dt className="text-[#AFAFAF] uppercase text-[8px] mb-0.5">File Path</dt>
                                <dd><FilePath path={review.artifact_path} segments={1} /></dd>
                              </div>
                            </dl>

                            {isActionable && (
                              <div className="bg-[#E8F9D9] text-[#3D8B00] border-2 border-[#58CC02]/20 text-[9px] font-extrabold p-2 rounded-xl text-center uppercase tracking-wider">
                                Action Required
                              </div>
                            )}

                            <div className="flex justify-end space-x-2 pt-1">
                              {review.approval === 'prd' && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleGeneratePrd();
                                  }}
                                  disabled={generatingPrd}
                                  className="bg-white hover:bg-zinc-50 border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-zinc-700 font-bold text-[10px] uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                >
                                  <RefreshCw className={`h-3 w-3 ${generatingPrd ? 'animate-spin' : ''}`} />
                                  <span>{generatingPrd ? 'Generating...' : 'Generate PRD'}</span>
                                </button>
                              )}
                              {review.artifact_path && (review.artifact_path.endsWith('.md') || review.artifact_path.endsWith('.markdown')) && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleEditArtifact(review);
                                  }}
                                  className="bg-white hover:bg-zinc-50 border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-zinc-700 font-bold text-[10px] uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                >
                                  <Edit className="h-3 w-3" />
                                  <span>Edit</span>
                                </button>
                              )}
                              {isActionable && (
                                <div className="flex flex-col items-end">
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleApproveDecision(review.approval, 'approved', getResolvingDebateRef(review.approval));
                                    }}
                                    disabled={actionLoading}
                                    className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold text-[10px] uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                  >
                                    {isApproveLoading ? (
                                      <RefreshCw className="h-3 w-3 animate-spin" />
                                    ) : (
                                      <Check className="h-3 w-3" />
                                    )}
                                    <span>{isApproveLoading ? 'Approving...' : 'Approve'}</span>
                                  </button>
                                  {isApproveLoading && (
                                    <span className="text-[8px] text-zinc-500 mt-1 text-right animate-pulse max-w-[150px] leading-tight">
                                      Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </article>
                        );
                      })}

                      {approvalReviews.length === 0 && (
                        <div className="bg-white dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 p-8 text-center rounded-2xl text-zinc-500 italic">
                          No approvals recorded in this workflow node state.
                        </div>
                      )}
                    </div>

                    {/* Right detail view / markdown editor & preview */}
                    <div className="lg:col-span-2 space-y-4">
                      {editingArtifact ? (
                        <div className="bg-white dark:bg-zinc-950 border-2 border-[#58CC02] shadow-[0_4px_0_#D1D1D1] rounded-2xl overflow-hidden flex flex-col min-h-[500px]">
                          <div className="px-6 py-4 bg-[#E8F9D9] border-b-2 border-[#58CC02] flex justify-between items-center">
                            <div>
                              <span className="text-[10px] font-bold text-[#3D8B00] uppercase tracking-wider">Markdown Editor</span>
                              <h3 className="font-extrabold text-sm text-[#3D8B00] mt-0.5">{editingArtifact.toUpperCase()}</h3>
                            </div>
                            <span className="text-[10px] font-mono font-bold text-[#3D8B00]">Save changes to disk</span>
                          </div>

                          <div className="flex-grow flex flex-col p-6 space-y-4">
                            <textarea
                              value={editorContent}
                              onChange={(e) => setEditorContent(e.target.value)}
                              spellCheck="false"
                              className="w-full flex-grow h-96 bg-zinc-50 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 text-xs font-mono focus:outline-none focus:border-[#58CC02] transition-colors leading-relaxed"
                            />
                            <code className="text-[10px] text-[#AFAFAF] font-bold leading-normal">Unsaved edits stay in this browser until saved.</code>
                          </div>

                          <div className="bg-[#F7F7F7] dark:bg-zinc-950 px-6 py-4 border-t-2 border-[#E5E5E5] dark:border-zinc-900 flex justify-end space-x-2">
                            <button
                              type="button"
                              onClick={() => setEditingArtifact(null)}
                              className="bg-white border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-zinc-700 font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics"
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              onClick={handleSaveArtifact}
                              disabled={actionLoading}
                              className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                            >
                              <Save className="h-4 w-4" />
                              <span>Save Markdown</span>
                            </button>
                          </div>
                        </div>
                      ) : approvalReviews.length > 0 ? (
                        (() => {
                          const selectedReview = approvalReviews.find(r => r.is_actionable) || approvalReviews[0];
                          const badge = getBadgeStyle(selectedReview.decision);
                          const committee = selectedReview.committee && typeof selectedReview.committee === 'object' ? selectedReview.committee : null;
                          const committeeAdvisors = Array.isArray(committee?.advisors) ? committee.advisors : [];
                          const committeeMessages = Array.isArray(committee?.messages) ? committee.messages : [];
                          const debateMessages = visibleGateDebate.key.endsWith(`:${selectedReview.approval}`) ? visibleGateDebate.messages : [];
                          const challengerMessages = debateMessages.filter((item) => ['ask', 'reject'].includes(item.kind));
                          const ownerRepliesByParent = debateMessages.reduce((acc, item) => {
                            if (['assert', 'propose'].includes(item.kind) && item.in_reply_to) {
                              acc[item.in_reply_to] = [...(acc[item.in_reply_to] || []), item];
                            }
                            return acc;
                          }, {});
                          const orphanOwnerReplies = debateMessages.filter((item) => (
                            ['assert', 'propose'].includes(item.kind) && !item.in_reply_to
                          ));
                          const debateAccent = (kind) => {
                            if (kind === 'ask') return 'border-l-[#FF9600]';
                            if (kind === 'reject') return 'border-l-[#FF4B4B]';
                            if (kind === 'challenge') return 'border-l-[#FF9600]';
                            if (kind === 'rebuttal') return 'border-l-[#1CB0F6]';
                            if (kind === 'assert') return 'border-l-[#1CB0F6]';
                            if (kind === 'propose') return 'border-l-[#58CC02]';
                            return 'border-l-[#AFAFAF]';
                          };
                          const renderDebateMessage = (item, extraClass = '') => (
                            <div key={item.id || `${item.from_role || 'unknown'}:${item.kind || 'message'}:${item.body || ''}`} className={`bg-white dark:bg-zinc-950 border border-[#E5E5E5] dark:border-zinc-800 border-l-4 ${debateAccent(item.kind)} rounded-xl p-3 ${extraClass}`}>
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-[9px] font-extrabold uppercase tracking-wider text-zinc-700 dark:text-zinc-300">{item.from_role || 'unknown'}</span>
                                {item.to && (
                                  <span className="text-[8px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">to {item.to}</span>
                                )}
                                <span className="text-[8px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">{item.kind}</span>
                              </div>
                              <p className="text-[11px] leading-relaxed text-zinc-650 dark:text-zinc-350 whitespace-pre-wrap">{item.body}</p>
                            </div>
                          );
                          const committeeRepliesByParent = committeeMessages.reduce((acc, item, index) => {
                            if (item?.in_reply_to) {
                              acc[item.in_reply_to] = [...(acc[item.in_reply_to] || []), { item, index }];
                            }
                            return acc;
                          }, {});
                          const committeeRootMessages = committeeMessages
                            .map((item, index) => ({ item, index }))
                            .filter(({ item }) => !item?.in_reply_to);
                          const hasCommitteeMessages = committeeMessages.length > 0;
                          const needsCommitteeDebate = !committee?.convened || !hasCommitteeMessages;
                          const debateRef = getResolvingDebateRef(selectedReview.approval);
                          const currentDebateRunKey = `${gateDebateProjectKey}:${gateDebateRunId}:${selectedReview.approval}`;
                          const isRunningDebate = debateRun.key === currentDebateRunKey && debateRun.loading;
                          const runDebateError = debateRun.key === currentDebateRunKey ? debateRun.error : '';
                          const isRejectLoading = approvingAction === `${selectedReview.approval}:rejected`;
                          const isRequestChangesLoading = approvingAction === `${selectedReview.approval}:changes_requested`;
                          const isApproveLoading = approvingAction === `${selectedReview.approval}:approved`;
                          const isSelectedApprovalLoading = isRejectLoading || isRequestChangesLoading || isApproveLoading;
                          return (
                            <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl overflow-hidden flex flex-col min-h-[500px]">
                              <div className="px-6 py-4 bg-zinc-50 dark:bg-zinc-900/50 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex justify-between items-center">
                                <div>
                                  <span className="text-[10px] font-bold text-[#AFAFAF] uppercase tracking-wider">Preview Panel</span>
                                  <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 mt-0.5">{selectedReview.artifact.toUpperCase()} Review Details</h3>
                                </div>
                                <span className={`inline-block px-2.5 py-0.5 rounded-full text-[9px] font-extrabold border uppercase tracking-wider leading-none ${badge.bg} ${badge.text} ${badge.border}`}>{selectedReview.decision.replace('_', ' ')}</span>
                              </div>

                              <div className="p-6 flex-grow space-y-6">
                                {/* Role reviewers info list */}
                                {selectedReview.review_roles && selectedReview.review_roles.length > 0 && (
                                  <div className="border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 bg-[#F7F7F7] dark:bg-zinc-900/10 font-bold">
                                    <h4 className="text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF] mb-2.5">Assigned Review Roles</h4>
                                    <div className="flex flex-wrap gap-2">
                                      {selectedReview.review_roles.map((role) => (
                                        <span key={role} className="bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300 text-[10px] px-2.5 py-1 rounded-lg border border-[#E5E5E5] dark:border-zinc-800">{role}</span>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                {selectedReview.is_actionable && committee && (
                                  <div className="border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 bg-[#FFFBF4] dark:bg-zinc-900/10 space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                      <div>
                                        <h4 className="text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">Committee</h4>
                                        {needsCommitteeDebate && (
                                          <p className="text-[10px] font-bold text-[#AFAFAF] mt-1">Committee declared. Run debate to convene it.</p>
                                        )}
                                      </div>
                                      <div className="flex items-center gap-2">
                                        {committee.convened && (
                                          <span className="text-[8px] font-extrabold uppercase tracking-wider text-[#3D8B00]">Convened</span>
                                        )}
                                        {needsCommitteeDebate && (
                                          <button
                                            type="button"
                                            onClick={() => handleRunDebate(selectedReview.approval)}
                                            disabled={isRunningDebate}
                                            className="bg-white hover:bg-[#F7F7F7] disabled:opacity-60 disabled:cursor-not-allowed border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-zinc-700 font-bold text-[9px] uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1.5"
                                          >
                                            <RefreshCw className={`h-3 w-3 ${isRunningDebate ? 'animate-spin' : ''}`} />
                                            <span>{isRunningDebate ? 'Running debate...' : 'Run debate'}</span>
                                          </button>
                                        )}
                                      </div>
                                    </div>
                                    {runDebateError && (
                                      <div className="text-[10px] font-bold text-[#FF4B4B]">Debate run failed: {runDebateError}</div>
                                    )}
                                    {committeeAdvisors.length > 0 && (
                                      <div className="flex flex-wrap gap-2">
                                        {committeeAdvisors.map((role) => (
                                          <span key={role} className="bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300 text-[10px] px-2.5 py-1 rounded-lg border border-[#E5E5E5] dark:border-zinc-800">{role}</span>
                                        ))}
                                      </div>
                                    )}
                                    {hasCommitteeMessages && (
                                      <div className="space-y-2">
                                        {committeeRootMessages.map(({ item, index }) => {
                                          const parentKey = item.id || String(index);
                                          return (
                                            <div key={parentKey} className="space-y-2">
                                              {renderDebateMessage(item)}
                                              {(committeeRepliesByParent[item.id] || []).map(({ item: reply }) => renderDebateMessage(reply, 'ml-8'))}
                                            </div>
                                          );
                                        })}
                                        {committeeMessages.length > committeeRootMessages.length && committeeRootMessages.length === 0 && (
                                          committeeMessages.map((message, index) => renderDebateMessage(message, index > 0 ? 'ml-8' : ''))
                                        )}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {selectedReview.is_actionable && !committee && (
                                  <div className="border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 bg-[#FFFBF4] dark:bg-zinc-900/10 space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                      <h4 className="text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">Gate Debate & Consensus</h4>
                                      <div className="flex items-center gap-2">
                                        {challengerMessages.length > 0 && (
                                          <span className="text-[8px] font-extrabold uppercase tracking-wider text-[#B36B00]">{challengerMessages.length} Objections</span>
                                        )}
                                        <button
                                          type="button"
                                          onClick={() => handleRunDebate(selectedReview.approval)}
                                          disabled={isRunningDebate}
                                          className="bg-white hover:bg-[#F7F7F7] disabled:opacity-60 disabled:cursor-not-allowed border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-zinc-700 font-bold text-[9px] uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1.5"
                                        >
                                          <RefreshCw className={`h-3 w-3 ${isRunningDebate ? 'animate-spin' : ''}`} />
                                          <span>{isRunningDebate ? 'Running debate...' : 'Run debate'}</span>
                                        </button>
                                      </div>
                                    </div>
                                    {runDebateError && (
                                      <div className="text-[10px] font-bold text-[#FF4B4B]">Debate run failed: {runDebateError}</div>
                                    )}
                                    {visibleGateDebate.loading && visibleGateDebate.key.endsWith(`:${selectedReview.approval}`) ? (
                                      <div className="text-[10px] font-bold text-[#AFAFAF]">Loading debate...</div>
                                    ) : visibleGateDebate.error && visibleGateDebate.key.endsWith(`:${selectedReview.approval}`) ? (
                                      <div className="text-[10px] font-bold text-[#FF4B4B]">Debate unavailable.</div>
                                    ) : debateMessages.length === 0 ? (
                                      <div className="text-[10px] font-bold text-[#AFAFAF]">No debate recorded for this gate.</div>
                                    ) : (
                                      <div className="space-y-2">
                                        {challengerMessages.map((message) => (
                                          <div key={message.id} className="space-y-2">
                                            {renderDebateMessage(message)}
                                            {(ownerRepliesByParent[message.id] || []).map((reply) => renderDebateMessage(reply, 'ml-8'))}
                                          </div>
                                        ))}
                                        {orphanOwnerReplies.map((message) => renderDebateMessage(message, 'ml-8'))}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {/* Markdown artifact preview */}
                                <div className="space-y-2">
                                  <h4 className="text-[10px] font-bold uppercase tracking-wider text-[#AFAFAF]">Content Preview</h4>
                                  {selectedReview.content ? (
                                    <div
                                      className="prose prose-zinc dark:prose-invert max-w-none text-xs leading-relaxed font-mono bg-[#F7F7F7] dark:bg-zinc-900/30 p-4 border border-[#E5E5E5] dark:border-zinc-800 rounded-2xl max-h-96 overflow-y-auto whitespace-pre-wrap select-all select-text"
                                    >
                                      {selectedReview.content}
                                    </div>
                                  ) : (
                                    <div className="text-center py-12 bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-850 rounded-xl text-zinc-400 italic text-xs">
                                      No artifact preview content available.
                                    </div>
                                  )}
                                </div>
                              </div>

                              {selectedReview.is_actionable && (
                                <div className="bg-[#F7F7F7] dark:bg-zinc-950 px-6 py-4 border-t-2 border-[#E5E5E5] dark:border-zinc-900 flex flex-col items-end space-y-2">
                                  <div className="flex justify-end space-x-2">
                                    <button
                                      type="button"
                                      onClick={() => handleApproveDecision(selectedReview.approval, 'rejected', debateRef)}
                                      disabled={actionLoading}
                                      className="bg-white hover:bg-[#FFE2E2] border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-[#FF4B4B] font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                    >
                                      {isRejectLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                                      <span>{isRejectLoading ? 'Rejecting...' : 'Reject'}</span>
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => handleApproveDecision(selectedReview.approval, 'changes_requested', debateRef)}
                                      disabled={actionLoading}
                                      className="bg-white hover:bg-[#FFF0D4] border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-[#FF9600] font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                    >
                                      {isRequestChangesLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                                      <span>{isRequestChangesLoading ? 'Requesting...' : 'Request Changes'}</span>
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => handleApproveDecision(selectedReview.approval, 'approved', debateRef)}
                                      disabled={actionLoading}
                                      className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                    >
                                      {isApproveLoading ? (
                                        <RefreshCw className="h-4 w-4 animate-spin" />
                                      ) : (
                                        <Check className="h-4 w-4" />
                                      )}
                                      <span>{isApproveLoading ? 'Submitting...' : 'Approve Gate'}</span>
                                    </button>
                                  </div>
                                  {isSelectedApprovalLoading && (
                                    <span className="text-[10px] text-zinc-500 animate-pulse text-right">
                                      Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })()
                      ) : (
                        <div className="bg-white dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 p-12 text-center text-zinc-400 italic text-xs min-h-[500px] flex items-center justify-center rounded-2xl">
                          Select an approval review card to view content.
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Tab: Messages */}
              {activeTab === 'messages' && (
                <MessageFeed
                  projectKey={messageFeedProjectKey}
                  runId={messageFeedRunId}
                  messages={messageFeed.key === targetMessageFeedKey ? messageFeed.messages : []}
                  loading={messageFeed.key === targetMessageFeedKey && messageFeed.loading}
                  error={messageFeed.key === targetMessageFeedKey ? messageFeed.error : ''}
                />
              )}

              {/* Tab: Memory */}
              {activeTab === 'memory' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Compounding Memory Layer</h2>
                    <p className="text-xs text-[#AFAFAF]">Long-term agent memory horizons and the semantic facts retrieved for the latest worker dispatches.</p>
                  </div>

                  {/* Cross-project Search Card */}
                  <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl overflow-hidden flex flex-col">
                    <div className="px-6 py-4 bg-zinc-50 dark:bg-zinc-900/50 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex justify-between items-center">
                      <div className="flex items-center space-x-3">
                        <Search className="h-5 w-5 text-[#1CB0F6] shrink-0" />
                        <div>
                          <span className="text-[10px] font-bold text-[#AFAFAF] uppercase tracking-wider">Search Engine</span>
                          <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 uppercase tracking-wide mt-0.5">Cross-Project Memory Retrieval</h3>
                        </div>
                      </div>
                    </div>

                    <form onSubmit={handleCrossSearch} className="p-6 space-y-4">
                      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                        {/* Role Select/Input */}
                        <div>
                          <label className="block text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF] mb-1.5">
                            Agent Role
                          </label>
                          <select
                            value={crossRole}
                            onChange={(e) => setCrossRole(e.target.value)}
                            className="w-full rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 bg-white dark:bg-zinc-950 px-3 py-2.5 text-xs font-semibold text-zinc-750 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-[#1CB0F6]/30"
                          >
                            <option value="implementation">Implementation</option>
                            <option value="ui">UI</option>
                            <option value="qa">QA</option>
                          </select>
                        </div>

                        {/* Query Text Input */}
                        <div className="md:col-span-2">
                          <label className="block text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF] mb-1.5">
                            Memory Query
                          </label>
                          <input
                            type="text"
                            value={crossQuery}
                            onChange={(e) => setCrossQuery(e.target.value)}
                            placeholder="Enter keywords or semantic query..."
                            required
                            className="w-full rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 bg-white dark:bg-zinc-950 px-3 py-2 text-xs font-semibold text-zinc-750 dark:text-zinc-200 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-[#1CB0F6]/30"
                          />
                        </div>

                        {/* Search Button */}
                        <div>
                          <button
                            type="submit"
                            disabled={crossLoading}
                            className="w-full bg-[#58CC02] hover:bg-[#4EBA02] disabled:bg-zinc-300 text-white border-2 border-[#3D8B00] px-4 py-2 rounded-xl text-xs font-extrabold uppercase tracking-wider shadow-[0_3px_0_#3D8B00] hover:shadow-[0_2px_0_#3D8B00] disabled:shadow-none hover:translate-y-0.5 active:translate-y-1 transition-all flex items-center justify-center gap-1.5 focus:outline-none cursor-pointer"
                          >
                            {crossLoading ? (
                              <RefreshCw className="h-4 w-4 animate-spin" />
                            ) : (
                              <Search className="h-4 w-4" />
                            )}
                            <span>Search</span>
                          </button>
                        </div>
                      </div>

                      {/* Optional Advanced Settings (Scope, Limit) */}
                      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 pt-2 border-t border-dashed border-[#F1F1F1] dark:border-zinc-850">
                        {/* Scope */}
                        <div className="md:col-span-3">
                          <label className="block text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF] mb-1.5">
                            Scope (comma-separated project keys, or 'all')
                          </label>
                          <input
                            type="text"
                            value={crossScope}
                            onChange={(e) => setCrossScope(e.target.value)}
                            placeholder="e.g. all, project-a, project-b"
                            className="w-full rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 bg-white dark:bg-zinc-950 px-3 py-2 text-xs font-semibold text-zinc-750 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-[#1CB0F6]/30"
                          />
                        </div>

                        {/* Limit (k) */}
                        <div>
                          <label className="block text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF] mb-1.5">
                            Max Results (k)
                          </label>
                          <input
                            type="number"
                            min="1"
                            max="50"
                            value={crossLimit}
                            onChange={(e) => setCrossLimit(parseInt(e.target.value) || 5)}
                            className="w-full rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 bg-white dark:bg-zinc-950 px-3 py-2 text-xs font-semibold text-zinc-750 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-[#1CB0F6]/30"
                          />
                        </div>
                      </div>
                    </form>

                    {/* Results / Error / Loading States */}
                    {(crossLoading || crossError || crossResults) && (
                      <div className="border-t-2 border-[#E5E5E5] dark:border-zinc-800 p-6 bg-zinc-50/50 dark:bg-zinc-900/10 space-y-4">
                        <h4 className="text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">
                          Cross-Project Search Results
                        </h4>

                        {crossLoading && (
                          <div className="flex items-center justify-center p-8 text-zinc-500 font-bold text-xs bg-zinc-50 dark:bg-zinc-900/30 border border-zinc-200 dark:border-zinc-850 rounded-xl">
                            <RefreshCw className="h-4 w-4 animate-spin mr-2 text-[#58CC02]" />
                            <span>Retrieving memories across projects...</span>
                          </div>
                        )}

                        {crossError && (
                          <div className="bg-amber-50 dark:bg-amber-950/20 border-2 border-amber-200 dark:border-amber-900 p-4 rounded-xl text-amber-700 dark:text-amber-300 text-xs font-semibold flex items-start gap-2">
                            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                            <span>{crossError}</span>
                          </div>
                        )}

                        {!crossLoading && !crossError && crossResults && crossResults.length === 0 && (
                          <div className="text-center py-8 bg-zinc-50 dark:bg-zinc-900/30 border border-zinc-200 dark:border-zinc-850 rounded-xl text-zinc-400 italic text-xs font-semibold">
                            no cross-project matches
                          </div>
                        )}

                        {!crossLoading && !crossError && crossResults && crossResults.length > 0 && (
                          <div className="space-y-3">
                            {crossResults.map((item) => (
                              <div
                                key={item.id}
                                className="w-full text-left border border-zinc-250 dark:border-zinc-850 p-4 rounded-xl space-y-2 bg-[#FDFDFD] dark:bg-zinc-900/20 shadow-sm"
                              >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <span className="font-mono text-[10px] font-bold text-[#3D8B00] dark:text-emerald-400">
                                    {item.id}
                                  </span>
                                  <span className="flex flex-wrap items-center gap-2">
                                    <span className="bg-blue-50 dark:bg-blue-950/20 text-blue-600 dark:text-blue-400 px-2 py-0.5 rounded uppercase text-[9px] font-extrabold border border-blue-100 dark:border-blue-900/50">
                                      {item.horizon || 'event'}
                                    </span>
                                    {item.score !== undefined && item.score !== null && (
                                      <span className="bg-amber-50 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded border border-amber-100 dark:border-amber-900/50 text-[9px] font-extrabold uppercase">
                                        Score: {typeof item.score === 'number' ? item.score.toFixed(2) : item.score}
                                      </span>
                                    )}
                                    <span className="bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 px-2 py-0.5 rounded uppercase text-[9px] font-extrabold border border-zinc-200 dark:border-zinc-700">
                                      Project: {item.project_key}
                                    </span>
                                  </span>
                                </div>
                                <p className="text-xs font-semibold leading-relaxed text-zinc-750 dark:text-zinc-250 whitespace-pre-wrap">
                                  {item.text || item.body || 'No event body recorded.'}
                                </p>
                                <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold text-zinc-500">
                                  <span className="font-mono text-zinc-500">{item.timestamp || item.ts}</span>
                                  {item.source && (
                                    <span className="font-mono text-zinc-400 truncate max-w-full">{item.source}</span>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {!state ? (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-8 text-center space-y-4 max-w-md mx-auto">
                      <Brain className="h-12 w-12 text-[#1CB0F6] mx-auto animate-pulse" />
                      <h3 className="font-extrabold text-base text-zinc-800 dark:text-zinc-200">No Active Runs</h3>
                      <p className="text-xs text-zinc-550 dark:text-zinc-400 font-semibold">
                        This project does not have an active run or initialized workspace. Please run a workflow first to seed and activate the memory layer.
                      </p>
                    </div>
                  ) : loadingMemory ? (
                    <div className="flex items-center justify-center p-12 text-zinc-500 font-bold">
                      <RefreshCw className="h-5 w-5 animate-spin mr-2 text-[#58CC02]" />
                      <span>Loading memory horizons...</span>
                    </div>
                  ) : memoryError ? (
                    <div className="bg-amber-50 dark:bg-amber-950/20 border-2 border-amber-200 dark:border-amber-900 p-6 rounded-2xl text-center space-y-2 max-w-xl mx-auto">
                      <AlertTriangle className="h-8 w-8 text-amber-500 mx-auto" />
                      <h3 className="font-extrabold text-sm text-amber-800 dark:text-amber-300">Memory Endpoint Not Available Yet</h3>
                      <p className="text-xs text-amber-600 dark:text-amber-400 font-semibold leading-relaxed">
                        The backend memory query API might not be deployed or is temporarily offline. Details: {memoryError}
                      </p>
                    </div>
                  ) : !memoryData || !memoryData.roles || memoryData.roles.length === 0 ? (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs">
                      No memory data recorded yet for this project.
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 gap-6">
                      {memoryData.roles.map((role) => {
                        const eventBrowser = memoryEventBrowsers[role.role] || {};
                        const browseEvents = Array.isArray(eventBrowser.events) ? eventBrowser.events : [];
                        return (
                        <div key={role.role} className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl overflow-hidden flex flex-col">
                          {/* Card Header */}
                          <div className="px-6 py-4 bg-zinc-50 dark:bg-zinc-900/50 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex justify-between items-center">
                            <div className="flex items-center space-x-3">
                              <Cpu className="h-5 w-5 text-[#1CB0F6] shrink-0" />
                              <div>
                                <span className="text-[10px] font-bold text-[#AFAFAF] uppercase tracking-wider">Agent Role</span>
                                <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 uppercase tracking-wide mt-0.5">{role.role}</h3>
                              </div>
                            </div>
                            <div className="flex flex-wrap items-center justify-end gap-2">
                              <button
                                type="button"
                                onClick={() => handleToggleMemoryEvents(role.role)}
                                className="bg-white hover:bg-zinc-50 dark:bg-zinc-950 dark:hover:bg-zinc-900 text-zinc-650 dark:text-zinc-300 border-2 border-[#E5E5E5] dark:border-zinc-800 px-3 py-1 rounded-xl text-[10px] font-extrabold tracking-wider uppercase transition-all duo-button-physics focus:outline-none focus:ring-2 focus:ring-[#1CB0F6]/30 flex items-center gap-1.5"
                                aria-expanded={Boolean(eventBrowser.open)}
                              >
                                <FileText className="h-3.5 w-3.5" />
                                <span>Browse events</span>
                              </button>
                              <span className="bg-[#E8F9D9] dark:bg-emerald-950/30 text-[#3D8B00] dark:text-emerald-400 border-2 border-[#58CC02]/20 dark:border-emerald-500/20 px-3 py-1 rounded-xl text-[10px] font-extrabold tracking-wider uppercase">
                                {role.event_count || 0} events
                              </span>
                            </div>
                          </div>

                          {/* Card Body */}
                          <div className="p-6 space-y-6">
                            {role.event_count === 0 ? (
                              <div className="text-center py-8 bg-zinc-50 dark:bg-zinc-900/30 border border-zinc-200 dark:border-zinc-850 rounded-xl text-zinc-400 italic text-xs font-semibold">
                                no events yet
                              </div>
                            ) : (
                              <>
                                {/* Memory Horizons Grid */}
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                  {/* Long-term Summary */}
                                  <div className="border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 bg-zinc-50/50 dark:bg-zinc-900/10 space-y-2">
                                    <h4 className="text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF] flex items-center space-x-1">
                                      <span>Long-Term Memory</span>
                                    </h4>
                                    <p className="text-xs text-zinc-700 dark:text-zinc-300 font-semibold leading-relaxed">
                                      {role.summaries?.['long-term'] || 'No long-term summary recorded.'}
                                    </p>
                                  </div>

                                  {/* Mid-term Summary */}
                                  <div className="border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 bg-zinc-50/50 dark:bg-zinc-900/10 space-y-2">
                                    <h4 className="text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF] flex items-center space-x-1">
                                      <span>Mid-Term Memory</span>
                                    </h4>
                                    <p className="text-xs text-zinc-700 dark:text-zinc-300 font-semibold leading-relaxed">
                                      {role.summaries?.['mid-term'] || 'No mid-term summary recorded.'}
                                    </p>
                                  </div>

                                  {/* Short-term Memory */}
                                  <div className="border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-4 bg-zinc-50/50 dark:bg-zinc-900/10 space-y-2">
                                    <h4 className="text-[9px] font-extrabold uppercase tracking-wider text-[#AFAFAF] flex items-center space-x-1">
                                      <span>Short-Term Memory</span>
                                    </h4>
                                    <p className="text-xs text-zinc-700 dark:text-zinc-300 font-semibold leading-relaxed whitespace-pre-wrap">
                                      {role.short_term || 'No short-term text recorded.'}
                                    </p>
                                  </div>
                                </div>

                                {eventBrowser.open && (
                                  <div className="space-y-3 pt-2">
                                    <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 border-b border-[#F1F1F1] dark:border-zinc-900 pb-2">
                                      <h4 className="text-[10px] font-bold uppercase tracking-wider text-[#AFAFAF] flex items-center space-x-1.5">
                                        <span>Browse Events</span>
                                      </h4>
                                      <label className="relative block w-full md:w-72">
                                        <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" />
                                        <input
                                          type="search"
                                          value={eventBrowser.query || ''}
                                          onChange={(event) => handleMemoryEventSearchChange(role.role, event.target.value)}
                                          placeholder="Search events"
                                          className="w-full rounded-xl border-2 border-[#E5E5E5] dark:border-zinc-800 bg-white dark:bg-zinc-950 py-2 pl-9 pr-3 text-xs font-semibold text-zinc-750 dark:text-zinc-200 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-[#1CB0F6]/30"
                                        />
                                      </label>
                                    </div>

                                    {eventBrowser.loading ? (
                                      <div className="flex items-center justify-center p-8 text-zinc-500 font-bold text-xs bg-zinc-50 dark:bg-zinc-900/30 border border-zinc-200 dark:border-zinc-850 rounded-xl">
                                        <RefreshCw className="h-4 w-4 animate-spin mr-2 text-[#58CC02]" />
                                        <span>Loading events...</span>
                                      </div>
                                    ) : eventBrowser.error ? (
                                      <div className="bg-amber-50 dark:bg-amber-950/20 border-2 border-amber-200 dark:border-amber-900 p-4 rounded-xl text-amber-700 dark:text-amber-300 text-xs font-semibold flex items-start gap-2">
                                        <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                                        <span>{eventBrowser.error}</span>
                                      </div>
                                    ) : browseEvents.length === 0 ? (
                                      <div className="text-center py-6 bg-zinc-50 dark:bg-zinc-900/30 border border-zinc-200 dark:border-zinc-850 rounded-xl text-zinc-400 italic text-[11px] font-semibold">
                                        No memory events match this search.
                                      </div>
                                    ) : (
                                      <div className="space-y-2">
                                        {browseEvents.map((event) => (
                                          <button
                                            type="button"
                                            key={event.id}
                                            onClick={() => handleOpenMemoryEvent(event.id)}
                                            className="w-full text-left border border-zinc-250 dark:border-zinc-850 p-4 rounded-xl space-y-2 bg-[#FDFDFD] hover:bg-zinc-50 dark:bg-zinc-900/20 dark:hover:bg-zinc-900/40 shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[#58CC02]/30"
                                          >
                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                              <span className="font-mono text-[10px] font-bold text-[#3D8B00] dark:text-emerald-400">{event.id}</span>
                                              <span className="flex flex-wrap items-center gap-2">
                                                {event.is_superseded && (
                                                  <span className="bg-zinc-100 dark:bg-zinc-850 text-zinc-550 dark:text-zinc-400 px-2 py-0.5 rounded uppercase text-[9px] font-extrabold border border-zinc-200 dark:border-zinc-800">
                                                    Superseded
                                                  </span>
                                                )}
                                                <span className="bg-blue-50 dark:bg-blue-950/20 text-blue-600 dark:text-blue-400 px-2 py-0.5 rounded uppercase text-[9px] font-extrabold border border-blue-100 dark:border-blue-900/50">
                                                  {event.kind || event.horizon || 'event'}
                                                </span>
                                              </span>
                                            </div>
                                            <p className="text-xs font-semibold leading-relaxed text-zinc-750 dark:text-zinc-250 whitespace-pre-wrap">
                                              {event.body || 'No event body recorded.'}
                                            </p>
                                            <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold text-zinc-500">
                                              <span className="font-mono text-zinc-500">{event.timestamp || event.ts}</span>
                                              {event.source && (
                                                <span className="font-mono text-zinc-400 truncate max-w-full">{event.source}</span>
                                              )}
                                            </div>
                                          </button>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {/* Retrieved Facts Section */}
                                <div className="space-y-3 pt-2">
                                  <div className="flex justify-between items-center border-b border-[#F1F1F1] dark:border-zinc-900 pb-2">
                                    <h4 className="text-[10px] font-bold uppercase tracking-wider text-[#AFAFAF] flex items-center space-x-1.5">
                                      <span>Retrieved Facts</span>
                                    </h4>
                                    {role.latest_retrieval?.job_id && (
                                      <span className="text-[9px] font-mono text-[#AFAFAF]">
                                        Last Job: <span className="font-bold text-zinc-650 dark:text-zinc-450 font-semibold">{role.latest_retrieval.job_id}</span>
                                      </span>
                                    )}
                                  </div>

                                  {role.latest_retrieval?.relevant_events && role.latest_retrieval.relevant_events.length > 0 ? (
                                    <div className="space-y-3">
                                      {role.latest_retrieval.relevant_events.map((event, idx) => (
                                        <div key={event.id || idx} className="border border-zinc-250 dark:border-zinc-850 p-4 rounded-xl space-y-2.5 bg-[#FDFDFD] dark:bg-zinc-900/20 shadow-sm">
                                          <p className="text-xs font-mono leading-relaxed text-zinc-800 dark:text-zinc-200 bg-zinc-50/50 dark:bg-zinc-900/40 p-2.5 rounded-lg border border-dashed border-zinc-200 dark:border-zinc-800 whitespace-pre-wrap select-all select-text font-medium">
                                            {event.text}
                                          </p>
                                          <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] font-bold text-zinc-500">
                                            <div className="flex items-center space-x-2">
                                              <button
                                                type="button"
                                                onClick={() => handleOpenMemoryEvent(event.id)}
                                                className="bg-[#E8F9D9] hover:bg-[#D7F6BF] dark:bg-emerald-950/30 dark:hover:bg-emerald-900/40 text-[#3D8B00] dark:text-emerald-400 px-2 py-0.5 rounded font-mono border-2 border-[#58CC02]/30 shadow-[0_2px_0_#A7D98A] dark:shadow-[0_2px_0_#12381f] transition-all duo-button-physics focus:outline-none focus:ring-2 focus:ring-[#58CC02]/40"
                                                aria-label={`Open full memory event ${event.id}`}
                                              >
                                                {event.id}
                                              </button>
                                              <span className="bg-blue-50 dark:bg-blue-950/20 text-blue-600 dark:text-blue-400 px-2 py-0.5 rounded uppercase text-[9px] border border-blue-100 dark:border-blue-900/50">
                                                {event.horizon}
                                              </span>
                                              {event.source && (
                                                <span className="text-zinc-400 dark:text-zinc-500 flex items-center space-x-1">
                                                  <span>Source:</span>
                                                  <span className="font-mono text-zinc-600 dark:text-zinc-400 truncate max-w-[200px]">{event.source}</span>
                                                </span>
                                              )}
                                            </div>
                                            {event.score !== undefined && event.score !== null && (
                                              <span className="bg-amber-50 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded border border-amber-100 dark:border-amber-900/50">
                                                Score: {typeof event.score === 'number' ? event.score.toFixed(2) : event.score}
                                              </span>
                                            )}
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <div className="text-center py-6 bg-zinc-50 dark:bg-zinc-900/30 border border-zinc-200 dark:border-zinc-850 rounded-xl text-zinc-400 italic text-[11px] font-semibold">
                                      No relevant facts retrieved in the latest job.
                                    </div>
                                  )}
                                </div>
                              </>
                            )}
                          </div>
                        </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Tab: Dev Loop */}
              {activeTab === 'devloop' && (
                <div className="space-y-6">
                  {/* Header Section */}
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2 flex justify-between items-end flex-wrap gap-2">
                    <div>
                      <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Dev Loop</h2>
                      <p className="text-xs text-[#AFAFAF]">Observability view of in-flight dev loops, including active worktrees, open PRs, CI statuses, and pre-reviews.</p>
                    </div>
                    {devLoopData?.generated_at && (
                      <span className="text-[10px] font-mono font-bold text-[#AFAFAF] uppercase bg-[#F1F1F1] dark:bg-zinc-900 border border-[#E5E5E5] dark:border-zinc-800 px-2 py-0.5 rounded-lg">
                        Freshness: {formatHumanTime(devLoopData.generated_at)}
                      </span>
                    )}
                  </div>

                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
                    {/* Left/Main Column: Observability lists (PRs, Worktrees, Warnings) */}
                    <div className="lg:col-span-2 space-y-6">

                  {loadingDevLoop && <Spinner />}

                  {devLoopError && (
                    <div className="bg-[#FFE2E2] dark:bg-zinc-900 border-2 border-[#FF4B4B]/30 p-4 rounded-xl text-[#EA2B2B] dark:text-[#FF4B4B] text-xs font-semibold flex items-start gap-2 max-w-2xl">
                      <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                      <div>
                        <h4 className="font-extrabold uppercase tracking-wide text-[10px]">Error loading Dev Loop status</h4>
                        <p className="mt-1">{devLoopError}</p>
                      </div>
                    </div>
                  )}

                  {!loadingDevLoop && !devLoopError && (!devLoopData || (
                    (!Array.isArray(devLoopData.worktrees) || devLoopData.worktrees.length === 0) &&
                    (!Array.isArray(devLoopData.pull_requests) || devLoopData.pull_requests.length === 0) &&
                    (!Array.isArray(devLoopData.warnings) || devLoopData.warnings.length === 0)
                  )) && (
                    <div className="bg-white dark:bg-zinc-950 border-2 border-zinc-200 dark:border-zinc-800 p-12 text-center rounded-2xl text-zinc-550 italic text-xs max-w-2xl">
                      No active Dev Loop details recorded.
                    </div>
                  )}

                  {!loadingDevLoop && !devLoopError && devLoopData && (
                    <div className="space-y-6 max-w-4xl">
                      {/* Warnings notice if any warnings exist */}
                      {Array.isArray(devLoopData.warnings) && devLoopData.warnings.length > 0 && (
                        <div className="bg-amber-50 dark:bg-amber-950/20 border-2 border-amber-200 dark:border-amber-900/50 p-4 rounded-xl text-amber-700 dark:text-amber-400 text-xs font-semibold space-y-1">
                          <div className="flex items-center space-x-1.5 text-amber-800 dark:text-amber-300">
                            <AlertTriangle className="h-4 w-4 shrink-0" />
                            <span className="font-extrabold uppercase text-[10px] tracking-wider">System Warnings</span>
                          </div>
                          <ul className="list-disc pl-5 font-medium space-y-1 mt-1 text-zinc-650 dark:text-zinc-400">
                            {devLoopData.warnings.map((w, idx) => (
                              <li key={idx}>{w}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Section 1: Open PRs */}
                      <div className="space-y-3">
                        <h3 className="text-xs font-extrabold text-zinc-550 dark:text-zinc-450 uppercase tracking-wider">Open PRs & CI Status</h3>
                        {Array.isArray(devLoopData.pull_requests) && devLoopData.pull_requests.length > 0 ? (
                          <div className="grid grid-cols-1 gap-4">
                            {devLoopData.pull_requests.map((pr, idx) => {
                              const prNumber = pr?.number;

                              // Join defensively with pre_reviews on pr number
                              const isPreReviewed = prNumber !== undefined && Array.isArray(devLoopData.pre_reviews) && devLoopData.pre_reviews.some(
                                (r) => r && r.pr === prNumber && r.present
                              );

                              // Normalize status
                              let ciStatus = String(pr?.ci || 'none').toLowerCase().trim();
                              if (ciStatus === 'pass') ciStatus = 'complete';
                              if (ciStatus === 'fail') ciStatus = 'failed';
                              const ciStyle = getBadgeStyle(ciStatus);
                              const CIIcon = ciStyle.icon;

                              return (
                                <article
                                  key={prNumber !== undefined ? prNumber : idx}
                                  className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-5 hover:-translate-y-0.5 hover:shadow-[0_6px_0_#D1D1D1] transition-all duration-200 flex flex-col sm:flex-row justify-between sm:items-center gap-4"
                                >
                                  <div className="space-y-1.5">
                                    <div className="flex flex-wrap items-center gap-2">
                                      {prNumber !== undefined && (
                                        <span className="font-mono text-xs font-bold text-zinc-400">#{prNumber}</span>
                                      )}
                                      <h4 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200">
                                        {pr?.title || 'Untitled Pull Request'}
                                      </h4>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold text-zinc-500">
                                      <span className="bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 px-2 py-0.5 rounded text-zinc-650 dark:text-zinc-400 font-mono text-[10px]">
                                        {pr?.head_branch || 'no branch'}
                                      </span>
                                      {pr?.land_command && (
                                        <code className="bg-zinc-50 dark:bg-zinc-900/60 border border-zinc-200 dark:border-zinc-800 px-2 py-0.5 rounded text-zinc-500 dark:text-zinc-400 font-mono text-[10px] truncate max-w-[200px] sm:max-w-xs md:max-w-md lg:max-w-lg" title={pr.land_command}>
                                          {pr.land_command}
                                        </code>
                                      )}
                                    </div>
                                  </div>

                                  <div className="flex flex-wrap items-center gap-2 shrink-0">
                                    {/* CI badge */}
                                    <span className={`inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase border ${ciStyle.bg} ${ciStyle.text} ${ciStyle.border}`}>
                                      <CIIcon className="h-3 w-3 shrink-0" />
                                      <span>CI: {pr?.ci || 'none'}</span>
                                    </span>

                                    {/* Pre-reviewed badge */}
                                    {isPreReviewed && (
                                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase border bg-[#D0F0FF] text-[#0A7BAF] border-[#1CB0F6]/30">
                                        pre-reviewed
                                      </span>
                                    )}

                                    {/* Land Readiness badge */}
                                    {pr?.land_ready === true && (
                                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase border bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]/30">
                                        <CheckCircle className="h-3 w-3 shrink-0" />
                                        <span>Ready to land</span>
                                      </span>
                                    )}
                                    {pr?.land_ready === false && (
                                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase border bg-[#F1F1F1] text-[#777777] border-[#AFAFAF]/30">
                                        <Clock className="h-3 w-3 shrink-0" />
                                        <span>not ready</span>
                                      </span>
                                    )}

                                    {/* Copy land command button */}
                                    {pr?.land_command && (
                                      <button
                                        onClick={() => copyLandCommand(pr.land_command)}
                                        className={`border-2 shadow-[0_2px_0_#D1D1D1] dark:shadow-[0_2px_0_#1a1a1a] font-bold p-1.5 rounded-xl text-xs transition-all duo-button-physics inline-flex items-center justify-center shrink-0 cursor-pointer gap-1 ${
                                          copiedText === pr.land_command
                                            ? 'bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]/30 hover:bg-[#E8F9D9]'
                                            : copyFailedText === pr.land_command
                                              ? 'bg-[#FFECEC] text-[#D22F2F] border-[#FF4B4B]/30 hover:bg-[#FFECEC]'
                                            : 'bg-white dark:bg-zinc-850 hover:bg-[#D0F0FF] hover:text-[#0A7BAF] border-[#E5E5E5] dark:border-zinc-800 text-zinc-700 dark:text-zinc-300'
                                        }`}
                                        title={`Copy land command: ${pr.land_command}`}
                                      >
                                        {copiedText === pr.land_command ? (
                                          <>
                                            <Check className="h-3.5 w-3.5" />
                                            <span className="text-[10px] font-extrabold uppercase">Copied!</span>
                                          </>
                                        ) : copyFailedText === pr.land_command ? (
                                          <>
                                            <X className="h-3.5 w-3.5" />
                                            <span className="text-[10px] font-extrabold uppercase">Copy failed</span>
                                          </>
                                        ) : (
                                          <>
                                            <Copy className="h-3.5 w-3.5" />
                                            <span className="text-[10px] font-extrabold uppercase">Copy land command</span>
                                          </>
                                        )}
                                      </button>
                                    )}

                                    {/* External Link */}
                                    {pr?.url && (
                                      <a
                                        href={pr.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="bg-white dark:bg-zinc-850 hover:bg-[#E8F9D9] hover:text-[#3D8B00] border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_2px_0_#D1D1D1] dark:shadow-[0_2px_0_#1a1a1a] text-zinc-700 dark:text-zinc-300 font-bold p-1.5 rounded-xl text-xs transition-all duo-button-physics inline-flex items-center justify-center shrink-0 cursor-pointer"
                                        title="View Pull Request on GitHub"
                                      >
                                        <ExternalLink className="h-3.5 w-3.5" />
                                      </a>
                                    )}
                                  </div>
                                </article>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="bg-zinc-50/50 dark:bg-zinc-900/10 border border-zinc-200 dark:border-zinc-850 p-6 rounded-2xl text-center text-zinc-400 italic text-xs font-semibold">
                            No open pull requests.
                          </div>
                        )}
                      </div>

                      {/* Section 2: Worktrees */}
                      <div className="space-y-3">
                        <h3 className="text-xs font-extrabold text-zinc-550 dark:text-zinc-450 uppercase tracking-wider">Active Worktrees</h3>
                        {Array.isArray(devLoopData.worktrees) && devLoopData.worktrees.length > 0 ? (
                          <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl overflow-hidden font-semibold text-xs">
                            <div className="overflow-x-auto">
                              <table className="w-full text-left border-collapse">
                                <thead>
                                  <tr className="bg-zinc-50 dark:bg-zinc-900/50 border-b-2 border-[#E5E5E5] dark:border-zinc-800 text-[10px] font-extrabold uppercase tracking-wider text-[#AFAFAF]">
                                    <th className="px-5 py-3">Path</th>
                                    <th className="px-5 py-3">Branch</th>
                                    <th className="px-5 py-3">Commit Head</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-[#F1F1F1] dark:divide-zinc-900">
                                  {devLoopData.worktrees.map((wt, idx) => (
                                    <tr key={idx} className="hover:bg-zinc-50/50 dark:hover:bg-zinc-900/30 text-zinc-700 dark:text-zinc-300">
                                      <td className="px-5 py-3 font-mono text-[11px] truncate max-w-xs" title={wt?.path}>
                                        <FilePath path={wt?.path} segments={2} />
                                      </td>
                                      <td className="px-5 py-3 font-mono text-[11px] text-zinc-800 dark:text-zinc-200 font-bold">
                                        {wt?.branch || 'no branch'}
                                      </td>
                                      <td className="px-5 py-3 font-mono text-[11px]">
                                        {wt?.head ? (
                                          <code className="bg-[#F1F1F1] dark:bg-zinc-900 border border-[#E5E5E5] dark:border-zinc-800 px-2 py-0.5 rounded text-zinc-655 dark:text-zinc-400 font-mono text-[10px]">
                                            {wt.head.slice(0, 7)}
                                          </code>
                                        ) : (
                                          <span className="text-[#AFAFAF] italic">n/a</span>
                                        )}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        ) : (
                          <div className="bg-zinc-50/50 dark:bg-zinc-900/10 border border-zinc-200 dark:border-zinc-850 p-6 rounded-2xl text-center text-zinc-400 italic text-xs font-semibold">
                            No active git worktrees.
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                    </div> {/* closes lg:col-span-2 */}

                    {/* Right Column: Dispatch form card */}
                    <div className="lg:col-span-1">
                      <div className="bg-white dark:bg-zinc-955 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] p-5 rounded-2xl space-y-4">
                        <div className="border-b border-[#F1F1F1] dark:border-zinc-900 pb-2">
                          <h3 className="font-extrabold text-sm text-zinc-855 dark:text-zinc-200">Dispatch a Track</h3>
                          <p className="text-[11px] text-[#AFAFAF] mt-0.5">Start a new parallel development track.</p>
                        </div>

                        {dispatchSuccess && (
                          <div className="bg-[#E8F9D9] dark:bg-zinc-900 border-2 border-[#58CC02]/30 p-3 rounded-xl text-[#3D8B00] dark:text-[#58CC02] text-xs font-semibold space-y-1">
                            <div className="flex items-center gap-1 font-bold">
                              <CheckCircle className="h-4 w-4 shrink-0" />
                              <span>Track started asynchronously!</span>
                            </div>
                            <ul className="list-disc pl-4 font-mono text-[10px] space-y-0.5 mt-1">
                              <li>Run ID: {dispatchSuccess.run_id}</li>
                              <li>Branch: {dispatchSuccess.branch}</li>
                              <li>Worktree: {dispatchSuccess.worktree}</li>
                            </ul>
                          </div>
                        )}

                        {dispatchError && (
                          <div className="bg-[#FFE2E2] dark:bg-zinc-900 border-2 border-[#FF4B4B]/30 p-3 rounded-xl text-[#EA2B2B] dark:text-[#FF4B4B] text-xs font-semibold flex items-start gap-1.5">
                            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                            <div className="break-all">{dispatchError}</div>
                          </div>
                        )}

                        <form onSubmit={handleDispatchTrack} className="space-y-3.5 text-xs font-semibold">
                          <div>
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Track Name</label>
                            <input
                              type="text"
                              value={dispatchName}
                              onChange={(e) => setDispatchName(e.target.value)}
                              placeholder="e.g. feat-login-page"
                              disabled={dispatchLoading}
                              className="w-full bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 focus:outline-none focus:border-[#58CC02] font-mono transition-colors"
                            />
                          </div>

                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Role</label>
                              <select
                                value={dispatchRole}
                                onChange={(e) => setDispatchRole(e.target.value)}
                                disabled={dispatchLoading}
                                className="w-full bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-2.5 py-2 focus:outline-none focus:border-[#58CC02] transition-colors"
                              >
                                {['backend', 'frontend', ...workerRoles.filter(r => r !== 'backend' && r !== 'frontend')].map((role) => (
                                  <option key={role} value={role}>{role}</option>
                                ))}
                                <option value="custom">Custom...</option>
                              </select>
                            </div>

                            <div>
                              <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Adapter</label>
                              <select
                                value={dispatchAdapter}
                                onChange={(e) => setDispatchAdapter(e.target.value)}
                                disabled={dispatchLoading}
                                className="w-full bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-2.5 py-2 focus:outline-none focus:border-[#58CC02] transition-colors"
                              >
                                {roleBackendOptions.map((opt) => (
                                  <option key={opt} value={opt}>
                                    {opt === 'codex' ? 'Codex CLI' : opt === 'anthropic' || opt === 'claude' ? 'Claude CLI' : opt}
                                  </option>
                                ))}
                                <option value="custom">Custom...</option>
                              </select>
                            </div>
                          </div>

                          {dispatchRole === 'custom' && (
                            <div>
                              <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Custom Role Name</label>
                              <input
                                type="text"
                                value={dispatchCustomRole}
                                onChange={(e) => setDispatchCustomRole(e.target.value)}
                                placeholder="e.g. backend_audit"
                                disabled={dispatchLoading}
                                className="w-full bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 focus:outline-none focus:border-[#58CC02] font-mono transition-colors"
                              />
                            </div>
                          )}

                          {dispatchAdapter === 'custom' && (
                            <div>
                              <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Custom Adapter Name</label>
                              <input
                                type="text"
                                value={dispatchCustomAdapter}
                                onChange={(e) => setDispatchCustomAdapter(e.target.value)}
                                placeholder="e.g. openai"
                                disabled={dispatchLoading}
                                className="w-full bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 focus:outline-none focus:border-[#58CC02] font-mono transition-colors"
                              />
                            </div>
                          )}

                          <div>
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Task Details</label>
                            <textarea
                              value={dispatchTask}
                              onChange={(e) => setDispatchTask(e.target.value)}
                              placeholder="Describe the task for this worker..."
                              disabled={dispatchLoading}
                              className="w-full h-24 bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl p-3 focus:outline-none focus:border-[#58CC02] font-mono leading-relaxed transition-colors resize-none"
                            />
                          </div>

                          <div>
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Constraints (Optional)</label>
                            <textarea
                              value={dispatchConstraints}
                              onChange={(e) => setDispatchConstraints(e.target.value)}
                              placeholder="Optional constraints to restrict the worker..."
                              disabled={dispatchLoading}
                              className="w-full h-16 bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl p-3 focus:outline-none focus:border-[#58CC02] font-mono leading-relaxed transition-colors resize-none"
                            />
                          </div>

                          <div>
                            <label className="block text-[10px] text-[#AFAFAF] uppercase tracking-wider mb-1">Verification Command (Optional)</label>
                            <input
                              type="text"
                              value={dispatchVerify}
                              onChange={(e) => setDispatchVerify(e.target.value)}
                              placeholder="e.g. npm run test"
                              disabled={dispatchLoading}
                              className="w-full bg-zinc-55 dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-2 focus:outline-none focus:border-[#58CC02] font-mono transition-colors"
                            />
                          </div>

                          <button
                            type="submit"
                            disabled={dispatchLoading}
                            className="w-full bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_4px_0_#3D8B00] text-white font-bold py-2.5 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {dispatchLoading ? (
                              <>
                                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                <span>Dispatching...</span>
                              </>
                            ) : (
                              <>
                                <Play className="h-3.5 w-3.5 fill-current" />
                                <span>Dispatch Track</span>
                              </>
                            )}
                          </button>
                        </form>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Tab: System */}
              {activeTab === 'system' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">System Information</h2>
                    <p className="text-xs text-[#AFAFAF]">Live JSON configuration files, workspaces parameters, and target variables.</p>
                  </div>

                  {/* Two columns of white cards: STATE / TARGET */}
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start font-semibold text-xs">

                    {/* STATE Card */}
                    <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-6 space-y-4">
                      <div className="border-b border-[#F1F1F1] pb-2 flex justify-between items-center">
                        <h3 className="font-extrabold text-sm text-zinc-850 dark:text-zinc-200">Active Workflow State</h3>
                        <span className="text-[10px] text-[#AFAFAF] uppercase tracking-wider">state/current.json</span>
                      </div>

                      {state ? (
                        <dl className="space-y-3 font-semibold text-zinc-700 dark:text-zinc-300">
                          {[
                            { k: 'Run ID', v: state.run_id },
                            { k: 'Project Key', v: state.project_key },
                            { k: 'Current Node', v: state.current_node },
                            { k: 'Next Node', v: state.next_node },
                            { k: 'Model Profile', v: state.model_profile },
                            { k: 'Delivery Mode', v: state.delivery_mode },
                            { k: 'Source Type', v: state.source_type },
                            { k: 'Source Ref', v: <FilePath path={state.source_ref} segments={2} /> },
                            { k: 'Context Memory Path', v: <FilePath path={state.context_packet_path} segments={2} /> },
                            { k: 'Task Ledger File', v: <FilePath path={state.artifacts?.task_ledger} segments={2} /> },
                          ].map((field, index) => (
                            <div key={index} className="flex justify-between items-center border-b border-[#F1F1F1] dark:border-zinc-900 pb-1.5 last:border-b-0">
                              <dt className="text-[#AFAFAF] uppercase text-[10px] tracking-wider">{field.k}</dt>
                              <dd className="font-mono text-[11px] truncate max-w-[200px] text-right font-bold text-zinc-800 dark:text-zinc-200">{field.v}</dd>
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <p className="text-zinc-400 italic text-center py-6">No run state loaded.</p>
                      )}
                    </div>

                    {/* PROJECT TARGET Card */}
                    <div className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl p-6 space-y-4">
                      <div className="border-b border-[#F1F1F1] pb-2 flex justify-between items-center">
                        <h3 className="font-extrabold text-sm text-zinc-850 dark:text-zinc-200">Project Target Config</h3>
                        <span className="text-[10px] text-[#AFAFAF] uppercase tracking-wider">Projects environment</span>
                      </div>

                      <dl className="space-y-3 font-semibold text-zinc-700 dark:text-zinc-300">
                        {[
                          { k: 'Workspace Root', v: <FilePath path={startContext.workspace} segments={2} /> },
                          { k: 'Model Config', v: <FilePath path={startContext.model_config} segments={2} /> },
                          { k: 'Project Config', v: <FilePath path={startContext.project_config} segments={2} /> },
                          { k: 'Project Base Folder', v: <FilePath path={startContext.project_base} segments={2} /> },
                          { k: 'Task Backend Mode', v: startContext.task_backend?.mode },
                          { k: 'Task Sync Destinations', v: startContext.task_backend?.sync?.join(', ') || 'Local only' },
                          { k: 'Worker Backend Mode', v: startContext.worker_backend?.mode },
                          { k: 'Deployment Backend', v: startContext.deployment_backend?.mode || 'None' },
                          { k: 'Delivery Contract Req', v: startContext.delivery_contract?.required ? 'Yes' : 'No' },
                          { k: 'Contract Min Gate', v: startContext.delivery_contract?.minimum_evidence || 'n/a' },
                        ].map((field, index) => (
                          <div key={index} className="flex justify-between items-center border-b border-[#F1F1F1] dark:border-zinc-900 pb-1.5 last:border-b-0">
                            <dt className="text-[#AFAFAF] uppercase text-[10px] tracking-wider">{field.k}</dt>
                            <dd className="font-mono text-[11px] truncate max-w-[200px] text-right font-bold text-zinc-800 dark:text-zinc-200">{field.v}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>

                  </div>
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
