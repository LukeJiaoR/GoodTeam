import { test, expect } from '@playwright/test';

// Real Chromium/router regression with deterministic HTTP fixtures. No model
// calls; backend durable binding semantics are covered separately in Python.
test('proposal ownership survives neither review navigation nor browser back', async ({ page }) => {
  const binding = { proposal_id: 'proposal-browser', run_id: 'run-browser', artifact_key: 'prd', base_version: 'sha256:base', proposal_version: 'sha256:proposal' };
  const state = { state: { run_id: binding.run_id }, approval_reviews: ['prd', 'rfc'].map(artifact => ({
    approval: artifact, title: artifact.toUpperCase(), artifact, artifact_path: `/runs/${binding.run_id}/${artifact}.md`,
    artifact_version: binding.base_version, content: `${artifact.toUpperCase()} BROWSER BODY`, is_actionable: artifact === 'prd',
  })) };
  const mutations = [];
  await page.route('**/api/state?**', route => route.fulfill({ json: state }));
  await page.route('**/api/open-questions?**', route => route.fulfill({ json: { questions: [] } }));
  await page.route('**/api/refine-artifact', route => route.fulfill({ json: { proposal_binding: binding, proposed_path: '/not-an-api-key/prd.md' } }));
  await page.route('**/api/artifact?**', route => {
    expect(new URL(route.request().url()).searchParams.get('key')).toBe('prd_proposed');
    return route.fulfill({ json: { content: 'PRD BROWSER PROPOSAL', run_id: binding.run_id, artifact_version: binding.proposal_version, proposal_binding: binding } });
  });
  await page.route('**/api/*-refinement', route => {
    mutations.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto('/projects/DEMO/review/prd');
  await page.getByRole('button', { name: 'Propose refinement', exact: true }).click();
  await expect(page.getByText('PRD BROWSER PROPOSAL', { exact: true })).toBeVisible();
  await page.locator('.review-queue a[href$="/review/rfc"]').click();
  await page.getByRole('button', { name: 'Discard and leave', exact: true }).click();
  await expect(page).toHaveURL(/\/review\/rfc$/);
  await expect(page.getByText('RFC BROWSER BODY', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Accept revision', exact: true })).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/\/review\/prd$/);
  await expect(page.getByText('PRD BROWSER PROPOSAL', { exact: true })).toHaveCount(0);
  expect(mutations).toEqual([]);
  await page.getByRole('button', { name: 'Propose refinement', exact: true }).click();
  await expect(page.getByText('PRD BROWSER PROPOSAL', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Discard proposal', exact: true }).click();
  await expect(page.getByText('PRD BROWSER PROPOSAL', { exact: true })).toHaveCount(0);
  expect(mutations).toEqual([{
    project_key: 'DEMO', artifact: 'prd', expected_run_id: binding.run_id,
    expected_artifact_key: 'prd', expected_artifact_version: binding.base_version,
    expected_proposal_id: binding.proposal_id, expected_proposal_version: binding.proposal_version,
  }]);
});
