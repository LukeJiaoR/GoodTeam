import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  DEFAULT_BACKEND_OPTIONS,
  DEFAULT_WORKER_ROLES,
  FALLBACK_AGENT_MODES,
  MODEL_UNAVAILABLE_BACKENDS,
  WORKER_ROUTING_BUCKETS,
  canonicalBackendForOption,
} from '../constants/workers';

export function useWorkerBackendSettings({
  activeTab,
  agentsMemorySubTab,
  data,
  projectKey,
  showToast,
  fetchState,
}) {
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

  const startContext = useMemo(() => data?.start_context || {}, [data]);
  const backendOptions = useMemo(
    () => startContext.worker_backend_options || DEFAULT_BACKEND_OPTIONS,
    [startContext]
  );
  const workerRoles = useMemo(
    () => startContext.worker_roles || DEFAULT_WORKER_ROLES,
    [startContext]
  );

  const safeDiscoveredAgents = useMemo(() => (
    (discoveredAgents || []).reduce((acc, agent) => {
      const backend = canonicalBackendForOption(String(agent?.backend || '').trim());
      if (!backend || acc.seen.has(backend)) return acc;
      acc.seen.add(backend);
      acc.items.push({ ...agent, backend });
      return acc;
    }, { seen: new Set(), items: [] }).items
  ), [discoveredAgents]);

  const discoveredAgentByBackend = useMemo(() => {
    const byBackend = {};
    safeDiscoveredAgents.forEach((agent) => {
      if (agent?.backend) {
        byBackend[agent.backend] = agent;
      }
      if (Array.isArray(agent?.aliases)) {
        agent.aliases.forEach((alias) => {
          if (alias) {
            byBackend[alias] = agent;
            byBackend[canonicalBackendForOption(alias)] = agent;
          }
        });
      }
    });
    return byBackend;
  }, [safeDiscoveredAgents]);

  const roleBackendOptions = useMemo(() => {
    const discoveredBackendOptions = Array.from(new Set(
      safeDiscoveredAgents
        .map((agent) => agent?.backend)
        .filter(Boolean)
    ));
    return Array.from(
      [...backendOptions, ...discoveredBackendOptions]
        .reduce((acc, option) => {
          const canonical = canonicalBackendForOption(String(option || '').trim());
          if (canonical) acc.add(canonical);
          return acc;
        }, new Set())
    );
  }, [backendOptions, safeDiscoveredAgents]);

  const getBackendOptionLabel = useCallback((backend) => {
    const baseLabel = backend === 'codex'
      ? 'Codex CLI'
      : backend === 'anthropic' || backend === 'claude'
        ? 'Claude CLI'
        : backend;
    const discovered = discoveredAgentByBackend[backend];
    if (!discovered) return baseLabel;
    return `${baseLabel} ${discovered.ready ? '✓ ready' : '⚠ not-ready'}`;
  }, [discoveredAgentByBackend]);

  const usedBackends = useMemo(() => (
    Array.from(new Set([
      panelGlobalMode,
      ...workerRoles.map(role => panelRoleOverrides[role]).filter(b => b && b !== 'default' && b !== '')
    ])).filter(Boolean)
  ), [panelGlobalMode, panelRoleOverrides, workerRoles]);

  const detectCustomModelSelections = useCallback(({
    agents = discoveredAgents,
    roleModels = startContext.role_model_overrides || {},
    roleOverrides = panelRoleOverrides,
    globalMode = panelGlobalMode,
    context = startContext,
  } = {}) => {
    const customSelected = {};
    if (!agents) return customSelected;

    const agentMap = {};
    agents.forEach(a => {
      if (a && a.backend) {
        agentMap[a.backend] = a;
        if (a.aliases) {
          a.aliases.forEach(alias => {
            agentMap[alias] = a;
          });
        }
      }
    });
    const roles = context.worker_roles || DEFAULT_WORKER_ROLES;
    roles.forEach(role => {
      const currentModel = roleModels[role];
      const currentBackend = roleOverrides[role] || context.project_report?.role_worker_backends?.[role] || globalMode;
      const fallbackAgent = FALLBACK_AGENT_MODES[currentBackend];
      const agent = currentBackend ? agentMap[currentBackend] || (fallbackAgent ? { backend: currentBackend, ...fallbackAgent } : null) : null;
      if (agent && currentModel) {
        const models = agent.models || FALLBACK_AGENT_MODES[agent.backend]?.models || ["auto"];
        if (!models.includes(currentModel)) {
          customSelected[role] = true;
        }
      }
    });
    return customSelected;
  }, [discoveredAgents, panelGlobalMode, panelRoleOverrides, startContext]);

  const seedWorkerBackendSettings = useCallback((startContextPayload) => {
    setPanelGlobalMode(startContextPayload.worker_backend?.mode || 'codex');
    setPanelRoleOverrides(startContextPayload.project_report?.role_worker_backends || {});

    const initialModels = startContextPayload.role_model_overrides || {};
    setPanelRoleModels(initialModels);
    setBackendsError(null);
    setBackendsSuccess(null);
    setCustomModelSelected(detectCustomModelSelections({
      agents: discoveredAgents,
      roleModels: initialModels,
      roleOverrides: startContextPayload.project_report?.role_worker_backends || {},
      globalMode: startContextPayload.worker_backend?.mode || 'codex',
      context: startContextPayload,
    }));
  }, [detectCustomModelSelections, discoveredAgents]);

  const handleAgentChange = useCallback((role, newAgent) => {
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
  }, [discoveredAgentByBackend, panelGlobalMode]);

  const handleApplySimpleRouting = useCallback(() => {
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
  }, [panelRoleOverrides, simpleRoutingBackends, workerRoles]);

  const handleModelDropdownChange = useCallback((role, val) => {
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
  }, []);

  const handleCustomModelChange = useCallback((role, val) => {
    setPanelRoleModels(prev => ({
      ...prev,
      [role]: val
    }));
  }, []);

  useEffect(() => {
    if (discoveredAgents && data?.start_context?.role_model_overrides) {
      let cancelled = false;
      queueMicrotask(() => {
        if (cancelled) return;
        setCustomModelSelected(prev => ({
          ...detectCustomModelSelections({
            agents: discoveredAgents,
            roleModels: data.start_context.role_model_overrides,
            context: data.start_context,
          }),
          ...prev,
        }));
      });
      return () => {
        cancelled = true;
      };
    }
  }, [detectCustomModelSelections, discoveredAgents, data]);

  const handleSaveWorkerBackends = useCallback(async () => {
    setSavingBackends(true);
    setBackendsError(null);
    setBackendsSuccess(null);
    try {
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
      await fetchState(projectKey, true);
    } catch (err) {
      setBackendsError(err.message);
      showToast(err.message, true);
    } finally {
      setSavingBackends(false);
    }
  }, [
    data,
    discoveredAgentByBackend,
    fetchState,
    panelGlobalMode,
    panelRoleModels,
    panelRoleOverrides,
    projectKey,
    showToast,
  ]);

  const fetchDiscoveredAgents = useCallback(async () => {
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
  }, []);

  useEffect(() => {
    if ((activeTab === 'workers' || (activeTab === 'agents_memory' && agentsMemorySubTab === 'workers')) && discoveredAgents === null && !loadingDiscoveredAgents) {
      const timer = window.setTimeout(() => {
        fetchDiscoveredAgents();
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [activeTab, agentsMemorySubTab, discoveredAgents, fetchDiscoveredAgents, loadingDiscoveredAgents]);

  return {
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
    fetchDiscoveredAgents,
  };
}
