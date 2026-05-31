import { useState, useEffect } from 'react';
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
  ChevronDown,
  Info,
  ExternalLink,
  Lock,
  Unlock,
  CheckCircle,
  XCircle,
  Clock,
  FileCode,
  Brain
} from 'lucide-react';

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
  const [savingBackends, setSavingBackends] = useState(false);

  
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
  const [roleModelOverrides, setRoleModelOverrides] = useState('');
  const [docsPath, setDocsPath] = useState('');
  const [backendPath, setBackendPath] = useState('');
  const [frontendPath, setFrontendPath] = useState('');
  const [projectConfig, setProjectConfig] = useState('');
  const [taskSync, setTaskSync] = useState('');
  const [workerBackendMode, setWorkerBackendMode] = useState('codex');
  const [deploymentBackendMode, setDeploymentBackendMode] = useState('');
  const [startMode, setStartMode] = useState('existing_project'); // existing_project or new_project
  const [dryRun, setDryRun] = useState(true);

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

  // Approval review artifact editor state
  const [editingArtifact, setEditingArtifact] = useState(null); // { key, content }
  const [editorContent, setEditorContent] = useState('');

  // Memory visualization states
  const [memoryData, setMemoryData] = useState(null);
  const [loadingMemory, setLoadingMemory] = useState(false);
  const [memoryError, setMemoryError] = useState(null);



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
        
        // Repos paths
        const target = sc.project_report?.project || {};
        const repos = target.repos || {};
        setDocsPath(repos.docs || '');
        setBackendPath(repos.backend || '');
        setFrontendPath(repos.frontend || '');

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
  }, [projectKey, data]);

  // Fetch state on mount and projectKey changes
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchState(projectKey, true);
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

  // Lazy fetch memory data when the Memory tab is opened
  useEffect(() => {
    if (activeTab === 'memory' && projectKey) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      fetchMemory(projectKey);
    }
  }, [activeTab, projectKey]);

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

      const response = await fetch('/api/project-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_name: projectName,
          project_key: startProjectKey || projectKey,
          docs_path: docsPath,
          backend_path: backendPath,
          frontend_path: frontendPath,
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
    setActionLoading(true);
    showToast('Starting workflow...');
    try {
      const overrides = roleModelOverrides
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);

      const response = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idea: idea.trim(),
          project_key: startProjectKey || projectKey,
          model_profile: modelProfile,
          delivery_mode: deliveryMode,
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
    }
  };

  const handleApproveDecision = async (approvalKey, decision) => {
    setActionLoading(true);
    try {
      const response = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          approval: approvalKey,
          decision,
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to submit decision');
      showToast(`Decision '${decision}' submitted for ${approvalKey.toUpperCase()}`);
      fetchState(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
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

  const handleSaveWorkerBackends = async () => {
    setSavingBackends(true);
    try {
      // Build workers object containing ONLY explicit overrides for managed roles (exclude empty strings or "default")
      const workers = {};
      const managedRoles = data?.start_context?.worker_roles || DEFAULT_WORKER_ROLES;
      managedRoles.forEach((role) => {
        const backend = panelRoleOverrides[role];
        if (backend && backend !== 'default' && backend !== '') {
          workers[role] = backend;
        }
      });

      const response = await fetch('/api/project-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project: projectKey,
          worker_backend_mode: panelGlobalMode,
          workers: workers,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to save worker backends');
      }
      showToast('Worker backends updated successfully');
      await fetchState(projectKey, false); // Fetch state without full screen loader
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setSavingBackends(false);
    }
  };

  // Returns first two sentences of the idea prompt for default display
  const getMissionSummary = (text) => {
    if (!text) return '';
    const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
    if (sentences.length <= 2) return text;
    return sentences.slice(0, 2).join(' ');
  };

  // Render project overview (Multi-project control room)
  if (!projectKey) {
    const projectItems = data?.project_items || [];
    const legacyState = data?.legacy_state;

    return (
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
                    className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${startMode === 'new_project' ? 'bg-white dark:bg-zinc-850 shadow-sm text-[#3D8B00] dark:text-emerald-400' : 'text-[#AFAFAF] hover:text-zinc-800 dark:hover:text-zinc-200'}`}
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
                      <option value="init_project">Init project / production starter</option>
                      <option value="mvp">MVP / validation slice</option>
                      <option value="multi_service">Multi-service product</option>
                    </select>
                  </div>

                  <div className="col-span-2 border-t border-zinc-100 dark:border-zinc-900 my-4" />

                  {/* Toggle Advanced Button */}
                  <div className="col-span-2">
                    <button
                      type="button"
                      onClick={() => setShowConfigDrawer(!showConfigDrawer)}
                      className="text-xs font-bold text-[#3D8B00] hover:text-[#58CC02] transition-colors flex items-center space-x-1"
                    >
                      <span>{showConfigDrawer ? 'Hide Advanced Config' : 'Show Advanced Config (Repo Paths, Task Backend, Deployment)'}</span>
                      <ChevronDown className={`h-4 w-4 transition-transform ${showConfigDrawer ? 'rotate-180' : ''}`} />
                    </button>
                  </div>

                  {showConfigDrawer && (
                    <>
                      <div className="col-span-2 grid grid-cols-1 md:grid-cols-3 gap-4 border-2 border-[#E5E5E5] dark:border-zinc-800 p-5 rounded-2xl bg-[#F7F7F7] dark:bg-zinc-900/30">
                        <div className="col-span-3 font-extrabold text-[10px] text-[#AFAFAF] uppercase tracking-wider">Repository Paths</div>
                        <div>
                          <label className="block text-[10px] text-zinc-550 mb-1 uppercase">Docs Dir</label>
                          <input
                            type="text"
                            value={docsPath}
                            onChange={(e) => setDocsPath(e.target.value)}
                            placeholder="Absolute path to docs repo"
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-[10px] text-zinc-550 mb-1 uppercase">Backend Dir</label>
                          <input
                            type="text"
                            value={backendPath}
                            onChange={(e) => setBackendPath(e.target.value)}
                            placeholder="Absolute path to backend repo"
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-[10px] text-zinc-550 mb-1 uppercase">Frontend Dir</label>
                          <input
                            type="text"
                            value={frontendPath}
                            onChange={(e) => setFrontendPath(e.target.value)}
                            placeholder="Absolute path to frontend repo"
                            className="w-full bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs font-mono"
                          />
                        </div>
                      </div>

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
                      disabled={actionLoading}
                      className="w-full bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_4px_0_#3D8B00] text-white font-bold py-3 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center space-x-2"
                    >
                      <Play className="h-3.5 w-3.5 fill-current" />
                      <span>{dryRun ? 'Preview Workflow' : (startMode === 'existing_project' ? 'Start Adoption' : 'Start New Project')}</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // Dashboard Detailed View for a single project
  const state = data?.state;
  const startContext = data?.start_context || {};
  const backendOptions = startContext.worker_backend_options || DEFAULT_BACKEND_OPTIONS;
  const workerRoles = startContext.worker_roles || DEFAULT_WORKER_ROLES;
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
            { id: 'roles', label: 'Roles', icon: Users },
            { id: 'artifacts', label: 'Artifacts', icon: FileCode },
            { id: 'actions', label: 'Actions', icon: Activity },
            { id: 'board', label: 'Agile Board', icon: Kanban },
            { id: 'smoke', label: 'Smoke Runs', icon: PlaySquare },
            { id: 'workers', label: 'Workers', icon: Cpu },
            { id: 'memory', label: 'Memory', icon: Brain },
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
                          <button onClick={() => setStartMode('new_project')} className={`px-3 py-1.5 rounded-lg font-bold transition-all ${startMode === 'new_project' ? 'bg-white dark:bg-zinc-850 shadow-sm text-[#3D8B00]' : 'text-[#AFAFAF] hover:text-zinc-850'}`}>Start New Project</button>
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
                              <option value="init_project">Init project / production starter</option>
                              <option value="mvp">MVP / validation slice</option>
                              <option value="multi_service">Multi-service product</option>
                            </select>
                          </div>
                          
                          <div className="col-span-2 border-t-2 border-[#E5E5E5] dark:border-zinc-850 my-2" />

                          <div className="col-span-2 flex items-center justify-between">
                            <div className="flex items-center space-x-2">
                              <input type="checkbox" id="run-dry" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="rounded text-[#58CC02] focus:ring-[#58CC02] h-4 w-4 cursor-pointer" />
                              <label htmlFor="run-dry" className="font-bold text-zinc-500 uppercase tracking-wider text-[10px]">Dry run simulation</label>
                            </div>
                            <button
                              onClick={handleStartWorkflow}
                              disabled={actionLoading}
                              className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold px-6 py-2.5 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-2"
                            >
                              <Play className="h-3.5 w-3.5 fill-current" />
                              <span>{dryRun ? 'Preview Workflow' : 'Start Workflow'}</span>
                            </button>
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
                          {workerRoles.map((role) => (
                            <div key={role} className="flex items-center justify-between space-x-4 border-b border-[#F1F1F1] dark:border-zinc-900 pb-2.5">
                              <span className="text-xs font-bold text-zinc-700 dark:text-zinc-300 capitalize">{role} Role</span>
                              <select
                                value={panelRoleOverrides[role] || ''}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setPanelRoleOverrides(prev => ({
                                    ...prev,
                                    [role]: val
                                  }));
                                }}
                                disabled={savingBackends || actionLoading}
                                className="w-48 bg-white dark:bg-zinc-900 border-2 border-[#E5E5E5] dark:border-zinc-800 rounded-xl px-3 py-1.5 text-xs font-bold focus:outline-none focus:border-[#58CC02] transition-colors"
                              >
                                <option value="">Use default</option>
                                {backendOptions.map((opt) => (
                                  <option key={opt} value={opt}>
                                    {opt === 'codex' ? 'Codex CLI' : opt === 'anthropic' || opt === 'claude' ? 'Claude CLI' : opt}
                                  </option>
                                ))}
                              </select>
                            </div>
                          ))}
                        </div>

                        {/* Save Button */}
                        <div className="pt-2 flex justify-end">
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
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleApproveDecision(review.approval, 'approved');
                                  }}
                                  disabled={actionLoading}
                                  className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold text-[10px] uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                >
                                  <Check className="h-3 w-3" />
                                  <span>Approve</span>
                                </button>
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
                                <div className="bg-[#F7F7F7] dark:bg-zinc-950 px-6 py-4 border-t-2 border-[#E5E5E5] dark:border-zinc-900 flex justify-end space-x-2">
                                  <button
                                    type="button"
                                    onClick={() => handleApproveDecision(selectedReview.approval, 'rejected')}
                                    disabled={actionLoading}
                                    className="bg-white hover:bg-[#FFE2E2] border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-[#FF4B4B] font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics"
                                  >
                                    Reject
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleApproveDecision(selectedReview.approval, 'changes_requested')}
                                    disabled={actionLoading}
                                    className="bg-white hover:bg-[#FFF0D4] border-2 border-[#E5E5E5] shadow-[0_3px_0_#D1D1D1] text-[#FF9600] font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics"
                                  >
                                    Request Changes
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleApproveDecision(selectedReview.approval, 'approved')}
                                    disabled={actionLoading}
                                    className="bg-[#58CC02] border-2 border-[#3D8B00] shadow-[0_3px_0_#3D8B00] text-white font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                  >
                                    <Check className="h-4 w-4" />
                                    <span>Approve Gate</span>
                                  </button>
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

              {/* Tab: Memory */}
              {activeTab === 'memory' && (
                <div className="space-y-6">
                  <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
                    <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Compounding Memory Layer</h2>
                    <p className="text-xs text-[#AFAFAF]">Long-term agent memory horizons and the semantic facts retrieved for the latest worker dispatches.</p>
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
                      {memoryData.roles.map((role) => (
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
                            <span className="bg-[#E8F9D9] dark:bg-emerald-950/30 text-[#3D8B00] dark:text-emerald-400 border-2 border-[#58CC02]/20 dark:border-emerald-500/20 px-3 py-1 rounded-xl text-[10px] font-extrabold tracking-wider uppercase">
                              {role.event_count || 0} events
                            </span>
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
                                              <span className="bg-zinc-100 dark:bg-zinc-900 text-zinc-700 dark:text-zinc-350 px-2 py-0.5 rounded font-mono border border-zinc-200 dark:border-zinc-800">
                                                {event.id}
                                              </span>
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
                      ))}
                    </div>
                  )}
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
