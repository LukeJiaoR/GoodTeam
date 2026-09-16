import { afterEach, expect, test, vi } from 'vitest';
import { refineArtifact, saveArtifact } from './reviewApi';

afterEach(() => vi.unstubAllGlobals());

test('binds an artifact save to its exact run and full-content version', async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ saved: true }),
  });
  vi.stubGlobal('fetch', fetch);

  await saveArtifact('DEMO', 'prd', '# Complete document', {
    expectedArtifact: { runId: 'run-1', artifactVersion: 'sha256:full-v1' },
  });

  const [path, options] = fetch.mock.calls[0];
  expect(path).toBe('/api/artifact');
  expect(JSON.parse(options.body)).toEqual({
    project_key: 'DEMO',
    artifact: 'prd',
    content: '# Complete document',
    expected_run_id: 'run-1',
    expected_artifact_version: 'sha256:full-v1',
  });
});

test('binds an artifact refinement to the exact run and full-content version', async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ proposed_path: '/runs/run-1/prd-proposed.md' }),
  });
  vi.stubGlobal('fetch', fetch);

  await refineArtifact('DEMO', 'prd', '# Complete document', {
    expectedArtifact: { runId: 'run-1', artifactVersion: 'sha256:full-v1' },
  });

  const [path, options] = fetch.mock.calls[0];
  expect(path).toBe('/api/refine-artifact');
  expect(JSON.parse(options.body)).toEqual({
    project_key: 'DEMO',
    artifact: 'prd',
    content: '# Complete document',
    expected_run_id: 'run-1',
    expected_artifact_version: 'sha256:full-v1',
  });
});
