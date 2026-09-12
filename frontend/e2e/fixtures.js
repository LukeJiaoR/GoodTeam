import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { test as base, expect } from '@playwright/test';

export const test = base.extend({
  fixture: async ({ page }, use, testInfo) => {
    const name = `project-${testInfo.testId.replace(/[^a-zA-Z0-9]/g, '').slice(-12)}-${testInfo.repeatEachIndex}`;
    const root = path.join(process.env.GOODTEAM_E2E_ROOT, 'targets', name);
    fs.mkdirSync(path.join(root, 'src'), { recursive: true });
    fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({
      name, scripts: { test: 'node --test', build: 'node --check src/main.js' },
      dependencies: { react: '^19.0.0' },
    }));
    fs.writeFileSync(path.join(root, 'README.md'), '# Isolated acceptance fixture\n');
    fs.writeFileSync(path.join(root, 'src', 'main.js'), "export const fixture = 'read-only source';\n");
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));
    await use({ root, name, key: name.toUpperCase().replaceAll('-', '_'), errors });
    expect(errors, 'Browser console and unhandled errors').toEqual([]);
  },
});

export { expect };

export async function importProject(page, fixture) {
  await page.goto('/projects');
  await page.getByRole('button', { name: 'Add project', exact: true }).first().click();
  await page.getByLabel('Project directory').fill(fixture.root);
  const response = page.waitForResponse((item) => item.url().endsWith('/api/projects/import') && item.request().method() === 'POST');
  await page.getByRole('button', { name: 'Load project', exact: true }).click();
  const imported = await response;
  expect(imported.status(), await imported.text()).toBe(201);
  const payload = await imported.json();
  fixture.key = payload.project.project_key;
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await expect(page.getByRole('heading', { name: 'Task Assistant', exact: true })).toBeVisible();
  return payload;
}
