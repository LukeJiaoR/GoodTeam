const PREVIEW_TRUNCATED = /\[Preview truncated\]\s*$/;

export function isTruncatedPreview(content) {
  return typeof content === 'string' && PREVIEW_TRUNCATED.test(content);
}

export function reviewNeedsFullArtifact(review) {
  if (!review?.artifact) return false;
  return typeof review.content !== 'string'
    || review.content_truncated === true
    || isTruncatedPreview(review.content);
}

export function createReviewDraft(identity, runId, review) {
  const content = typeof review?.content === 'string' ? review.content : '';
  const full = !reviewNeedsFullArtifact(review);
  return {
    identity,
    runId,
    content,
    baseline: content,
    artifactVersion: String(review?.artifact_version || ''),
    full,
    loading: false,
    requestId: null,
    error: null,
    conflict: false,
    pendingReadback: null,
  };
}

function isDirty(draft) {
  return draft.content !== draft.baseline;
}

function artifactContent(payload) {
  return typeof payload?.content === 'string' ? payload.content : null;
}

function receiveReview(draft, action) {
  const incoming = createReviewDraft(action.identity, action.runId, action.review);
  if (draft.identity !== incoming.identity) return incoming;
  const versionChanged = incoming.artifactVersion
    && draft.artifactVersion
    && incoming.artifactVersion !== draft.artifactVersion;
  if (versionChanged) return isDirty(draft) ? { ...draft, conflict: true } : incoming;
  if (!draft.full && incoming.full && !isDirty(draft)) return incoming;
  if (!draft.artifactVersion && incoming.artifactVersion && !isDirty(draft)) {
    return { ...draft, artifactVersion: incoming.artifactVersion };
  }
  return draft;
}

function loadArtifact(draft, action) {
  if (draft.requestId !== action.requestId) return draft;
  const content = artifactContent(action.payload);
  if (content === null || isTruncatedPreview(content)) {
    return { ...draft, loading: false, error: new Error('The complete artifact could not be loaded.') };
  }
  if (action.payload.run_id && action.payload.run_id !== draft.runId) {
    return {
      ...draft,
      loading: false,
      conflict: true,
      error: new Error('The artifact belongs to a different run. Reload the review before editing.'),
    };
  }
  if (draft.pendingReadback) {
    return saveArtifact(draft, {
      payload: action.payload,
      ...draft.pendingReadback,
    });
  }
  if (isDirty(draft)) return { ...draft, loading: false, conflict: true };
  return {
    ...draft,
    content,
    baseline: content,
    artifactVersion: String(action.payload.artifact_version || draft.artifactVersion),
    full: true,
    loading: false,
    error: null,
    conflict: false,
  };
}

function saveArtifact(draft, action) {
  const content = artifactContent(action.payload);
  if (content === null || isTruncatedPreview(content)) {
    return { ...draft, loading: false, error: new Error('The saved artifact could not be reloaded.') };
  }
  if (action.payload.run_id && action.payload.run_id !== draft.runId) {
    return { ...draft, conflict: true, error: new Error('The artifact belongs to a different run. Reload the review.') };
  }
  const changedAfterSubmit = draft.content !== action.originalContent;
  return {
    ...draft,
    content: changedAfterSubmit ? draft.content : action.submittedContent,
    baseline: content,
    artifactVersion: String(action.payload.artifact_version || draft.artifactVersion),
    full: true,
    loading: false,
    error: null,
    conflict: content !== action.submittedContent,
    pendingReadback: null,
  };
}

export function reviewDraftReducer(draft, action) {
  if (action.type === 'reload') {
    return { ...createReviewDraft(action.identity, action.runId, action.review), full: false };
  }
  if (action.type === 'receive') return receiveReview(draft, action);
  if (action.identity !== draft.identity) return draft;
  if (action.type === 'loadStarted') return { ...draft, loading: true, requestId: action.requestId, error: null };
  if (action.type === 'loaded') return loadArtifact(draft, action);
  if (action.type === 'loadFailed') {
    return draft.requestId === action.requestId ? { ...draft, loading: false, error: action.error } : draft;
  }
  if (action.type === 'change') return draft.full ? { ...draft, content: action.content } : draft;
  if (action.type === 'savePendingReadback') {
    return {
      ...draft,
      pendingReadback: {
        submittedContent: action.submittedContent,
        originalContent: action.originalContent,
      },
    };
  }
  if (action.type === 'saved') return saveArtifact(draft, action);
  if (action.type === 'error') return { ...draft, error: action.error };
  if (action.type === 'retry') return { ...draft, error: null };
  return draft;
}
