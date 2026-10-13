import crypto from 'node:crypto';
import fs from 'node:fs';
import { expect, test, importProject } from './fixtures.js';


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

test('project entry paths converge on Work without writing to the target', async ({ page, fixture }) => {
  const before = targetSnapshot(fixture.root);

  await importProject(page, fixture);
  await expect(page.getByRole('button', { name: 'Start understanding the codebase' })).toHaveCount(0);

  await page.goto('/projects');
  await page.getByRole('button', { name: `Enter workspace: ${fixture.name}` }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));

  await page.goto(`/projects/${fixture.key}/home`);
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await expect(page.getByRole('heading', { name: 'Task Assistant' })).toBeVisible();

  expect(targetSnapshot(fixture.root)).toEqual(before);
});

test('legacy bookmarks redirect and the project shell reaches Team/Review/Knowledge', async ({ page, fixture }) => {
  await importProject(page, fixture);

  for (const legacy of ['/inbox', '/activity']) {
    await page.goto(legacy);
    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole('heading', { name: 'Choose a project' })).toBeVisible();
  }

  await page.goto(`/projects/${fixture.key}/settings/general`);
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/team$`));
  await expect(page.getByRole('heading', { name: 'Team', exact: true })).toBeVisible();

  await page.goto(`/projects/${fixture.key}/home`);
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await expect(page.getByRole('heading', { name: 'Task Assistant' })).toBeVisible();

  await page.getByRole('link', { name: 'Reviews', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/review$`));
  await expect(page.getByRole('heading', { name: 'Review', exact: true })).toBeVisible();

  await page.getByRole('link', { name: 'Knowledge base', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/knowledge$`));
  await expect(page.getByRole('heading', { name: 'Knowledge', exact: true })).toBeVisible();

  await page.getByRole('link', { name: 'Team', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/team$`));
  await expect(page.getByRole('heading', { name: 'Team', exact: true })).toBeVisible();

});

test('global and project settings support direct links, refresh, and legacy sections', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/settings$`));
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Configure team', exact: true })).toBeVisible();

  for (const route of [`/projects/${fixture.key}/settings`, '/settings']) {
    const direct = await page.goto(route);
    expect(direct.status()).toBe(200);
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    const refreshed = await page.reload();
    expect(refreshed.status()).toBe(200);
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  }

  await page.goto(`/projects/${fixture.key}/settings/runtime`);
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/team$`));
  await expect(page.getByRole('heading', { name: 'Team', exact: true })).toBeVisible();
});
