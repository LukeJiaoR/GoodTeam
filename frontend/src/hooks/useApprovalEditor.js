import { useCallback, useEffect, useState } from 'react';

export function useApprovalEditor({
  projectKey,
  showToast,
  fetchState,
  setActiveTab,
  setActionLoading,
}) {
  const [editingArtifact, setEditingArtifact] = useState(null);
  const [editorContent, setEditorContent] = useState('');
  const [refineLoading, setRefineLoading] = useState(false);
  const [proposedRevision, setProposedRevision] = useState(null);
  const [openQuestions, setOpenQuestions] = useState([]);
  const [openQuestionsAnswers, setOpenQuestionsAnswers] = useState({});
  const [fetchingOpenQuestions, setFetchingOpenQuestions] = useState(false);
  const [answeringOpenQuestions, setAnsweringOpenQuestions] = useState(null);

  const fetchOpenQuestions = useCallback(async (projKey, artKey) => {
    if (!projKey || !artKey) return;
    setFetchingOpenQuestions(true);
    try {
      const response = await fetch(`/api/open-questions?project=${encodeURIComponent(projKey)}&artifact=${encodeURIComponent(artKey)}`);
      const res = await response.json();
      if (response.ok) {
        setOpenQuestions(res.questions || []);
        setOpenQuestionsAnswers(prev => {
          const next = {};
          (res.questions || []).forEach(q => {
            next[q] = prev[q] !== undefined ? prev[q] : '';
          });
          return next;
        });
      } else {
        console.error(res.error || 'Failed to fetch open questions');
      }
    } catch (err) {
      console.error(err);
    } finally {
      setFetchingOpenQuestions(false);
    }
  }, []);

  const handleEditArtifact = useCallback(async (artifactOrReview) => {
    const artifactName = typeof artifactOrReview === 'string'
      ? artifactOrReview
      : (artifactOrReview?.artifact || '');
    if (!artifactName) return;

    setActionLoading(true);
    try {
      const response = await fetch(`/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(artifactName)}`);
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || '暂无该产物');
      }
      setEditingArtifact(artifactName);
      setEditorContent(res.content || '');
      setActiveTab('approvals');
    } catch (err) {
      const errMsg = err.message.includes('File not found') || err.message.includes('No such file') || err.message.includes('does not exist')
        ? '暂无该产物'
        : err.message;
      showToast(errMsg, true);
    } finally {
      setActionLoading(false);
    }
  }, [projectKey, setActionLoading, setActiveTab, showToast]);

  const handleOpenQuestionsFromApproval = useCallback(async (review) => {
    await handleEditArtifact(review);
    if (review?.artifact) {
      fetchOpenQuestions(projectKey, review.artifact);
    }
  }, [fetchOpenQuestions, handleEditArtifact, projectKey]);

  const handleSaveArtifact = useCallback(async () => {
    if (!editingArtifact) return;
    setActionLoading(true);
    try {
      const response = await fetch('/api/artifact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
          content: editorContent,
        }),
      });
      const res = await response.json();
      if (!response.ok) throw new Error(res.error || 'Failed to save artifact');
      showToast(`Artifact ${editingArtifact.toUpperCase()} saved successfully`);
      setEditingArtifact(null);
      fetchState(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setActionLoading(false);
    }
  }, [editingArtifact, editorContent, fetchState, projectKey, setActionLoading, showToast]);

  const handleApplyAnswers = useCallback(async (singleQuestion) => {
    if (!projectKey || !editingArtifact) return;

    let answersToSend = [];
    if (singleQuestion) {
      const ans = openQuestionsAnswers[singleQuestion];
      if (ans && ans.trim()) {
        answersToSend = [{ question: singleQuestion, answer: ans }];
      }
    } else {
      answersToSend = Object.entries(openQuestionsAnswers)
        .filter(([, ans]) => ans && ans.trim())
        .map(([q, ans]) => ({ question: q, answer: ans }));
    }

    if (answersToSend.length === 0) return;

    setAnsweringOpenQuestions(singleQuestion || true);
    try {
      const response = await fetch('/api/answer-open-questions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
          answers: answersToSend,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to apply answers with AI');
      }

      const proposedPath = res.proposed_path;
      const contentResponse = await fetch(`/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(proposedPath)}`);
      const contentRes = await contentResponse.json();
      if (!contentResponse.ok) {
        throw new Error(contentRes.error || 'Failed to fetch proposed artifact content');
      }

      setProposedRevision({
        proposed_path: proposedPath,
        content: contentRes.content || '',
        diff_summary: res.diff_summary || { added_lines: 0, removed_lines: 0 }
      });
      showToast('Answers applied with AI. Please review the proposed revision.');
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setAnsweringOpenQuestions(null);
    }
  }, [editingArtifact, openQuestionsAnswers, projectKey, showToast]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      if (!editingArtifact || !projectKey) {
        setOpenQuestions([]);
        setOpenQuestionsAnswers({});
        return;
      }
      setOpenQuestionsAnswers({});
      fetchOpenQuestions(projectKey, editingArtifact);
    });
    return () => {
      cancelled = true;
    };
  }, [editingArtifact, fetchOpenQuestions, projectKey]);

  const handleRefineArtifact = useCallback(async () => {
    if (!editingArtifact || !editorContent) return;
    setRefineLoading(true);
    try {
      const response = await fetch('/api/refine-artifact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
          content: editorContent,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to refine artifact');
      }

      const proposedPath = res.proposed_path;
      const contentResponse = await fetch(`/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(proposedPath)}`);
      const contentRes = await contentResponse.json();
      if (!contentResponse.ok) {
        throw new Error(contentRes.error || 'Failed to fetch proposed artifact content');
      }

      setProposedRevision({
        proposed_path: proposedPath,
        content: contentRes.content || '',
        diff_summary: res.diff_summary || { added_lines: 0, removed_lines: 0 }
      });
      showToast('Refinement generated. Please review the proposal.');
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setRefineLoading(false);
    }
  }, [editingArtifact, editorContent, projectKey, showToast]);

  const handleAcceptRefinement = useCallback(async () => {
    if (!editingArtifact || !proposedRevision) return;
    setRefineLoading(true);
    try {
      const response = await fetch('/api/accept-refinement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to accept refinement');
      }

      showToast(`Refinement accepted. Saved to ${editingArtifact.toUpperCase()}.`);
      setEditorContent(proposedRevision.content);
      setProposedRevision(null);

      await fetchState(projectKey);
      await fetchOpenQuestions(projectKey, editingArtifact);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setRefineLoading(false);
    }
  }, [editingArtifact, fetchOpenQuestions, fetchState, projectKey, proposedRevision, showToast]);

  const handleDiscardRefinement = useCallback(async () => {
    if (!editingArtifact || !proposedRevision) return;
    setRefineLoading(true);
    try {
      const response = await fetch('/api/discard-refinement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          artifact: editingArtifact,
        }),
      });
      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || 'Failed to discard refinement');
      }

      showToast('Refinement discarded. Keeping your original edits.');
      setProposedRevision(null);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setRefineLoading(false);
    }
  }, [editingArtifact, projectKey, proposedRevision, showToast]);

  return {
    editingArtifact,
    setEditingArtifact,
    editorContent,
    setEditorContent,
    refineLoading,
    proposedRevision,
    openQuestions,
    openQuestionsAnswers,
    setOpenQuestionsAnswers,
    fetchingOpenQuestions,
    answeringOpenQuestions,
    handleEditArtifact,
    handleOpenQuestionsFromApproval,
    handleSaveArtifact,
    fetchOpenQuestions,
    handleApplyAnswers,
    handleRefineArtifact,
    handleAcceptRefinement,
    handleDiscardRefinement,
  };
}
