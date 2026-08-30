import { afterEach, expect, test, vi } from 'vitest';
import { decideReview } from './reviewApi';

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
