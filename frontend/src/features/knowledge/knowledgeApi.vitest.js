import { afterEach, expect, test, vi } from 'vitest';
import { getKnowledgeHome, getRepositoryUnderstanding } from './knowledgeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

test('reads readiness-backed Knowledge data through encoded project paths', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response('{}', {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetch);

  await getKnowledgeHome('DEMO SPACE');
  await getRepositoryUnderstanding('DEMO SPACE');

  expect(fetch.mock.calls.map(([path]) => path)).toEqual([
    '/api/projects/DEMO%20SPACE/home',
    '/api/projects/DEMO%20SPACE/readiness/steps/repository_understanding/artifact',
  ]);
});
