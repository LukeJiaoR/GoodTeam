import { useCallback, useRef, useState } from 'react';

export function useWorkflowGraphData() {
  const [graphData, setGraphData] = useState(null);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [graphError, setGraphError] = useState(null);
  const graphRequestSeq = useRef(0);

  const fetchWorkflowGraph = useCallback(async (key) => {
    const seq = graphRequestSeq.current + 1;
    graphRequestSeq.current = seq;

    setLoadingGraph(true);
    setGraphError(null);
    try {
      const response = await fetch(`/api/workflow/graph?project=${encodeURIComponent(key)}`);
      const payload = await response.json().catch(() => ({}));

      if (graphRequestSeq.current !== seq) return;

      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch workflow graph (HTTP ${response.status})`);
      }
      setGraphData(payload);
    } catch (err) {
      if (graphRequestSeq.current === seq) {
        console.error('Error fetching workflow graph:', err);
        setGraphError(err.message);
      }
    } finally {
      if (graphRequestSeq.current === seq) {
        setLoadingGraph(false);
      }
    }
  }, []);

  return { graphData, loadingGraph, graphError, fetchWorkflowGraph };
}
