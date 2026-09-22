import { test, expect, importProject } from './fixtures.js';
import { clickMutation, decisions, prepareFirstTask, sendTask, snapshot } from './business-loop-helpers.js';

test('current identity and decision stay visible without opening the context drawer', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  await page.setViewportSize({ width: 1280, height: 900 });
  try {
    await importProject(page, fixture);
    const bar = page.getByRole('region', { name: 'Current task and next step' });
    await expect(bar).toBeVisible();
    await expect(bar).toContainText('No task started');
    const text = 'Implement title search without changing the database schema.';
    await sendTask(page, fixture, text);
    await expect(bar).toContainText(text);
    await expect(bar.getByRole('button', { name: 'Review task draft' })).toBeVisible();
    await prepareFirstTask(page, fixture, evidence);
    await expect(bar).toHaveAttribute('data-state', 'awaiting_review');
    await page.mouse.move(0, 0);
    await expect(page.locator('.work-context-disclosure')).not.toHaveAttribute('open', '');
    await expect(bar).toBeVisible();
    const before = await snapshot(page, fixture, evidence);
    await bar.getByRole('button', { name: 'Go to required decision' }).click();
    await expect(page.locator('.team-attention')).toBeFocused();
    const after = await snapshot(page, fixture, evidence);
    expect(after.state).toEqual(before.state);
    expect(decisions(after)).toEqual(decisions(before));
    expect(after.worker_jobs).toEqual(before.worker_jobs);
    await testInfo.attach('current-work-light-1280', { body: await page.screenshot(), contentType: 'image/png' });
    await page.reload();
    await expect(bar).toContainText(text);
    await expect(bar).toHaveAttribute('data-state', 'awaiting_review');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('button', { name: 'Appearance: Light', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Appearance: Dark', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await testInfo.attach('current-work-dark-1440', { body: await page.screenshot(), contentType: 'image/png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.work-context-disclosure summary').click();
    await expect(page.locator('.work-run-context')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await testInfo.attach('active-task-context-mobile', { body: await page.screenshot(), contentType: 'image/png' });
  } finally {
    await testInfo.attach('current-work-context-evidence', { body: JSON.stringify({ project: fixture.key, snapshots: evidence }, null, 2), contentType: 'application/json' });
  }
});

test('change discussion is a pending decision, not applied execution', async ({ page, fixture }) => {
  const evidence = [];
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement title search.');
  await prepareFirstTask(page, fixture, evidence);
  const before = await snapshot(page, fixture, evidence);
  await clickMutation(page, page.getByRole('button', { name: 'Request changes', exact: true }), '/api/intent-commit/start');
  const bar = page.getByRole('region', { name: 'Current task and next step' });
  await expect(bar).toContainText('It has not changed the execution instructions.');
  await page.reload();
  await expect(bar).toContainText('It has not changed the execution instructions.');
  const after = await snapshot(page, fixture, evidence);
  expect(after.state).toEqual(before.state);
  expect(decisions(after)).toEqual(decisions(before));
});
