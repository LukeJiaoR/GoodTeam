import { test, expect } from '@playwright/test';
import http from 'node:http';
import process from 'node:process';

test('coding and memory models persist independently in PG and reuse saved credentials', async ({ page, request }) => {
  test.skip(!process.env.GOODTEAM_DATABASE_URL, 'Requires disposable PostgreSQL');
  const modelRequests = [];
  const provider = http.createServer((req, res) => {
    modelRequests.push({ path: req.url, authorization: req.headers.authorization });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ data: [{ id: 'fixture-model' }] }));
  });
  await new Promise((resolve) => provider.listen(0, '127.0.0.1', resolve));
  try {
    await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));
    await page.goto('/settings');
    await page.getByRole('button', { name: 'Add provider' }).click();
    await page.getByLabel('Provider name').fill('Browser fixture');
    await page.getByLabel('Base URL', { exact: false }).fill(`http://127.0.0.1:${provider.address().port}/v1`);
    await page.getByLabel('API Key', { exact: false }).fill('browser-fixture-key');
    await page.getByRole('form', { name: 'Add provider' }).getByRole('button', { name: 'Fetch models' }).click();
    await page.getByLabel('Choose a fetched model').selectOption('fixture-model');
    await page.getByRole('button', { name: 'Save provider' }).click();
    const card = page.getByRole('article', { name: 'Browser fixture' });
    await card.getByRole('button', { name: 'Use provider' }).click();
    await expect(card.getByText('Active', { exact: true })).toBeVisible();
    await page.reload();
    await expect(card.getByText('Active', { exact: true })).toBeVisible();
    await card.getByRole('button', { name: 'Edit provider' }).click();
    await expect(page.locator('input[type="password"]')).toHaveValue('');
    await page.getByRole('form', { name: 'Edit provider' }).getByRole('button', { name: 'Fetch models' }).click();
    await expect(page.getByLabel('Choose a fetched model')).toBeVisible();
    expect(modelRequests).toEqual(Array(2).fill({ path: '/v1/models', authorization: 'Bearer browser-fixture-key' }));
    const publicState = await (await request.get('/api/providers')).json();
    expect(JSON.stringify(publicState)).not.toContain('browser-fixture-key');
    expect(publicState.storage).toBe('postgresql');
    const badOrigin = await request.post('/api/providers', { data: { action: 'activate', id: '', expected_revision: publicState.revision }, headers: { Origin: 'https://untrusted.invalid' } });
    expect(badOrigin.status()).toBe(403);
    const badType = await request.post('/api/providers', { data: '{}', headers: { 'Content-Type': 'text/plain' } });
    expect(badType.status()).toBe(415);
    const memory = page.getByRole('form', { name: 'Project memory retrieval' });
    await memory.getByLabel('Memory provider').selectOption(publicState.active.codex);
    await memory.getByLabel('Embedding model').fill('fixture-embedding');
    await memory.getByRole('button', { name: 'Save memory retrieval' }).click();
    await expect(page.getByText('Memory retrieval saved. New pickups use these settings; existing task inputs remain captured.')).toBeVisible();
    await page.reload();
    await expect(memory.getByLabel('Embedding model')).toHaveValue('fixture-embedding');
    await expect(card.getByText('Active', { exact: true })).toBeVisible();
    const withMemory = await (await request.get('/api/providers')).json();
    expect(withMemory.active).toEqual(publicState.active);
    expect(withMemory.providers).toEqual(publicState.providers);
    expect(withMemory.embeddings).toMatchObject({ provider_id: publicState.active.codex, model: 'fixture-embedding' });
    expect(JSON.stringify(withMemory)).not.toContain('browser-fixture-key');
    await card.getByRole('button', { name: 'Edit provider' }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('button', { name: 'Save provider' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: 'test-results/providers-mobile.png', fullPage: true });
  } finally {
    await new Promise((resolve) => provider.close(resolve));
  }
});
