import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { expect, importProject, test } from './fixtures.js';

test('installs a server-local Skill through the real browser and keeps it after reload', async ({ page, fixture }) => {
  const skillName = `browser-review-${fixture.name.replace(/[^a-z0-9]/gi, '').slice(-10).toLowerCase()}`;
  const skillRoot = path.join(process.env.GOODTEAM_E2E_ROOT, 'skills', skillName);
  fs.mkdirSync(path.join(skillRoot, 'references'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'SKILL.md'), [
    '---',
    `name: ${skillName}`,
    'description: Browser-installed review method.',
    'license: MIT',
    '---',
    'Review persistence boundaries and cite concrete source evidence.',
    '',
  ].join('\n'));
  fs.writeFileSync(path.join(skillRoot, 'references', 'rules.md'), 'Check retry behavior.\n');

  await importProject(page, fixture);
  await page.goto(`/projects/${fixture.key}/team`);
  await page.getByRole('button', { name: 'Skills', exact: true }).click();

  let dialog = page.getByRole('dialog', { name: 'Skills' });
  await dialog.getByLabel('Server directory').fill(skillRoot);
  const previewResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/skills/local/preview') && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Preview', exact: true }).click();
  const preview = await previewResponse;
  expect(preview.status(), await preview.text()).toBe(200);
  await expect(dialog.getByText('Browser-installed review method.', { exact: true })).toBeVisible();
  await expect(dialog.getByText('MIT', { exact: true })).toBeVisible();

  const installResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/skills/local/install') && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Install Skill', exact: true }).click();
  const installed = await installResponse;
  expect(installed.status(), await installed.text()).toBe(200);
  await expect(dialog.getByText('Installed. This Skill is not enabled for any role yet.', { exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: 'Skills', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Skills' });
  await expect(dialog.getByText(skillName, { exact: true })).toBeVisible();
  await expect(dialog.getByText('Browser-installed review method.', { exact: true })).toBeVisible();
  expect(fixture.errors).toEqual([]);
});
