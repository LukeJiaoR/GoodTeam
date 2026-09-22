// Temporary continuation audit transport, removed before final acceptance.
// Only tracked repository bytes are archived; no HOME, credentials, .git,
// working-tree files, runtime state, or model/provider calls are included.
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));

test('engineering source capture only - not Skill runtime acceptance', async ({}, testInfo) => {
  const git = (args) => execFileSync('git', args, { cwd: root, timeout: 30000, maxBuffer: 64 * 1024 * 1024 });
  const head = git(['rev-parse', 'HEAD']).toString().trim();
  const tree = git(['rev-parse', 'HEAD^{tree}']).toString().trim();
  expect(head).toMatch(/^[0-9a-f]{40}$/);
  expect(tree).toMatch(/^[0-9a-f]{40}$/);
  const source = git(['archive', '--format=tar', head]);
  await testInfo.attach('tracked-source.tar', { body: source, contentType: 'application/x-tar' });
  await testInfo.attach('source-identity.json', {
    body: JSON.stringify({ head, tree, sha256: createHash('sha256').update(source).digest('hex'), purpose: 'engineering-source-audit', runtime_acceptance: false, provider_calls: 0 }, null, 2),
    contentType: 'application/json',
  });
});
