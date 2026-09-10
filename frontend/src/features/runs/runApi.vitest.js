import { afterEach, expect, test, vi } from 'vitest';
import {
  confirmChangeIntent,
  discussChangeIntent,
  keepDiscussingChangeIntent,
  startChangeIntent,
} from './runApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

test('posts the durable Intent Commit conversation steps with exact target binding', async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ intent_commit: { id: 'intent-1' } }),
  });
  vi.stubGlobal('fetch', fetch);
  const target = {
    run_id: 'run-1',
    target_type: 'review',
    target_id: 'security',
    task_id: 'DEMO-ARCH',
    artifact_key: 'rfc',
    artifact_version: 'sha256:rfc-v3',
  };

  await startChangeIntent('DEMO', 'security', target);
  await discussChangeIntent('DEMO', 'intent-1', 'Keep the provider interface generic.');
  await keepDiscussingChangeIntent('DEMO', 'intent-1');
  await confirmChangeIntent('DEMO', 'intent-1');

  expect(fetch.mock.calls.map(([path]) => path)).toEqual([
    '/api/intent-commit/start',
    '/api/intent-commit/discuss',
    '/api/intent-commit/keep-discussing',
    '/api/intent-commit/confirm',
  ]);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    project_key: 'DEMO',
    approval: 'security',
    expected_run_id: 'run-1',
    expected_target_type: 'review',
    expected_target_id: 'security',
    expected_task_id: 'DEMO-ARCH',
    expected_artifact_key: 'rfc',
    expected_artifact_version: 'sha256:rfc-v3',
  });
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    project_key: 'DEMO',
    intent_id: 'intent-1',
    text: 'Keep the provider interface generic.',
  });
  expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
    project_key: 'DEMO',
    intent_id: 'intent-1',
  });
  expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({
    project_key: 'DEMO',
    intent_id: 'intent-1',
  });
});

test('includes raw initial feedback without dropping request cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  const initialText = '  Retain whitespace.  \n\n    code()\n';
  await startChangeIntent('DEMO', 'prd', { artifact_version: 'v1' }, { initialText, signal });
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    project_key: 'DEMO', approval: 'prd', expected_artifact_version: 'v1', initial_text: initialText,
  });
  expect(fetch.mock.calls[0][1].signal).toBe(signal);
});
