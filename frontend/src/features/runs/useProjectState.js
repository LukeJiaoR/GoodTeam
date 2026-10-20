import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { runApi } from './runApi';

const POLL_MS = 5_000;

export default function useProjectState(projectKey, { api = runApi, poll = true } = {}) {
  const [snapshot, setSnapshot] = useState({ projectKey: '', data: null, error: null, status: 'loading' });
  const generation = useRef(0);
  const pendingRequest = useRef(0);
  const activeProjectKey = useRef(projectKey);
  const apiRef = useRef(api);
  useLayoutEffect(() => {
    if (activeProjectKey.current !== projectKey) generation.current += 1;
    activeProjectKey.current = projectKey;
    apiRef.current = api;
  }, [api, projectKey]);
  const data = snapshot.projectKey === projectKey ? snapshot.data : null;
  const error = snapshot.projectKey === projectKey ? snapshot.error : null;
  const status = snapshot.projectKey === projectKey ? snapshot.status : 'loading';

  const refresh = useCallback(async ({ signal, quiet = false } = {}) => {
    const requestProjectKey = projectKey;
    if (requestProjectKey !== activeProjectKey.current) return null;
    const requestGeneration = ++generation.current;
    pendingRequest.current = requestGeneration;
    if (!quiet) {
      setSnapshot((current) => {
        const sameProject = current.projectKey === requestProjectKey;
        const currentData = sameProject ? current.data : null;
        return {
          projectKey: requestProjectKey,
          data: currentData,
          error: sameProject ? current.error : null,
          status: currentData ? current.status : 'loading',
        };
      });
    }
    try {
      const payload = await apiRef.current.getProjectState(requestProjectKey, { signal, goalLimit: 1 });
      if (
        signal?.aborted
        || requestGeneration !== generation.current
        || requestProjectKey !== activeProjectKey.current
      ) return null;
      setSnapshot({ projectKey: requestProjectKey, data: payload, error: null, status: 'ready' });
      return payload;
    } catch (caught) {
      if (signal?.aborted || caught?.name === 'AbortError') return null;
      if (requestGeneration !== generation.current || requestProjectKey !== activeProjectKey.current) return null;
      setSnapshot((current) => {
        const currentData = current.projectKey === requestProjectKey ? current.data : null;
        return {
          projectKey: requestProjectKey,
          data: currentData,
          error: caught,
          status: currentData ? 'stale' : 'error',
        };
      });
      return null;
    } finally {
      if (pendingRequest.current === requestGeneration) pendingRequest.current = 0;
    }
  }, [projectKey]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    queueMicrotask(() => {
      if (active) void refresh({ signal: controller.signal });
    });
    return () => {
      active = false;
      generation.current += 1;
      controller.abort();
    };
  }, [projectKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // A hidden tab still owns its polling lifecycle. A response can arrive while
  // it is hidden; visibility must not decide whether future polling exists.
  const hasLiveWork = Boolean(data?.active_run || data?.project_assistant || data?.task_assistant_session)
    || data?.team_workspace?.mission?.source === 'task_assistant';
  useEffect(() => {
    if (!poll || !hasLiveWork) return undefined;
    const controller = new AbortController();
    const timer = globalThis.setInterval(() => {
      if (!globalThis.document?.hidden && !pendingRequest.current) {
        void refresh({ signal: controller.signal, quiet: true });
      }
    }, POLL_MS);
    return () => {
      globalThis.clearInterval(timer);
      controller.abort();
    };
  }, [hasLiveWork, poll, refresh]);

  useEffect(() => {
    if (!poll || !globalThis.document) return undefined;
    const controller = new AbortController();
    const resume = () => {
      // Do not abort or overlap an initial read, manual refresh, or slow poll.
      // Refresh idle projects too: work may have started while the user was away.
      if (!document.hidden && !pendingRequest.current) {
        void refresh({ signal: controller.signal, quiet: true });
      }
    };
    document.addEventListener('visibilitychange', resume);
    return () => {
      document.removeEventListener('visibilitychange', resume);
      controller.abort();
    };
  }, [poll, refresh]);

  return { data, error, refresh, status };
}
