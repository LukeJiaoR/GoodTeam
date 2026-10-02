import { test, expect, importProject } from './fixtures.js';
import { clickMutation, prepareFirstTask, sendTask, snapshot } from './business-loop-helpers.js';

test('an old confirmation cannot archive a replacement task started from the same project', async ({ page, context, fixture }, testInfo) => {
  const evidence = [];
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement a searchable project list.');
  const firstRun = await prepareFirstTask(page, fixture, evidence);
  const manager = page.getByRole('region', { name: 'Task Assistant', exact: true });
  const endpoint = `/api/projects/${fixture.key}/task-assistant/actions`;
  await manager.getByRole('button', { name: 'New task', exact: true }).click();
  await manager.getByRole('textbox', { name: 'New task', exact: true }).fill('Add keyboard navigation.');
  await clickMutation(page, manager.getByRole('button', { name: 'Save task', exact: true }), endpoint);
  await page.getByRole('button', { name: 'Archive current task', exact: true }).click();
  await expect(page.getByText(/moves the current task to history/)).toBeVisible();

  // Delay only the old tab's reads; both tasks and every write use the real
  // product HTTP handlers in the isolated fixture workspace, with fake Workers.
  let releaseReads;
  const readsPaused = new Promise((resolve) => { releaseReads = resolve; });
  const stateUrl = `**/api/state?project=${fixture.key}`;
  await page.route(stateUrl, async (route) => { await readsPaused; await route.continue(); });
  const otherTab = await context.newPage();
  otherTab.on('pageerror', (error) => fixture.errors.push(error.message));
  otherTab.on('console', (message) => { if (message.type() === 'error') fixture.errors.push(message.text()); });
  // Retain the expected conflict separately; any other console error still
  // fails the shared fixture's browser-error assertion.
  const expectedConsole = [];
  const conflictConsole = (message) => {
    if (message.type() !== 'error' || !message.text().includes('409')
        || !message.location().url.endsWith('/api/terminate')) return;
    expectedConsole.push(message.text());
    const index = fixture.errors.lastIndexOf(message.text());
    if (index >= 0) fixture.errors.splice(index, 1);
  };
  page.on('console', conflictConsole);
  try {
    const stop = await otherTab.request.post('/api/terminate', {
      data: { project_key: fixture.key, expected_run_id: firstRun },
    });
    expect(stop.ok(), await stop.text()).toBe(true);
    await otherTab.goto(`/projects/${fixture.key}/work`);
    await otherTab.getByRole('button', { name: 'Manage tasks', exact: true }).click();
    await clickMutation(otherTab, otherTab.getByRole('button', { name: 'Prepare this draft', exact: true }), endpoint);
    const started = await clickMutation(otherTab, otherTab.getByRole('button', { name: 'Start this task', exact: true }), endpoint);
    expect(started.workflow_started).toBe(true);
    expect(started.run_id).not.toBe(firstRun);
    const before = await snapshot(otherTab, fixture, evidence);

    const pending = page.waitForResponse((response) => response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/terminate');
    await page.getByRole('button', { name: 'Archive current task', exact: true }).click();
    const rejected = await pending;
    expect(rejected.request().postDataJSON()).toEqual({ project_key: fixture.key, expected_run_id: firstRun });
    expect(rejected.status()).toBe(409);
    expect((await rejected.json()).code).toBe('run_binding_mismatch');
    const after = await snapshot(otherTab, fixture, evidence);
    expect(after.state).toEqual(before.state);
    expect(after.state.run_id).toBe(started.run_id);
    expect(evidence.at(-1).ledger).toEqual(evidence.at(-2).ledger);

    releaseReads();
    await expect(page.getByRole('region', { name: 'Current task and next step' })).toContainText('Add keyboard navigation.');
    await expect(page.getByText(/moves the current task to history/)).toHaveCount(0);
    await testInfo.attach('task-bound-cancellation', {
      body: JSON.stringify({ evidence, expectedConsole }, null, 2), contentType: 'application/json',
    });
    await testInfo.attach('replacement-task-preserved', { body: await page.screenshot(), contentType: 'image/png' });
  } finally {
    releaseReads();
    await page.unroute(stateUrl);
    page.off('console', conflictConsole);
    await otherTab.close();
  }
});

test('stale task updates disable actions and a real refresh restores the existing task', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement title search.');
  const runId = await prepareFirstTask(page, fixture, evidence);
  const before = await snapshot(page, fixture, evidence);
  const archive = page.getByRole('button', { name: 'Archive current task', exact: true });
  await archive.click();
  const stateUrl = `**/api/state?project=${fixture.key}`;
  // Explicit negative-path fault injection: no successful business response
  // is mocked. A malformed poll must preserve the last visible task as stale.
  await page.route(stateUrl, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: '{incomplete',
  }));
  const staleMessage = page.getByText('Task updates are unavailable. Actions are paused until you refresh successfully.');
  await expect(staleMessage).toBeVisible({ timeout: 15_000 });
  await expect(archive).toBeDisabled();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeDisabled();
  let cancelRequests = 0;
  const countCancel = (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/terminate') cancelRequests += 1;
  };
  page.on('request', countCancel);
  try {
    await archive.evaluate((button) => button.click());
    expect(cancelRequests).toBe(0);
    const stillCurrent = await snapshot(page, fixture, evidence);
    expect(stillCurrent.state).toEqual(before.state);
    expect(evidence.at(-1).ledger).toEqual(evidence.at(-2).ledger);
    await testInfo.attach('stale-task-paused', { body: await page.screenshot(), contentType: 'image/png' });
    await page.unroute(stateUrl);
    await page.getByRole('region', { name: 'Current task and next step' })
      .getByRole('button', { name: 'Refresh current state', exact: true }).click();
    await expect(staleMessage).toHaveCount(0);
    await expect(archive).toBeEnabled();
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled();
    const archived = await clickMutation(page, archive, '/api/terminate');
    expect(archived.run_id).toBe(runId);
    const after = await snapshot(page, fixture, evidence);
    expect(after.state).toBeNull();
    expect(evidence.at(-1).ledger.filter((entry) => entry.action === 'run_terminated').map((entry) => entry.run_id))
      .toEqual([runId]);
    await testInfo.attach('stale-task-refresh-evidence', { body: JSON.stringify(evidence, null, 2), contentType: 'application/json' });
  } finally {
    await page.unroute(stateUrl);
    page.off('request', countCancel);
  }
});
