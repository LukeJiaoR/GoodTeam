import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { reviewApi } from './reviewApi';
import { createReviewDraft, reviewDraftReducer, reviewNeedsFullArtifact } from './reviewDraft';

function draftIdentity(projectKey, runId, artifact) {
  return JSON.stringify([projectKey, runId, artifact]);
}

export default function useReviewArtifactDraft(projectKey, runId, review, { getArtifact = reviewApi.getArtifact } = {}) {
  const artifact = review?.artifact || '';
  const identity = useMemo(() => draftIdentity(projectKey, runId, artifact), [artifact, projectKey, runId]);
  const preview = typeof review?.content === 'string' ? review.content : '';
  const version = String(review?.artifact_version || '');
  const needsFull = reviewNeedsFullArtifact(review);
  const [draft, dispatch] = useReducer(
    reviewDraftReducer,
    { identity, runId, review },
    ({ identity: initialIdentity, runId: initialRunId, review: initialReview }) => (
      createReviewDraft(initialIdentity, initialRunId, initialReview)
    ),
  );
  const current = draft.identity === identity ? draft : createReviewDraft(identity, runId, review);
  const [retryKey, setRetryKey] = useState(0);
  const requestSequence = useRef(0);
  const lastForcedRead = useRef(0);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) dispatch({ type: 'receive', identity, runId, review });
    });
    return () => { active = false; };
  }, [identity, needsFull, preview, review, runId, version]);

  useEffect(() => {
    const forcedRead = retryKey > lastForcedRead.current;
    if (!artifact || (current.full && !forcedRead)) return undefined;
    if (forcedRead) lastForcedRead.current = retryKey;
    const controller = new AbortController();
    let active = true;
    const requestId = ++requestSequence.current;
    queueMicrotask(() => {
      if (active) dispatch({ type: 'loadStarted', identity, requestId });
    });
    Promise.resolve()
      .then(() => getArtifact(projectKey, artifact, { signal: controller.signal }))
      .then((payload) => {
        if (requestId !== requestSequence.current) return;
        dispatch({ type: 'loaded', identity, requestId, payload });
      })
      .catch((error) => {
        if (controller.signal.aborted || error?.name === 'AbortError') return;
        if (requestId === requestSequence.current) {
          dispatch({ type: 'loadFailed', identity, requestId, error });
        }
      });
    return () => {
      active = false;
      controller.abort();
      requestSequence.current += 1;
    };
  }, [artifact, current.full, getArtifact, identity, preview, projectKey, retryKey, version]);

  const setContent = useCallback((content) => {
    dispatch({ type: 'change', identity, content });
  }, [identity]);
  const acceptSavedArtifact = useCallback((payload, submittedContent, expectedIdentity = identity, originalContent = submittedContent) => {
    dispatch({ type: 'saved', identity: expectedIdentity, payload, submittedContent, originalContent });
  }, [identity]);
  const beginSavedReadback = useCallback((submittedContent, expectedIdentity = identity, originalContent = submittedContent) => {
    dispatch({ type: 'savePendingReadback', identity: expectedIdentity, submittedContent, originalContent });
  }, [identity]);
  const setError = useCallback((error) => {
    dispatch({ type: 'error', identity, error });
  }, [identity]);
  const reload = useCallback(() => {
    dispatch({ type: 'reload', identity, runId, review });
    setRetryKey((value) => value + 1);
  }, [identity, review, runId]);
  const retryFullRead = useCallback(() => {
    dispatch({ type: 'retry', identity });
    setRetryKey((value) => value + 1);
  }, [identity]);

  return {
    acceptSavedArtifact,
    beginSavedReadback,
    artifactVersion: current.artifactVersion,
    conflict: current.conflict,
    content: current.content,
    error: current.error,
    full: current.full,
    identity,
    loading: current.loading,
    pendingReadback: Boolean(current.pendingReadback),
    reload,
    retryFullRead,
    runId,
    setContent,
    setError,
    baseline: current.baseline,
  };
}
