import fs from 'node:fs';
import { expect, test } from '@playwright/test';

const root = '/private/tmp/goodteam-project-load-e2e';
const emptyRoot = `${root}/targets/empty`;
const existingRoot = `${root}/targets/existing`;
const ambiguousRoot = `${root}/targets/ambiguous`;
const blockedRoot = `${root}/targets/blocked`;

test('loads once, restores Home, confirms mode, and switches language', async ({ page }) => {
  const consoleErrors = [];
  const unreadableResponses = [];
  let expectingUnreadableResponse = false;
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    if (expectingUnreadableResponse && message.text().includes('403 (Forbidden)')) return;
    consoleErrors.push(message.text());
  });
  page.on('response', (response) => {
    const { pathname } = new URL(response.url());
    if (expectingUnreadableResponse && pathname === '/api/projects/import') {
      unreadableResponses.push(response.status());
    }
  });
  await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));

  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: 'No projects loaded' })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('projects-en-light-desktop.png', {
    fullPage: true,
    maxDiffPixels: 120,
  });

  await page.getByLabel('Project directory').fill(existingRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/home$/);
  await expect(page.getByLabel('Next action').getByText('Repository understanding')).toBeVisible();
  await page.reload();
  await expect(page.locator('.project-home-root')).toHaveText(existingRoot);

  await page.goto('/projects');
  await page.getByLabel('Project directory').fill(existingRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/home$/);
  await page.goto('/projects');
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'existing', exact: true })).toHaveCount(1);

  await page.getByLabel('Project directory').fill(emptyRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EMPTY\/home$/);
  await expect(page.getByLabel('Next action').getByText('New project initialization')).toBeVisible();

  await page.goto('/projects');
  try {
    expectingUnreadableResponse = true;
    await page.getByLabel('Project directory').fill(blockedRoot);
    await page.getByRole('button', { name: 'Load project' }).click();
    await expect(page.getByText('GoodTeam cannot read this directory.')).toBeVisible();
  } finally {
    expectingUnreadableResponse = false;
    fs.chmodSync(blockedRoot, 0o700);
  }

  await page.getByLabel('Project directory').fill(ambiguousRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/AMBIGUOUS\/home$/);
  await page.getByRole('button', { name: 'Understand an existing codebase' }).click();
  await expect(page.getByLabel('Next action').getByText('Repository understanding')).toBeVisible();

  await page.getByLabel('Language').selectOption('zh-CN');
  await expect(page.getByLabel('下一步操作').getByText('理解代码库')).toBeVisible();
  await expect(page.locator('.project-home-root')).toHaveText(ambiguousRoot);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('home-zh-light-mobile.png', { fullPage: true });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => {
    localStorage.setItem('gt-language', 'en');
    localStorage.setItem('color-scheme', 'dark');
  });
  await page.reload();
  await expect(page.getByLabel('Next action').getByText('Repository understanding')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('home-en-dark-desktop.png', { fullPage: true });

  expect(fs.existsSync(`${existingRoot}/docs`)).toBe(false);
  expect(fs.existsSync(`${existingRoot}/.git`)).toBe(false);
  expect(fs.readdirSync(emptyRoot)).toEqual([]);
  expect(fs.readFileSync(`${ambiguousRoot}/blob.bin`, 'utf8')).toBe('asset');
  expect(unreadableResponses).toEqual([403]);
  expect(consoleErrors).toEqual([]);
});
