/**
 * [INPUT]: 接收模型目录 API 和当前编辑的执行器。
 * [OUTPUT]: 提供页面内目录与按执行器加载入口。
 * [POS]: Team 与任务助手设置共用请求状态。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { catalogProvider } from '../../lib/api/globalApi';
import { finishCatalog, refreshCatalog } from './catalogState';

const EMPTY = { agents: [], error: null, failedProvider: '' };

export default function useAgentCatalog(api) {
  const [snapshot, setSnapshot] = useState(EMPTY);
  const { agents, error, failedProvider } = snapshot.api === api ? snapshot : EMPTY;
  const inFlight = useRef(new Map());

  useEffect(() => {
    const requests = inFlight.current;
    return () => {
      for (const controller of requests.values()) controller.abort();
      requests.clear();
    };
  }, [api]);

  const loadCatalog = useCallback(async (value, { refresh = false } = {}) => {
    const provider = catalogProvider(value);
    const requests = inFlight.current;
    if (!provider || requests.has(provider) || (!refresh && agents.some((agent) => agent.backend === provider))) return;
    const controller = new AbortController();
    requests.set(provider, controller);
    const update = (transform, error = null) => setSnapshot((previous) => ({
      api, agents: transform(previous.api === api ? previous.agents : []), error, failedProvider: error ? provider : '',
    }));
    update((previous) => refreshCatalog(previous, provider));
    try {
      const payload = await api.getSettingsAgents({ provider, signal: controller.signal });
      if (!controller.signal.aborted) {
        const received = Array.isArray(payload?.agents) ? payload.agents.filter((agent) => agent.backend === provider) : [];
        update((previous) => finishCatalog(previous, received, provider));
      }
    } catch (error) {
      if (!controller.signal.aborted) update((previous) => finishCatalog(previous, [], provider), error);
    } finally {
      if (requests.get(provider) === controller) requests.delete(provider);
    }
  }, [agents, api]);

  return { agents, error, failedProvider, loadCatalog };
}
