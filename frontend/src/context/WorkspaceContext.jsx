/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api } from '../api';

const ACTIVE_PROJECT_STORAGE_KEY = 'goodteam.v2.activeProjectId';

const WorkspaceContext = createContext({
  projects: [],
  projectsLoading: true,
  projectsError: '',
  activeProjectId: '',
  activeProject: null,
  setActiveProjectId: () => {},
  refreshProjects: async () => {},
  pendingDeliveries: 0,
  setPendingDeliveries: () => {},
  sseStatus: 'idle',
  setSseStatus: () => {},
});

export function useWorkspace() {
  return useContext(WorkspaceContext);
}

export function WorkspaceProvider({ children, initialProjectId = '' }) {
  const [projects, setProjects] = useState([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState('');
  const [activeProjectId, setActiveProjectIdState] = useState(() => {
    if (initialProjectId) return initialProjectId;
    try {
      return localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY) || '';
    } catch {
      return '';
    }
  });
  const [pendingDeliveries, setPendingDeliveries] = useState(0);
  const [sseStatus, setSseStatus] = useState('idle');

  const setActiveProjectId = useCallback((projectId) => {
    const next = projectId || '';
    setActiveProjectIdState(next);
    try {
      if (next) localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, next);
      else localStorage.removeItem(ACTIVE_PROJECT_STORAGE_KEY);
    } catch {
      // Ignore storage failures.
    }
  }, []);

  const refreshProjects = useCallback(async () => {
    setProjectsLoading(true);
    setProjectsError('');
    try {
      const list = await api.listProjects();
      setProjects(list);
      setActiveProjectIdState((current) => {
        if (current && list.some((p) => p.id === current)) return current;
        const first = list[0]?.id || '';
        try {
          if (first) localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, first);
        } catch {
          // Ignore storage failures.
        }
        return first;
      });
      return list;
    } catch (err) {
      setProjectsError(err.message || '加载项目列表失败');
      return [];
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  useEffect(() => {
    const id = setTimeout(() => {
      refreshProjects();
    }, 0);
    return () => clearTimeout(id);
  }, [refreshProjects]);

  const activeProject = useMemo(
    () => projects.find((item) => item.id === activeProjectId) || null,
    [projects, activeProjectId],
  );

  const value = useMemo(
    () => ({
      projects,
      projectsLoading,
      projectsError,
      activeProjectId,
      activeProject,
      setActiveProjectId,
      refreshProjects,
      pendingDeliveries,
      setPendingDeliveries,
      sseStatus,
      setSseStatus,
    }),
    [
      projects,
      projectsLoading,
      projectsError,
      activeProjectId,
      activeProject,
      setActiveProjectId,
      refreshProjects,
      pendingDeliveries,
      sseStatus,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
