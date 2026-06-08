import { useCallback, useRef, useState } from 'react';

export function useDevLoopData() {
  const [devLoopData, setDevLoopData] = useState(null);
  const [loadingDevLoop, setLoadingDevLoop] = useState(false);
  const [devLoopError, setDevLoopError] = useState(null);
  const devLoopRequestSeq = useRef(0);

  const fetchDevLoop = useCallback(async (key) => {
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
  }, []);

  return { devLoopData, loadingDevLoop, devLoopError, fetchDevLoop };
}
