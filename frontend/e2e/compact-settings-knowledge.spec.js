import fs from 'node:fs';
import { test, expect } from './fixtures.js';

// UI-only Provider fixture: never sends credentials or requests a real model.
// Project import, navigation and knowledge overview use the isolated backend.
test('compact settings and knowledge retain project context, controls and readable responsive layouts', async ({ page, fixture }, testInfo) => {
  await page.route('**/api/providers', route => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill({ json: { configured: true, revision: 1, active: { codex: 'fixture' }, providers: [
      { id: 'fixture', name: 'Local UI fixture · retained project connection', agent: 'codex', protocol: 'responses',
        base_url: 'https://example.invalid/v1', model: 'fixture-only', has_api_key: true },
    ] } });
  });
  await page.route('**/api/providers/usage', route => route.fulfill({ json: {
    totals: { invocations: 3, reported_invocations: 2, total_tokens: 210, input_tokens: 200, cached_input_tokens: 80, output_tokens: 10 }, by_model: [],
  } }));
  const imported = await page.request.post('/api/projects/import', { data: { root: fixture.root } });
  expect(imported.status()).toBe(201);
  fixture.key = (await imported.json()).project.project_key;
  const metrics = [];
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`/projects/${fixture.key}/settings`);
  await expect(page.getByRole('article', { name: 'Local UI fixture · retained project connection' })).toBeVisible();
  await page.getByRole('button', { name: 'Add provider', exact: true }).click();
  await expect(page.getByRole('form', { name: 'Add provider' })).toBeVisible();
  await page.getByLabel('Provider name').fill('Unsaved UI-only draft');
  await page.getByLabel('Provider name').focus();
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => document.documentElement.classList.toggle('dark', theme === 'dark'), theme);
    metrics.push(await page.locator('.studio-settings-page').evaluate(el => ({
      page: 'settings', title: parseFloat(getComputedStyle(el.querySelector('h1')).fontSize),
      button: el.querySelector('.product-button').getBoundingClientRect().height,
      input: parseFloat(getComputedStyle(el.querySelector('input')).fontSize),
    })));
    await page.screenshot({ path: testInfo.outputPath(`settings-${theme}-1920.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.getByLabel('Provider name').evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
  await page.screenshot({ path: testInfo.outputPath('settings-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`/projects/${fixture.key}/knowledge`);
  await expect(page.locator('.product-knowledge-page h1')).toBeVisible();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => document.documentElement.classList.toggle('dark', theme === 'dark'), theme);
    metrics.push(await page.locator('.product-knowledge-page').evaluate(el => ({
      page: 'knowledge', title: parseFloat(getComputedStyle(el.querySelector('h1')).fontSize),
      button: el.querySelector('.knowledge-view-switcher button').getBoundingClientRect().height,
    })));
    await page.screenshot({ path: testInfo.outputPath(`knowledge-${theme}-1920.png`), fullPage: true });
  }
  await page.locator('.knowledge-view-switcher button').last().click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/knowledge.*memory`));
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('knowledge-mobile.png'), fullPage: true });
  for (const item of metrics) {
    expect(item.title).toBe(16);
    expect(item.button).toBeGreaterThanOrEqual(32);
    expect(item.button).toBeLessThanOrEqual(34);
  }
  fs.writeFileSync(testInfo.outputPath('compact-metrics.json'), JSON.stringify(metrics, null, 2));
});
