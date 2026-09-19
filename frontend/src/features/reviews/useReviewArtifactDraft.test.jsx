import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import useReviewArtifactDraft from './useReviewArtifactDraft';

const FULL_CONTENT = '# Long review\n\nThe body continues beyond the preview.\n\nAcceptance: preserve the final requirement.';

function truncatedReview(overrides = {}) {
  return {
    approval: 'prd',
    artifact: 'prd',
    artifact_path: '/runs/run-1/prd.md',
    artifact_version: 'sha256:v1',
    content: '# Long review\n\nThe body continues.\n\n[Preview truncated]',
    content_truncated: true,
    ...overrides,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((accept) => { resolve = accept; });
  return { promise, resolve };
}

describe('useReviewArtifactDraft', () => {
  test('loads the complete artifact before making a truncated preview editable', async () => {
    const getArtifact = vi.fn().mockResolvedValue({
      content: FULL_CONTENT,
      run_id: 'run-1',
      artifact_version: 'sha256:v1',
    });
    const { result } = renderHook(() => useReviewArtifactDraft(
      'DEMO', 'run-1', truncatedReview(), { getArtifact },
    ));

    await waitFor(() => expect(result.current.full).toBe(true));
    expect(getArtifact).toHaveBeenCalledWith('DEMO', 'prd', expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(result.current.content).toBe(FULL_CONTENT);
    expect(result.current.baseline).toBe(FULL_CONTENT);
    expect(result.current.artifactVersion).toBe('sha256:v1');
  });

  test('keeps a dirty draft when polling reports a newer artifact version', async () => {
    const getArtifact = vi.fn().mockResolvedValue({
      content: FULL_CONTENT,
      run_id: 'run-1',
      artifact_version: 'sha256:v1',
    });
    const { result, rerender } = renderHook(
      ({ review }) => useReviewArtifactDraft('DEMO', 'run-1', review, { getArtifact }),
      { initialProps: { review: truncatedReview() } },
    );
    await waitFor(() => expect(result.current.full).toBe(true));
    act(() => result.current.setContent(`${FULL_CONTENT}\nUnsaved edit`));

    rerender({ review: truncatedReview({
      artifact_version: 'sha256:v2',
      content: '# Long review v2\n\n[Preview truncated]',
    }) });

    expect(result.current.content).toBe(`${FULL_CONTENT}\nUnsaved edit`);
    expect(result.current.baseline).toBe(FULL_CONTENT);
    expect(result.current.artifactVersion).toBe('sha256:v1');
    expect(result.current.conflict).toBe(true);
    expect(getArtifact).toHaveBeenCalledTimes(1);
  });

  test('ignores a late artifact response after the project and run change', async () => {
    const pending = deferred();
    const getArtifact = vi.fn(() => pending.promise);
    const { result, rerender } = renderHook(
      ({ projectKey, runId, review }) => useReviewArtifactDraft(projectKey, runId, review, { getArtifact }),
      { initialProps: { projectKey: 'FIRST', runId: 'run-1', review: truncatedReview() } },
    );
    await waitFor(() => expect(getArtifact).toHaveBeenCalledTimes(1));

    rerender({
      projectKey: 'SECOND',
      runId: 'run-2',
      review: { artifact: 'prd', content: '# Second project', artifact_version: 'sha256:second' },
    });
    await act(async () => {
      pending.resolve({ content: FULL_CONTENT, run_id: 'run-1', artifact_version: 'sha256:v1' });
      await pending.promise;
    });

    expect(result.current.content).toBe('# Second project');
    expect(result.current.artifactVersion).toBe('sha256:second');
  });

  test('retries a missing save readback without losing the submitted draft', async () => {
    const savedContent = `${FULL_CONTENT}\nSaved edit`;
    const getArtifact = vi.fn()
      .mockResolvedValueOnce({
        content: FULL_CONTENT,
        run_id: 'run-1',
        artifact_version: 'sha256:v1',
      })
      .mockResolvedValueOnce({
        content: savedContent,
        run_id: 'run-1',
        artifact_version: 'sha256:v2',
      });
    const { result } = renderHook(() => useReviewArtifactDraft(
      'DEMO', 'run-1', truncatedReview(), { getArtifact },
    ));

    await waitFor(() => expect(result.current.full).toBe(true));
    act(() => {
      result.current.setContent(savedContent);
      result.current.beginSavedReadback(savedContent, result.current.identity, savedContent);
      result.current.setError(new Error('The saved artifact could not be reloaded.'));
    });
    act(() => result.current.retryFullRead());

    await waitFor(() => expect(result.current.artifactVersion).toBe('sha256:v2'));
    expect(result.current.content).toBe(savedContent);
    expect(result.current.baseline).toBe(savedContent);
    expect(result.current.conflict).toBe(false);
    expect(result.current.error).toBeNull();
    expect(getArtifact).toHaveBeenCalledTimes(2);
  });
});
