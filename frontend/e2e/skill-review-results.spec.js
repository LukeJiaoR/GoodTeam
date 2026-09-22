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
    await sendTask(page, fixture, 'Prepare the isolated project before the bounded Reviewer contract test.');
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
    await sendTask(page, fixture, 'Review the exact fixture source using only my selected method.');
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
    expect(received.stages.resource_read_observed).toBe('unobserved');
    const historical = await page.request.get(`/api/projects/${fixture.key}/runs/${oldRun}/skill-reviews/${accepted.job_id}/report`);
    expect(historical.status()).toBe(404);
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
    await details.getByRole('button', { name: 'Refresh reviews' }).click();
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
