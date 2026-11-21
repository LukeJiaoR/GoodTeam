import { useEffect, useRef, useState } from 'react';

export const SSE_EVENT_TYPES = [
  'chat_message',
  'chat_delta',
  'chat_done',
  'run_updated',
  'run_event',
  'delivery_created',
  'autopilot_updated',
  'task_updated',
];

/**
 * Subscribe to a project's SSE event stream with safe JSON parsing and automatic reconnect.
 */
export function useProjectStream(projectId, onEvent) {
  const canStream = Boolean(projectId && typeof EventSource !== 'undefined');
  const [status, setStatus] = useState(canStream ? 'connecting' : 'idle');
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!canStream) {
      return undefined;
    }

    let closed = false;
    let source = null;
    let reconnectTimer = null;
    let attempt = 0;

    const url = `/api/projects/${encodeURIComponent(projectId)}/events`;

    const connect = () => {
      if (closed) return;
      if (attempt > 0) setStatus('reconnecting');
      source = new EventSource(url);

      source.onopen = () => {
        attempt = 0;
        if (!closed) setStatus('live');
      };

      const handleTypedEvent = (eventType) => (evt) => {
        let parsed = evt.data;
        try {
          parsed = JSON.parse(evt.data);
        } catch {
          // Keep raw string if payload is not JSON.
        }
        onEventRef.current?.(eventType, parsed);
      };

      for (const type of SSE_EVENT_TYPES) {
        source.addEventListener(type, handleTypedEvent(type));
      }

      source.onerror = () => {
        if (closed) return;
        setStatus('reconnecting');
        if (source && source.readyState === EventSource.CLOSED) {
          source.close();
          attempt += 1;
          const delay = Math.min(1000 * 2 ** Math.min(attempt - 1, 4), 10000);
          reconnectTimer = setTimeout(connect, delay);
        }
      };
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (source) source.close();
    };
  }, [canStream, projectId]);

  return canStream ? status : 'idle';
}
