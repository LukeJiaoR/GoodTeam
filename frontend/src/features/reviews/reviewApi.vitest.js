import { afterEach, expect, test, vi } from 'vitest';
import { acceptRefinement, discardRefinement, answerOpenQuestions, decideReview } from './reviewApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

test('posts the exact approval target binding with an approved decision', async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ decision: 'approved' }),
  });
  vi.stubGlobal('fetch', fetch);

  await decideReview('DEMO', 'security', 'approved', '', {
    expectedApproval: {
      run_id: 'run-1',
      target_type: 'review',
      target_id: 'security',
      artifact_key: 'rfc',
      artifact_version: 'sha256:rfc-v3',
    },
  });

  expect(fetch).toHaveBeenCalledTimes(1);
  const [path, options] = fetch.mock.calls[0];
  expect(path).toBe('/api/approve');
  expect(JSON.parse(options.body)).toEqual({
    project_key: 'DEMO',
    approval: 'security',
    decision: 'approved',
    expected_run_id: 'run-1',
    expected_target_type: 'review',
    expected_target_id: 'security',
    expected_artifact_key: 'rfc',
    expected_artifact_version: 'sha256:rfc-v3',
  });
});


test.each([acceptRefinement, discardRefinement])('proposal mutations post every expected identity field', async (mutate) => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  await mutate('DEMO', 'prd', { expectedProposal: {
    proposal_id: 'proposal-1', run_id: 'run-1', artifact_key: 'prd',
    base_version: 'sha256:base', proposal_version: 'sha256:proposal',
  } });
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    project_key: 'DEMO', artifact: 'prd', expected_run_id: 'run-1', expected_artifact_key: 'prd',
    expected_artifact_version: 'sha256:base', expected_proposal_id: 'proposal-1', expected_proposal_version: 'sha256:proposal',
  });
});

test('answers post the displayed artifact binding', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  await answerOpenQuestions('DEMO', 'prd', [], { expectedArtifact: { runId: 'run-1', artifactVersion: 'sha256:base' } });
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    project_key: 'DEMO', artifact: 'prd', answers: [], expected_run_id: 'run-1', expected_artifact_version: 'sha256:base',
  });
});
