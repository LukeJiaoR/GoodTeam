import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { spawnSync } from 'node:child_process';
import { test, expect, importProject } from './fixtures.js';
import { approveGate, clickMutation, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

// Real browser + HTTP + Worker persistence; the selected process is a labelled
// constant fixture, not a model, a fake HTTP report, or live Runtime acceptance.
function drive(fixture, runId, mode) {
  const child = spawnSync(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', [
    '../tests/fixtures/skill_review_browser_driver.py', fixture.key, runId, mode,
  ], { encoding: 'utf8', timeout: 30000, env: { ...process.env, PYTHONPATH: path.resolve('../src') } });
  expect(child.status, child.stderr || String(child.error || '')).toBe(0);
  const result = JSON.parse(child.stdout.trim());
  expect(result.provider_calls).toBe(0);
  return result;
}

async function methodDialog(page, fixture, label) {
  await page.goto(`/projects/${fixture.key}/team`);
  await page.getByRole('button', { name: 'Show roles in Quality & delivery', exact: true }).click();
  await page.getByRole('article', { name: label, exact: true }).getByRole('button', { name: 'Methods', exact: true }).click();
  return page.getByRole('dialog', { name: `${label} methods`, exact: true });
}

async function panel(page, fixture) {
  await page.goto(`/projects/${fixture.key}/work`);
  await page.getByRole('button', { name: 'Review execution details', exact: true }).click();
  return page.getByRole('region', { name: 'Review execution details', exact: true });
}

test('task-bound reports: source evidence, zero findings, durable recovery, failure, revocation, and Role edit', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  const results = [];
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Implement the baseline task before testing the bounded Reviewer contract.');
    await prepareFirstTask(page, fixture, evidence);
    for (const gate of ['takeover', 'prd', 'rfc', 'security', 'release']) await approveGate(page, fixture, gate);
    const baseline = await workState(page, fixture.key);
    const oldRun = baseline.state.run_id;
    const label = baseline.team.roles.find((role) => role.role === 'code_review').label;
    const name = `review-method-${fixture.name.replace(/[^a-z0-9]/gi, '').slice(-10).toLowerCase()}`;
    const source = path.join(process.env.GOODTEAM_E2E_ROOT, 'skills', name);
    fs.mkdirSync(source, { recursive: true });
    fs.writeFileSync(path.join(source, 'SKILL.md'), `---\nname: ${name}\ndescription: Inspect exact source declarations.\n---\nCheck the exported declaration, cite its source, and state checks not performed.\n`);
    await page.goto(`/projects/${fixture.key}/team`);
    await page.getByRole('button', { name: 'Skills', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: 'Skills', exact: true });
    await dialog.getByLabel('Server directory').fill(source);
    await clickMutation(page, dialog.getByRole('button', { name: 'Preview', exact: true }), '/api/skills/local/preview');
    const installed = await clickMutation(page, dialog.getByRole('button', { name: 'Install Skill', exact: true }), '/api/skills/local/install');
    await dialog.locator('footer').getByRole('button', { name: 'Close Skills', exact: true }).click();
    dialog = await methodDialog(page, fixture, label);
    await dialog.getByRole('radio', { name: /Custom Skills/ }).check();
    await dialog.getByRole('checkbox', { name: new RegExp(name) }).check();
    await clickMutation(page, dialog.getByRole('button', { name: 'Save methods', exact: true }), `/api/projects/${fixture.key}/team/commands`);
    await sendTask(page, fixture, 'Implement the change and review the exact fixture source using only my selected method.');
    await clickMutation(page, page.getByRole('button', { name: 'Start this task', exact: true }), `/api/projects/${fixture.key}/task-assistant/actions`);
    const before = await snapshot(page, fixture, evidence);
    const runId = before.state.run_id;
    expect(before.state.task_skill_context).toBeTruthy();
    const accepted = drive(fixture, runId, 'valid'); results.push(accepted);
    expect(accepted.fixture_processes).toBe(1);
    let details = await panel(page, fixture);
    let review = details.getByRole('article', { name: accepted.job_id, exact: true });
    await expect(review.getByText('Report received', { exact: true })).toBeVisible();
    await expect(review.getByText('Contract fixture · not live Runtime acceptance')).toBeVisible();
    await review.getByText('Locked method version', { exact: true }).click();
    await expect(review.getByText(installed.installed.version_id, { exact: true })).toBeVisible();
    await review.getByRole('button', { name: 'Open review report', exact: true }).click();
    await expect(review.getByText('No findings reported')).toBeVisible();
    await expect(review.getByText('This Reviewer did not run the application test suite.')).toBeVisible();
    await review.getByText('E1 · candidate:1–1', { exact: true }).click();
    await expect(review.locator('pre')).toHaveText("export const fixture = 'read-only source';");
    await testInfo.attach('received-advisory-source-evidence', { body: await page.screenshot(), contentType: 'image/png' });
    const endpoint = `/api/projects/${fixture.key}/runs/${runId}/skill-reviews`;
    const received = await (await page.request.get(`${endpoint}/${accepted.job_id}/report`)).json();
    expect(received.report.findings).toEqual([]);
    expect(received.methods).toHaveLength(1);
    expect(received.methods[0].skill_id).toBe(installed.installed.skill_id);
    expect(received.methods[0].version_id).toBe(installed.installed.version_id);
    expect(received.stages.resource_read_observed).toBe('unobserved');
    const historical = await page.request.get(`/api/projects/${fixture.key}/runs/${oldRun}/skill-reviews/${accepted.job_id}/report`);
    expect(historical.status()).toBe(404);
    const gitFailure = drive(fixture, runId, 'git-malformed'); results.push(gitFailure);
    expect(gitFailure.fixture_processes).toBe(1);
    const gitOriginal = await (await page.request.get(`${endpoint}/${gitFailure.job_id}`)).json();
    expect(gitOriginal.status).toBe('invalid');
    expect(gitOriginal.target.kind).toBe('git_change');
    expect(gitOriginal.target.files).toHaveLength(3);
    expect(gitOriginal.target.files.find(file => file.path === 'review-delete.txt').head).toBeNull();
    dialog = await methodDialog(page, fixture, label);
    await dialog.getByRole('radio', { name: /GoodTeam default methods/ }).check();
    await clickMutation(page, dialog.getByRole('button', { name: 'Restore default methods', exact: true }), `/api/projects/${fixture.key}/team/commands`);
    const replay = drive(fixture, runId, 'valid'); results.push(replay);
    expect(replay.fixture_processes).toBe(0);
    expect(replay.job_id).toBe(accepted.job_id);
    const reread = await (await page.request.get(`${endpoint}/${accepted.job_id}/report`)).json();
    expect(reread.methods).toEqual(received.methods);
    expect(reread.report_digest).toBe(received.report_digest);
    details = await panel(page, fixture);
    await page.reload();
    await page.getByRole('button', { name: 'Review execution details', exact: true }).click();
    review = page.getByRole('article', { name: accepted.job_id, exact: true });
    await review.getByRole('button', { name: 'Open review report', exact: true }).click();
    await expect(review.getByText('No findings reported')).toBeVisible();
    // Edit the working tree AFTER capture and Role edit; retry must use old bytes.
    fs.writeFileSync(path.join(fixture.root, 'review-change.js'), "WRONG CURRENT WORKTREE\n");
    details = await panel(page, fixture);
    const gitInvalid = details.getByRole('article', { name: gitFailure.job_id, exact: true });
    const retried = await clickMutation(page, gitInvalid.getByRole('button', { name: 'Retry with locked inputs', exact: true }),
      `${endpoint}/${gitFailure.job_id}/retry`);
    results.push(retried);
    expect(retried.created).toBe(true);
    expect(retried.job_id).not.toBe(gitFailure.job_id);
    expect(retried.status).toBe('failed'); // Production Runtime remains closed, not a fake success.
    let retryReview = details.getByRole('article', { name: retried.job_id, exact: true });
    await expect(retryReview).toBeVisible();
    await expect(retryReview.getByRole('button', { name: 'Open review report' })).toHaveCount(0);
    const executedRetry = drive(fixture, runId, 'git-retry'); results.push(executedRetry);
    expect(executedRetry.job_id).toBe(retried.job_id);
    expect(executedRetry.fixture_processes).toBe(1);
    const retryReport = await (await page.request.get(`${endpoint}/${retried.job_id}/report`)).json();
    expect(retryReport.target).toEqual(gitOriginal.target);
    expect(retryReport.methods).toEqual(gitOriginal.methods);
    expect(retryReport.context_ref).not.toEqual(gitOriginal.context_ref);
    expect(retryReport.runtime.session_id).not.toBe(gitOriginal.runtime.session_id);
    expect(retryReport.report.evidence[0].path).toBe('head/review-change.js');
    expect(retryReport.report.evidence[0].quote).toBe("export const reviewed = 'head';");
    const duplicate = await page.request.post(`${endpoint}/${gitFailure.job_id}/retry`, { data: {
      snapshot_digest: gitOriginal.snapshot_ref.digest, context_digest: gitOriginal.context_ref.digest,
    } });
    expect(duplicate.status()).toBe(200);
    expect((await duplicate.json()).created).toBe(false);
    await page.reload();
    await page.getByRole('button', { name: 'Review execution details', exact: true }).click();
    details = page.getByRole('region', { name: 'Review execution details', exact: true });
    retryReview = details.getByRole('article', { name: retried.job_id, exact: true });
    await retryReview.getByText('Changed files · exact base and head', { exact: true }).click();
    await expect(retryReview.getByText(gitOriginal.target.head_sha, { exact: true })).toBeVisible();
    await retryReview.getByRole('button', { name: 'Open review report', exact: true }).click();
    await retryReview.getByText('E1 · head/review-change.js:1–1', { exact: true }).click();
    await expect(retryReview.locator('pre')).toHaveText("export const reviewed = 'head';");
    await testInfo.attach('exact-git-source-new-context-retry', { body: await page.screenshot(), contentType: 'image/png' });
    const bad = drive(fixture, runId, 'malformed'); results.push(bad);
    const interrupted = drive(fixture, runId, 'save-failure'); results.push(interrupted);
    details = await panel(page, fixture);
    const invalid = details.getByRole('article', { name: bad.job_id, exact: true });
    await expect(invalid.getByText('Invalid report', { exact: true })).toBeVisible();
    await expect(invalid.getByRole('button', { name: 'Open review report' })).toHaveCount(0);
    const pending = details.getByRole('article', { name: interrupted.job_id, exact: true });
    await expect(pending.getByText('Saved outcome requires recovery')).toBeVisible();
    await expect(pending.getByRole('button', { name: 'Open review report' })).toHaveCount(0);
    const recovered = drive(fixture, runId, 'save-failure'); results.push(recovered);
    expect(recovered.fixture_processes).toBe(0);
    await details.getByRole('button', { name: 'Refresh reviews' }).click();
    await expect(pending.getByText('Report received', { exact: true })).toBeVisible();
    results.push(drive(fixture, runId, 'revoke'));
    const revokedRefresh = page.waitForResponse((response) => new URL(response.url()).pathname === endpoint);
    await details.getByRole('button', { name: 'Refresh reviews' }).click();
    const revokedResponse = await revokedRefresh;
    expect(revokedResponse.status()).toBe(200);
    const revokedPayload = await revokedResponse.json();
    expect(revokedPayload.reviews).toHaveLength(5);
    expect(revokedPayload.reviews.every((entry) => !entry.report_available)).toBe(true);
    await expect(details.getByRole('button', { name: 'Open review report' })).toHaveCount(0);
    const after = await snapshot(page, fixture, evidence);
    expect(after.state).toEqual(before.state); // Report actions never decide or advance gates.
    expect(after.state.task_skill_context).toEqual(before.state.task_skill_context);
    expect(fixture.errors).toEqual([]);
  } finally {
    await testInfo.attach('controlled-review-contract-results', {
      body: JSON.stringify({ project: fixture.key, live_runtime_acceptance: false, results, evidence }, null, 2),
      contentType: 'application/json',
    });
  }
});
