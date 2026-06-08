import { useCallback, useEffect, useMemo, useState } from 'react';

const EMPTY_GATE_DEBATE = { key: '', messages: [], loading: false, error: '' };
const EMPTY_MESSAGE_FEED = { key: '', messages: [], loading: false, error: '' };

export function useMessageFeeds({ activeTab, projectKey, data, actionableGateReview }) {
  const [gateDebate, setGateDebate] = useState(EMPTY_GATE_DEBATE);
  const [debateRun, setDebateRun] = useState({ key: '', loading: false, error: '' });
  const [messageFeed, setMessageFeed] = useState(EMPTY_MESSAGE_FEED);

  const gateDebateRunId = data?.state?.run_id || '';
  const gateDebateProjectKey = projectKey || data?.state?.project_key || '';
  const messageFeedProjectKey = projectKey || data?.state?.project_key || '';
  const messageFeedRunId = data?.state?.run_id || '';
  const targetMessageFeedKey = activeTab === 'messages' && messageFeedProjectKey && messageFeedRunId
    ? `${messageFeedProjectKey}:${messageFeedRunId}`
    : '';
  const targetGateDebateKey = activeTab === 'approvals' && actionableGateReview?.is_actionable && gateDebateProjectKey && gateDebateRunId
    ? `${gateDebateProjectKey}:${gateDebateRunId}:${actionableGateReview.approval}`
    : '';
  const visibleGateDebate = gateDebate.key === targetGateDebateKey ? gateDebate : EMPTY_GATE_DEBATE;

  const fetchGateDebate = useCallback(async ({ key, runId, gate }) => {
    const debateKey = `${key}:${runId}:${gate}`;
    setGateDebate({ key: debateKey, messages: [], loading: true, error: '' });
    try {
      const params = new URLSearchParams({ project: key, run: runId, gate });
      const response = await fetch(`/api/messages?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to fetch gate debate');
      }
      setGateDebate({ key: debateKey, messages: payload.messages || [], loading: false, error: '' });
    } catch (err) {
      setGateDebate({ key: debateKey, messages: [], loading: false, error: err.message });
    }
  }, []);

  const fetchMessageFeed = useCallback(async ({ key, runId }) => {
    const feedKey = `${key}:${runId}`;
    setMessageFeed({ key: feedKey, messages: [], loading: true, error: '' });
    try {
      const params = new URLSearchParams({ project: key, run: runId });
      const response = await fetch(`/api/messages?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to fetch message feed');
      }
      setMessageFeed({ key: feedKey, messages: payload.messages || [], loading: false, error: '' });
    } catch (err) {
      setMessageFeed({ key: feedKey, messages: [], loading: false, error: err.message });
    }
  }, []);

  const handleRunDebate = useCallback(async (approvalKey) => {
    const key = projectKey || data?.state?.project_key || '';
    const runId = data?.state?.run_id || '';
    const debateKey = `${key}:${runId}:${approvalKey}`;

    if (!key || !runId || !approvalKey) {
      setDebateRun({ key: debateKey, loading: false, error: 'Missing project, run, or gate for debate.' });
      return;
    }

    setDebateRun({ key: debateKey, loading: true, error: '' });
    try {
      const response = await fetch('/api/debate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: key,
          run: runId,
          gate: approvalKey,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to run debate');
      }
      await fetchGateDebate({ key, runId, gate: approvalKey });
      setDebateRun((current) => (
        current.key === debateKey ? { key: debateKey, loading: false, error: '' } : current
      ));
    } catch (err) {
      setDebateRun((current) => (
        current.key === debateKey ? { key: debateKey, loading: false, error: err.message } : current
      ));
    }
  }, [data, fetchGateDebate, projectKey]);

  useEffect(() => {
    if (!targetMessageFeedKey || messageFeed.key === targetMessageFeedKey) return;
    Promise.resolve().then(() => {
      fetchMessageFeed({ key: messageFeedProjectKey, runId: messageFeedRunId });
    });
  }, [fetchMessageFeed, targetMessageFeedKey, messageFeed.key, messageFeedProjectKey, messageFeedRunId]);

  useEffect(() => {
    if (!targetGateDebateKey || gateDebate.key === targetGateDebateKey) return;
    Promise.resolve().then(() => {
      fetchGateDebate({ key: gateDebateProjectKey, runId: gateDebateRunId, gate: actionableGateReview.approval });
    });
  }, [
    actionableGateReview?.approval,
    fetchGateDebate,
    targetGateDebateKey,
    gateDebate.key,
    gateDebateProjectKey,
    gateDebateRunId,
  ]);

  const getResolvingDebateRef = useCallback((approvalKey) => {
    const runId = data?.state?.run_id || '';
    if (!runId || !visibleGateDebate.messages.length || !visibleGateDebate.key.endsWith(`:${approvalKey}`)) return '';
    const resolving = [...visibleGateDebate.messages].reverse().find((item) => ['assert', 'propose'].includes(item.kind))
      || visibleGateDebate.messages[visibleGateDebate.messages.length - 1];
    return resolving?.id ? `messages/${runId}.jsonl#${resolving.id}` : '';
  }, [data, visibleGateDebate]);

  return useMemo(() => ({
    gateDebateProjectKey,
    gateDebateRunId,
    debateRun,
    visibleGateDebate,
    messageFeedProjectKey,
    messageFeedRunId,
    messageFeed,
    targetMessageFeedKey,
    handleRunDebate,
    getResolvingDebateRef,
  }), [
    debateRun,
    gateDebateProjectKey,
    gateDebateRunId,
    getResolvingDebateRef,
    handleRunDebate,
    messageFeed,
    messageFeedProjectKey,
    messageFeedRunId,
    targetMessageFeedKey,
    visibleGateDebate,
  ]);
}
