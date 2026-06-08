import { useCallback, useEffect, useRef, useState } from 'react';

export function useCrossProjectMemorySearch(projectKey, activeTab, agentsMemorySubTab) {
  const [crossRole, setCrossRole] = useState('implementation');
  const [crossQuery, setCrossQuery] = useState('');
  const [crossScope, setCrossScope] = useState('all');
  const [crossLimit, setCrossLimit] = useState(5);
  const [crossLoading, setCrossLoading] = useState(false);
  const [crossError, setCrossError] = useState(null);
  const [crossResults, setCrossResults] = useState(null);
  const [allProjectKeys, setAllProjectKeys] = useState([]);
  const crossSearchRequestSeq = useRef(0);

  useEffect(() => {
    if (activeTab === 'memory' || (activeTab === 'agents_memory' && agentsMemorySubTab === 'memory')) {
      fetch('/api/state')
        .then((res) => {
          if (!res.ok) throw new Error('Failed to load project keys');
          return res.json();
        })
        .then((payload) => {
          if (payload && Array.isArray(payload.project_items)) {
            const keys = payload.project_items.map((item) => item.project_key).filter(Boolean);
            setAllProjectKeys(keys);
          }
        })
        .catch((err) => {
          console.error('Error fetching all project keys:', err);
        });
    }
  }, [activeTab, agentsMemorySubTab]);

  const handleCrossSearch = useCallback(async (e) => {
    if (e) e.preventDefault();
    if (!crossRole.trim() || !crossQuery.trim()) {
      setCrossError('Role and query are required.');
      return;
    }

    const seq = crossSearchRequestSeq.current + 1;
    crossSearchRequestSeq.current = seq;

    setCrossLoading(true);
    setCrossError(null);
    setCrossResults(null);

    try {
      let scopeVal = crossScope.trim();
      if (!scopeVal || scopeVal.toLowerCase() === 'all') {
        if (allProjectKeys.length > 0) {
          scopeVal = allProjectKeys.join(',');
        } else if (projectKey) {
          scopeVal = projectKey;
        } else {
          scopeVal = 'all';
        }
      }

      const params = new URLSearchParams({
        role: crossRole.trim(),
        q: crossQuery.trim(),
        scope: scopeVal,
        k: String(crossLimit),
      });

      const response = await fetch(`/api/memory/cross?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));

      if (crossSearchRequestSeq.current !== seq) return;

      if (!response.ok) {
        throw new Error(payload.error || `Failed to fetch cross-project memory (HTTP ${response.status})`);
      }

      const results = Array.isArray(payload.results) ? payload.results : [];
      setCrossResults(results);
    } catch (err) {
      if (crossSearchRequestSeq.current === seq) {
        console.error('Error in cross-project memory search:', err);
        setCrossError(err.message);
      }
    } finally {
      if (crossSearchRequestSeq.current === seq) {
        setCrossLoading(false);
      }
    }
  }, [allProjectKeys, crossLimit, crossQuery, crossRole, crossScope, projectKey]);

  return {
    crossRole,
    setCrossRole,
    crossQuery,
    setCrossQuery,
    crossScope,
    setCrossScope,
    crossLimit,
    setCrossLimit,
    crossLoading,
    crossError,
    crossResults,
    handleCrossSearch,
  };
}
