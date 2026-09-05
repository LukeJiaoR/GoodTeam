import crypto from 'node:crypto';
import fs from 'node:fs';
import { expect, test } from '@playwright/test';

const root = '/private/tmp/goodteam-project-load-e2e';
const existingRoot = `${root}/targets/existing`;

function targetSnapshot(directory) {
  const visit = (current, relative = '') => fs.readdirSync(current, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) => {
      const absolute = `${current}/${entry.name}`;
      const childRelative = relative ? `${relative}/${entry.name}` : entry.name;
      const stat = fs.lstatSync(absolute);
      const record = {
        path: childRelative,
        mode: stat.mode,
        size: stat.size,
        type: entry.isDirectory() ? 'directory' : entry.isSymbolicLink() ? 'symlink' : 'file',
      };
      if (entry.isDirectory()) return [record, ...visit(absolute, childRelative)];
      if (entry.isFile()) {
        record.sha256 = crypto.createHash('sha256').update(fs.readFileSync(absolute)).digest('hex');
      } else if (entry.isSymbolicLink()) {
        record.target = fs.readlinkSync(absolute);
      }
      return [record];
    });
  return visit(directory);
}

test('project entry paths converge on Work without writing to the target', async ({ page }) => {
  const consoleErrors = [];
  const before = targetSnapshot(existingRoot);
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));

  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: 'Add your first project' })).toBeVisible();
  await page.getByRole('button', { name: 'Add project' }).first().click();
  await page.getByLabel('Project directory').fill(existingRoot);
  await page.getByRole('button', { name: 'Load project' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work$/);
  await expect(page.getByRole('heading', { name: 'Task Assistant' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start understanding the codebase' })).toHaveCount(0);

  await page.goto('/projects');
  await page.getByRole('button', { name: 'Enter workspace: existing' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work$/);

  await page.goto('/projects/EXISTING/home');
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work$/);
  await expect(page.getByRole('heading', { name: 'Task Assistant' })).toBeVisible();

  expect(targetSnapshot(existingRoot)).toEqual(before);
  expect(consoleErrors).toEqual([]);
});
