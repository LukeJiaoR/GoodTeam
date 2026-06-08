import { useCallback, useEffect, useRef, useState } from 'react';

export function useMemoryData(projectKey) {
  const [memoryData, setMemoryData] = useState(null);
  const [loadingMemory, setLoadingMemory] = useState(false);
  const [memoryError, setMemoryError] = useState(null);
  const [memoryEventDetail, setMemoryEventDetail] = useState(null);
  const [memoryEventBrowsers, setMemoryEventBrowsers] = useState({});
  const memoryBrowseTimers = useRef({});
  const memoryBrowseRequestSeq = useRef({});

  const fetchMemory = useCallback(async (key) => {
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
  }, []);

  const handleOpenMemoryEvent = useCallback(async (eventId) => {
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
  }, [projectKey]);

  const fetchMemoryEvents = useCallback(async (roleKey, searchQuery = '') => {
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
  }, [projectKey]);

  const handleToggleMemoryEvents = useCallback((roleKey) => {
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
  }, [fetchMemoryEvents, memoryEventBrowsers]);

  const handleMemoryEventSearchChange = useCallback((roleKey, value) => {
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
  }, [fetchMemoryEvents]);

  useEffect(() => () => {
    Object.values(memoryBrowseTimers.current).forEach((timer) => clearTimeout(timer));
  }, []);

  return {
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
  };
}
