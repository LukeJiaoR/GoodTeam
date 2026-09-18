import { test, expect, importProject } from './fixtures.js';
import { workState } from './business-loop-helpers.js';

test('Enter shows immediate feedback; a transport failure preserves text and resend identity', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  const endpoint = `/api/projects/${fixture.key}/task-assistant/messages`;
  let release;
  let submitted;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(`**${endpoint}`, async (route) => {
    submitted = route.request().postDataJSON();
    await gate;
    await route.continue();
  });
  try {
    const text = 'Implement a searchable project list.';
    const input = page.getByRole('textbox', { name: 'Message', exact: true });
    await input.fill(text);
    const response = page.waitForResponse((res) => new URL(res.url()).pathname === endpoint);
    await input.press('Enter');
    await expect.poll(() => submitted?.text).toBe(text);
    const timeline = page.locator('.team-manager-timeline');
    await expect(timeline).toContainText(text);
    await expect(timeline).toContainText('Sending…');
    expect((await workState(page, fixture.key)).state).toBeNull();
    release();
    expect((await response).ok()).toBe(true);
    await expect(page.getByRole('region', { name: 'Proposed task' })).toContainText(text);
    await expect(input).toHaveValue('');
    await expect(timeline.locator('[data-delivery="sending"]')).toHaveCount(0);
    await expect(timeline.getByText(text, { exact: true })).toHaveCount(1);
  } finally {
    release();
    await page.unroute(`**${endpoint}`);
  }

  // This test deliberately causes exactly one transport error. Assert its
  // request identity and console output before consuming it; the shared fixture
  // still rejects every other console/unhandled error.
  const expectedError = 'Failed to load resource: net::ERR_FAILED';
  expect(fixture.errors).toEqual([]);
  const failedRequest = page.waitForEvent('requestfailed', (request) => (
    request.method() === 'POST' && new URL(request.url()).pathname === endpoint
  ));
  let failed;
  await page.route(`**${endpoint}`, async (route) => {
    failed = route.request().postDataJSON();
    await route.abort('failed');
  });
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  const question = 'Why this scope?';
  await input.fill(question);
  await input.press('Enter');
  await expect(page.locator('.team-manager-timeline')).toContainText('Request not confirmed');
  await expect(input).toHaveValue(question);
  const failure = await failedRequest;
  expect(failure.failure()?.errorText).toBe('net::ERR_FAILED');
  await expect.poll(() => [...fixture.errors]).toEqual([expectedError]);
  await testInfo.attach('intentional-transport-error', {
    body: JSON.stringify({ url: failure.url(), messageId: failed.message_id, errors: [...fixture.errors] }),
    contentType: 'application/json',
  });
  fixture.errors.splice(0, 1);
  await expect(page.locator('.team-manager-timeline [data-delivery="sending"]')).toHaveCount(0);
  await page.unroute(`**${endpoint}`);
  let retried;
  await page.route(`**${endpoint}`, async (route) => {
    retried = route.request().postDataJSON();
    await route.continue();
  });
  const response = page.waitForResponse((res) => new URL(res.url()).pathname === endpoint);
  await input.press('Enter');
  expect((await response).ok()).toBe(true);
  expect(retried.message_id).toBe(failed.message_id);
  await expect(input).toHaveValue('');
  expect((await workState(page, fixture.key)).state).toBeNull();
});
