import { useCallback, useEffect, useRef, useState } from 'react';
import { runApi } from './runApi';

const POLL_MS = 5_000;

export default function useProjectState(projectKey, { api = runApi, poll = true } = {}) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const generation = useRef(0);

  const refresh = useCallback(async ({ signal, quiet = false } = {}) => {
    const requestGeneration = ++generation.current;
    if (!quiet) setStatus((current) => (data ? current : 'loading'));
    try {
      const payload = await api.getProjectState(projectKey, { signal });
      if (signal?.aborted || requestGeneration !== generation.current) return null;
      setData(payload);
      setError(null);
      setStatus('ready');
      return payload;
    } catch (caught) {
      if (signal?.aborted || caught?.name === 'AbortError') return null;
      if (requestGeneration !== generation.current) return null;
      setError(caught);
      setStatus(data ? 'stale' : 'error');
      return null;
    }
  }, [api, data, projectKey]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => void refresh({ signal: controller.signal }));
    return () => {
      generation.current += 1;
      controller.abort();
    };
  }, [projectKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!poll || !data?.active_run || globalThis.document?.hidden) return undefined;
    const controller = new AbortController();
    const timer = globalThis.setInterval(() => {
      if (!globalThis.document?.hidden) void refresh({ signal: controller.signal, quiet: true });
    }, POLL_MS);
    return () => {
      globalThis.clearInterval(timer);
      controller.abort();
    };
  }, [data?.active_run, poll, refresh]);

  return { data, error, refresh, status };
}
