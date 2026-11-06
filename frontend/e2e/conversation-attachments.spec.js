import { test, expect, importProject } from './fixtures.js';
import { Buffer } from 'node:buffer';

test('attachments survive a failed send and reload, and narrow panels leave the composer usable', async ({ page, fixture }) => {
  await importProject(page, fixture);
  const input = page.locator('input[type="file"]');
  await input.setInputFiles([
    { name: 'requirements.md', mimeType: 'text/markdown', buffer: Buffer.from('Keep the conversation at 760px.\nPreserve draft text.') },
    { name: 'layout.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC', 'base64') },
  ]);
  const attached = page.locator('.conversation-editor .conversation-attachments');
  await expect(attached.locator('li[data-upload="ready"]')).toHaveCount(2);
  await expect(attached.locator('img')).toBeVisible();
  await input.setInputFiles({ name: 'requirements.md', mimeType: 'text/markdown', buffer: Buffer.from('Keep the conversation at 760px.\nPreserve draft text.') });
  await expect(attached.locator('li')).toHaveCount(2);
  const editor = page.getByRole('textbox', { name: 'Message', exact: true });
  await editor.fill('Explain the attached references.');
  const endpoint = `/api/projects/${fixture.key}/task-assistant/messages`;
  const requests = [];
  await page.route(`**${endpoint}`, async route => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'offline', message: 'Connection interrupted' }) });
  });
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
  await expect(editor).toHaveValue('Explain the attached references.');
  await expect(attached.locator('li[data-upload="ready"]')).toHaveCount(2);
  await page.unroute(`**${endpoint}`);
  // The deliberately failed HTTP response is expected; preserve every other browser error.
  expect(fixture.errors.filter(error => error.includes('503 (Service Unavailable)'))).toHaveLength(1);
  fixture.errors.splice(fixture.errors.findIndex(error => error.includes('503 (Service Unavailable)')), 1);
  const sent = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(endpoint));
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const response = await sent;
  expect(response.ok(), await response.text()).toBe(true);
  expect(response.request().postDataJSON()).toEqual(requests[0]);
  await expect(editor).toHaveValue('');
  await expect(page.locator('.conversation-editor .conversation-attachments')).toHaveCount(0);
  const saved = page.locator('.team-manager-timeline .conversation-attachments');
  await expect(saved.getByRole('link')).toHaveCount(2);
  await page.reload();
  await expect(saved.getByRole('link')).toHaveCount(2);
  expect((await page.request.get(await saved.getByRole('link').first().getAttribute('href'))).ok()).toBe(true);

  await editor.fill('Unsent text stays here.');
  await page.setViewportSize({ width: 390, height: 844 });
  const original = await editor.boundingBox();
  await page.locator('#team-tab-execution').click();
  await expect(page.locator('#team-panel-execution')).toBeVisible();
  expect(await editor.boundingBox()).toEqual(original);
  await page.locator('#team-panel-execution').getByRole('button', { name: 'Close panel' }).click();
  await page.locator('#team-tab-outcomes').click();
  await expect(page.locator('#team-panel-outcomes')).toBeVisible();
  await editor.fill('Still editable with details open.');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
