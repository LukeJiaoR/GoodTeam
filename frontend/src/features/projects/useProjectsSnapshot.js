import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { normalizeProjectsPayload } from './projectList';

const refreshListeners = new Map();

// Invalidate mounted consumers of this API only; each keeps its existing
// abort, stale-data and retry behavior rather than sharing mutable snapshots.
export function notifyProjectsChanged(api) {
  refreshListeners.get(api)?.forEach(refresh => refresh());
}

export default function useProjectsSnapshot(api) {
  const [requestId, setRequestId] = useState(0);
  const [snapshot, setSnapshot] = useState(null);
  const current = useRef(null);
  useLayoutEffect(() => {
    current.current = { api, requestId };
    return () => { current.current = null; };
  }, [api, requestId]);
  useEffect(() => {
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && current.current?.api === api
      && current.current.requestId === requestId;
    Promise.resolve(api.listProjects({ signal: controller.signal }))
      .then(payload => {
        if (!isCurrent()) return;
        setSnapshot({ api, requestId, projects: normalizeProjectsPayload(payload), status: 'ready' });
      }).catch(error => {
        if (!isCurrent() || error?.name === 'AbortError') return;
        setSnapshot(previous => ({ api, requestId,
          projects: previous?.api === api ? previous.projects : null,
          status: previous?.api === api && previous.projects ? 'stale' : 'error' }));
      });
    return () => controller.abort();
  }, [api, requestId]);
  const projects = snapshot?.api === api ? snapshot.projects : null;
  const status = snapshot?.api === api && snapshot.requestId === requestId
    ? snapshot.status : projects ? 'refreshing' : 'loading';
  const refresh = useCallback(() => setRequestId(value => value + 1), []);
  useEffect(() => {
    const listeners = refreshListeners.get(api) || new Set();
    listeners.add(refresh);
    refreshListeners.set(api, listeners);
    return () => {
      listeners.delete(refresh);
      if (!listeners.size) refreshListeners.delete(api);
    };
  }, [api, refresh]);
  return { projects: projects || [], status, refresh };
}
