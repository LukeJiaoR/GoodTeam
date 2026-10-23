import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { test, expect, importProject } from './fixtures.js';
import { getJson, workState } from './business-loop-helpers.js';

test('retained source, patch and QA stay bound while an explicit legacy Reviewer runs once', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  const seeded = JSON.parse(execFileSync(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', [
    '../tests/fixtures/delivery_evidence_browser_driver.py', fixture.root, fixture.key,
  ], { env: { ...process.env, PYTHONPATH: path.resolve('../src') }, encoding: 'utf8' }));
  const before = await workState(page, fixture.key);
  const source = before.delivery_summary.artifacts.find(item => item.kind === 'source');
  const patch = before.delivery_summary.artifacts.find(item => item.kind === 'patch');
  const qa = before.delivery_summary.artifacts.find(item => item.kind === 'qa');
  expect(qa).toMatchObject({ qa_status: 'blocked', process_status: 'completed' });
  expect(before.delivery_summary.verification).toBe('unverified');
  const posts = [];
  page.on('request', request => { if (request.method() === 'POST') posts.push(request); });
  await page.goto(`/projects/${fixture.key}/review/release`);
  const retained = page.getByRole('link', { name: 'Retained source', exact: true });
  await expect(retained).toHaveAttribute('href', source.href + '&view=text');
  await expect(page.getByRole('link', { name: 'Exact patch', exact: true })).toHaveAttribute('href', patch.href + '&view=text');
  const raw = await page.request.get(source.href + '&view=text');
  expect(raw.headers()['content-type']).toBe('text/plain; charset=utf-8');
  expect(raw.headers()['x-content-type-options']).toBe('nosniff');
  expect(raw.headers()['cache-control']).toBe('no-store');
  expect(await raw.text()).toBe(seeded.content);
  await page.getByRole('button', { name: 'src/main.js', exact: true }).click();
  await expect(page.locator('.delivery-report-content pre')).toHaveText(seeded.content);
  await expect(page.locator('.delivery-report-content img')).toHaveCount(0);
  await page.getByRole('button', { name: 'changes.patch', exact: true }).click();
  const patchReport = page.locator('.delivery-report-list > li').filter({ has: page.getByRole('button', { name: 'changes.patch', exact: true }) });
  await expect(patchReport.locator('pre')).toContainText('diff --git a/src/main.js b/src/main.js');
  await page.getByRole('button', { name: 'QA result.md', exact: true }).click();
  await expect(page.locator('.delivery-report-content').last()).toContainText('exit: 1; tests: 0');
  expect(posts).toHaveLength(0);
  const readOnly = await workState(page, fixture.key);
  expect(readOnly.state).toEqual(before.state);
  const start = page.getByRole('button', { name: 'Run this Reviewer', exact: true });
  const started = page.waitForResponse(response => new URL(response.url()).pathname === '/api/reviewer-jobs/run');
  await start.click();
  const response = await started;
  expect(response.ok(), await response.text()).toBe(true);
  expect((await response.json()).results).toMatchObject([{ job_id: seeded.job_id, status: 'completed' }]);
  const launches = posts.filter(request => new URL(request.url()).pathname === '/api/reviewer-jobs/run');
  expect(launches).toHaveLength(1);
  expect(launches[0].postDataJSON()).toMatchObject({ project_key: fixture.key, run_id: seeded.run_id,
    job_id: seeded.job_id, artifact_key: 'release_notes' });
  await page.getByRole('button', { name: 'Read original Reviewer report', exact: true }).click();
  await expect(page.locator('.legacy-reviewer-job .delivery-report-content')).toBeVisible();
  const after = await workState(page, fixture.key);
  expect(after.state).toEqual(before.state);
  const report = after.delivery_summary.reviewer_reports.find(item => item.job_id === seeded.job_id);
  expect((await getJson(page, report.href)).advisory_only).toBe(true);
  expect(after.delivery_summary.verification).toBe('unverified');
  expect(fs.readFileSync(path.join(fixture.root, 'src/main.js'), 'utf8')).toBe("export const fixture = 'read-only source';\n");
  await testInfo.attach('retained-delivery-evidence', { body: JSON.stringify({ seeded, source, patch, qa, report,
    reviewerLaunch: launches[0].postDataJSON(), approvals: after.state.approvals }, null, 2), contentType: 'application/json' });
  await testInfo.attach('retained-delivery-review', { body: await page.screenshot(), contentType: 'image/png' });
});
