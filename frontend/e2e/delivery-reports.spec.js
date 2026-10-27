import fs from 'node:fs';
import path from 'node:path';
import { test, expect, importProject } from './fixtures.js';
import { approveGate, clickMutation, decisions, expectGate, getJson, prepareFirstTask,
  sendTask, snapshot, workState } from './business-loop-helpers.js';

const unverified = 'Reports and process results are not proof that this code version passed verification or acceptance.';
const finished = 'Execution ended; acceptance unverified';

// Check computed foregrounds against the actual composited ancestor surfaces,
// not only the presence of a theme class. This caught the legacy dark-only
// Markdown palette leaking into light-mode delivery reports.
async function expectReadableReport(reports) {
  const contrast = await reports.locator('.delivery-report-content').evaluate((root) => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    const luminance = (rgb) => {
      const values = rgb.slice(0, 3).map((channel) => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
    };
    return [...root.querySelectorAll('h1, .md-p')].slice(0, 4).map((element) => {
      const ancestors = [];
      for (let parent = element; parent; parent = parent.parentElement) ancestors.unshift(parent);
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, 1, 1);
      for (const ancestor of ancestors) {
        context.fillStyle = getComputedStyle(ancestor).backgroundColor;
        context.fillRect(0, 0, 1, 1);
      }
      const background = [...context.getImageData(0, 0, 1, 1).data];
      const color = getComputedStyle(element).color;
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const foreground = [...context.getImageData(0, 0, 1, 1).data];
      const values = [luminance(background), luminance(foreground)].sort((a, b) => b - a);
      return { text: element.textContent.slice(0, 80), color, background,
        ratio: (values[0] + 0.05) / (values[1] + 0.05) };
    });
  });
  expect(contrast.length).toBeGreaterThan(1);
  for (const item of contrast) expect(item.ratio, JSON.stringify(item)).toBeGreaterThanOrEqual(4.5);
  return contrast;
}

test('delivery: exact constraints, current reports and archived versions remain read-only and isolated', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  const requests = [];
  const reportReads = [];
  const contrastEvidence = [];
  page.on('request', (request) => {
    if (request.method() === 'POST') requests.push(new URL(request.url()).pathname);
    if (new URL(request.url()).pathname === '/api/delivery-artifact') reportReads.push(request.url());
  });
  fs.writeFileSync(path.join(fixture.root, 'AGENTS.md'), '# Guardrails\n- Keep public APIs stable.\n- Do not deploy.\n');
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Implement a searchable project list.');
    const runId = await prepareFirstTask(page, fixture, evidence);
    const preparation = evidence.find((item) => item.session?.attention_request?.reason === 'constraint_confirmation');
    // Exact source-bearing constraints are also asserted at the visible confirmation
    // by prepareFirstTask, not inferred from the fact the button was clicked.
    expect(preparation?.session.attention_request.input_requirements.constraints).toEqual(expect.arrayContaining([
      'AGENTS.md: Keep public APIs stable.', 'AGENTS.md: Do not deploy.',
    ]));
    for (const gate of ['takeover', 'prd', 'rfc', 'security', 'release']) {
      await approveGate(page, fixture, gate);
    }
    await expect.poll(async () => (await workState(page, fixture.key)).state?.current_node).toBe('complete');
    const completed = await snapshot(page, fixture, evidence);
    expect(completed.delivery_summary).toMatchObject({ project_key: fixture.key, run_id: runId,
      verification: 'unverified', status: 'needs_verification', simulated: true });
    const report = completed.delivery_summary.artifacts.find((item) => item.key === 'implementation_handoff');
    expect(report?.status).toBe('available');
    expect(report.version).toMatch(/^sha256:[a-f0-9]{64}$/);
    await page.goto(`/projects/${fixture.key}/work`);
    const reports = page.getByRole('region', { name: 'Delivery reports', exact: true });
    await expect(reports).toContainText(unverified);
    await expect(reports).toContainText(finished);
    await expect(reports).toContainText('Current task delivery');
    expect(reportReads).toHaveLength(0);
    const postsBeforeRead = [...requests];
    const read = page.waitForResponse((res) => res.request().method() === 'GET'
      && new URL(res.url()).pathname === '/api/delivery-artifact');
    await reports.getByRole('button', { name: /handoff/i }).click();
    const response = await read;
    expect(response.ok()).toBe(true);
    const payload = await response.json();
    expect(payload).toMatchObject({ project_key: fixture.key, run_id: runId,
      artifact: report.key, version: report.version });
    expect(payload.content.trim().length).toBeGreaterThan(0);
    await expect(reports.locator('.delivery-report-content')).toBeVisible();
    expect(requests).toEqual(postsBeforeRead);
    const afterRead = await snapshot(page, fixture, evidence);
    expect(afterRead.state).toEqual(completed.state);
    expect(decisions(afterRead)).toEqual(decisions(completed));
    contrastEvidence.push({ mode: 'light', samples: await expectReadableReport(reports) });
    await testInfo.attach('delivery-current-desktop', { body: await page.screenshot(), contentType: 'image/png' });
    const appearance = page.getByRole('button', { name: /^Appearance:/ });
    await expect(appearance).toHaveAttribute('aria-pressed', 'false');
    await appearance.click();
    await expect(appearance).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('html')).toHaveClass(/dark/);
    contrastEvidence.push({ mode: 'dark', samples: await expectReadableReport(reports) });
    await testInfo.attach('delivery-current-dark', { body: await page.screenshot(), contentType: 'image/png' });
    await appearance.click();
    await expect(appearance).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('html')).not.toHaveClass(/dark/);
    expect(requests).toEqual(postsBeforeRead);


    // Starting another task is an explicit UI action, never a fixture state edit.
    const second = await sendTask(page, fixture, 'Build a task activity filter.');
    const started = await clickMutation(page, page.getByRole('button', { name: 'Start this task' }),
      `/api/projects/${fixture.key}/task-assistant/actions`);
    expect(started.run_id).toBe(`task-${second.id}`);
    expect(started.run_id).not.toBe(runId);
    const active = await expectGate(page, fixture.key, 'takeover');
    const archived = await getJson(page, `/api/projects/${fixture.key}/runs/${runId}`);
    expect(archived.state).toEqual(completed.state);
    expect(archived.delivery_summary.status).toBe('needs_verification');
    expect(archived.delivery_summary.artifacts.find((item) => item.key === report.key)).toEqual(report);
    const stale = new URL(report.href, 'http://localhost');
    stale.searchParams.set('version', 'sha256:' + '0'.repeat(64));
    expect((await page.request.get(stale.pathname + stale.search)).status()).toBe(409);
    const wrongProject = new URL(report.href, 'http://localhost');
    wrongProject.searchParams.set('project', 'DIFFERENT_PROJECT');
    expect((await page.request.get(wrongProject.pathname + wrongProject.search)).status()).toBe(404);
    const wrongRun = new URL(report.href, 'http://localhost');
    wrongRun.searchParams.set('run', started.run_id);
    expect((await page.request.get(wrongRun.pathname + wrongRun.search)).status()).toBe(404);
    expect(await getJson(page, report.href)).toEqual(payload);

    const postsBeforeHistory = [...requests];
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/projects/${fixture.key}/work/runs/${runId}`);
    await page.getByRole('tab', { name: /History & delivery/ }).click();
    await expect(page.getByText('Historical runs are read-only and visually separate from the active run.')).toBeVisible();
    await expect(reports).toContainText('Historical task delivery · read-only');
    await expect(reports).toContainText(finished);
    await expect(reports).toContainText(unverified);
    await reports.getByRole('button', { name: /handoff/i }).click();
    await expect(reports.locator('.delivery-report-content')).toBeVisible();
    await expect(reports).toContainText(report.version);
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Approve and continue', exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    contrastEvidence.push({ mode: 'historical-mobile', samples: await expectReadableReport(reports) });
    await reports.locator('.delivery-report-content').scrollIntoViewIfNeeded();
    await testInfo.attach('delivery-historical-mobile', { body: await page.screenshot(), contentType: 'image/png' });
    await page.reload();
    await page.getByRole('tab', { name: /History & delivery/ }).click();
    await expect(reports).toContainText(finished);
    const current = await snapshot(page, fixture, evidence);
    expect(current.state).toEqual(active.state);
    expect(decisions(current)).toEqual(decisions(active));
    expect(requests).toEqual(postsBeforeHistory);
    expect(fs.readFileSync(path.join(fixture.root, 'src', 'main.js'), 'utf8')).toBe("export const fixture = 'read-only source';\n");
  } finally {
    await testInfo.attach('delivery-boundary-evidence', {
      body: JSON.stringify({ project: fixture.key, snapshots: evidence, postRequests: requests, reportReads, contrastEvidence }, null, 2),
      contentType: 'application/json',
    });
  }
});
