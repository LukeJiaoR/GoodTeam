import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { expect, importProject, test } from './fixtures.js';

test('installs a server-local Skill, binds it to QA, and preserves Role state after reload', async ({ page, fixture }) => {
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
  const installedText = await installed.text();
  expect(installed.status(), installedText).toBe(200);
  const installedPayload = JSON.parse(installedText);
  await expect(dialog.getByText('Installed. This Skill is not enabled for any role yet.', { exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: 'Skills', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Skills' });
  await expect(dialog.getByText(skillName, { exact: true })).toBeVisible();
  await expect(dialog.getByText('Browser-installed review method.', { exact: true })).toBeVisible();
  await dialog.locator('footer').getByRole('button', { name: 'Close Skills', exact: true }).click();

  const beforeResponse = await page.request.get(`/api/state?project=${fixture.key}`);
  expect(beforeResponse.ok()).toBeTruthy();
  const beforeState = await beforeResponse.json();
  const qaBefore = beforeState.team.roles.find((entry) => entry.role === 'qa');
  expect(qaBefore.skill_binding.status).toBe('default');

  await page.getByRole('button', { name: 'Show roles in Quality & delivery', exact: true }).click();
  let qa = page.getByRole('article', { name: 'QA', exact: true });
  await qa.getByRole('button', { name: 'Methods', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'QA methods', exact: true });
  await dialog.getByRole('radio', { name: /Custom Skills/ }).check();
  await expect(dialog.getByText(skillName, { exact: true })).toBeVisible();
  await dialog.getByRole('checkbox', { name: new RegExp(skillName) }).check();

  const bindResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/api/projects/${fixture.key}/team/commands`)
      && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Save methods', exact: true }).click();
  const bound = await bindResponse;
  const boundText = await bound.text();
  expect(bound.status(), boundText).toBe(200);
  const boundPayload = JSON.parse(boundText);
  const qaBound = boundPayload.team.roles.find((entry) => entry.role === 'qa');
  expect(qaBound.skill_binding.status).toBe('configured');
  expect(qaBound.skill_binding.mode).toBe('custom');
  expect(qaBound.skill_binding.items).toHaveLength(1);
  expect(qaBound.skill_binding.items[0].skill_id).toBe(installedPayload.installed.skill_id);
  expect(qaBound.skill_binding.items[0].version_id).toBe(installedPayload.installed.version_id);
  expect(qaBound.skill_binding.items[0].activation).toBe('required');

  await expect(page.getByRole('dialog', { name: 'QA methods', exact: true })).toHaveCount(0);
  qa = page.getByRole('article', { name: 'QA', exact: true });
  await expect(qa.getByText('Custom methods', { exact: true })).toBeVisible();
  await expect(qa.getByText('Saved only · execution not connected', { exact: true })).toBeVisible();

  const afterResponse = await page.request.get(`/api/state?project=${fixture.key}`);
  expect(afterResponse.ok()).toBeTruthy();
  const afterState = await afterResponse.json();
  const qaAfter = afterState.team.roles.find((entry) => entry.role === 'qa');
  expect(qaAfter.state).toBe(qaBefore.state);
  expect(qaAfter.workers).toEqual(qaBefore.workers);
  expect(qaAfter.skill_binding.status).toBe('configured');

  await page.reload();
  await page.getByRole('button', { name: 'Show roles in Quality & delivery', exact: true }).click();
  qa = page.getByRole('article', { name: 'QA', exact: true });
  await expect(qa.getByText('Custom methods', { exact: true })).toBeVisible();
  await qa.getByRole('button', { name: 'Methods', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'QA methods', exact: true });
  await expect(dialog.getByRole('checkbox', { name: new RegExp(skillName) })).toBeChecked();
  await expect(dialog.getByText(/not connected to Worker execution yet/i)).toBeVisible();
  expect(fixture.errors).toEqual([]);
});
