import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { test, expect, importProject } from './fixtures.js';

const git = (root, ...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' }).trim();
const panelFor = (page) => page.getByRole('region', { name: 'Git baseline and recovery', exact: true });

function sourceBytes(root) {
  const visit = (relative = '') => Object.fromEntries(fs.readdirSync(path.join(root, relative), { withFileTypes: true })
    .filter((entry) => entry.name !== '.git')
    .flatMap((entry) => {
      const file = path.join(relative, entry.name);
      return entry.isDirectory() ? Object.entries(visit(file)) : [[file, fs.readFileSync(path.join(root, file)).toString('base64')]];
    }));
  return visit();
}

async function inspect(page, state) {
  const panel = panelFor(page);
  await panel.getByRole('button', { name: 'Git baseline and recovery', exact: true }).click();
  await expect(panel.getByRole('status')).toHaveText(state);
  return panel;
}

async function confirm(page, panel, fixture, files) {
  const response = page.waitForResponse((item) => item.url().endsWith('/api/git-init') && item.request().method() === 'POST');
  await panel.getByRole('button', { name: 'Confirm initial commit', exact: true }).click();
  const result = await response;
  expect(result.request().postDataJSON()).toEqual({ project_key: fixture.key, initial_files: files, create_remote: false });
  expect(result.status(), await result.text()).toBe(200);
  expect(await result.json()).toMatchObject({ initialized: true, remote_created: false, state: 'valid_head' });
  await expect(panel.getByRole('status')).toHaveText('Valid Git commit');
}

test('non-Git import stays read-only until the exact selection is explicitly confirmed', async ({ page, fixture }) => {
  const before = sourceBytes(fixture.root);
  const posts = [];
  page.on('request', (request) => {
    if (request.url().endsWith('/api/git-init') && request.method() === 'POST') posts.push(request.postDataJSON());
  });
  await importProject(page, fixture);
  const panel = await inspect(page, 'Git is not initialized');
  expect(sourceBytes(fixture.root)).toEqual(before);
  expect(fs.existsSync(path.join(fixture.root, '.git'))).toBe(false);

  const input = panel.getByLabel('Select exact relative file paths, one per line');
  const review = panel.getByRole('button', { name: 'Review selected files', exact: true });
  const confirmButton = panel.getByRole('button', { name: 'Confirm initial commit', exact: true });
  await input.fill('../outside.txt');
  await review.click();
  await expect(panel.getByRole('alert').filter({ hasText: 'Enter regular relative file paths' })).toBeVisible();
  await expect(confirmButton).toHaveCount(0);

  await input.fill('README.md');
  await review.click();
  await expect(confirmButton).toBeVisible();
  await panel.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(confirmButton).toHaveCount(0);
  await review.click();
  await input.fill('src/main.js');
  await expect(confirmButton).toHaveCount(0);
  expect(posts).toEqual([]);
  expect(sourceBytes(fixture.root)).toEqual(before);
  expect(fs.existsSync(path.join(fixture.root, '.git'))).toBe(false);

  await review.click();
  await expect(panel.getByRole('listitem')).toHaveText(['src/main.js']);
  await confirm(page, panel, fixture, ['src/main.js']);
  expect(posts).toEqual([{ project_key: fixture.key, initial_files: ['src/main.js'], create_remote: false }]);
  expect(git(fixture.root, 'ls-tree', '-r', '--name-only', 'HEAD')).toBe('src/main.js');
  expect(sourceBytes(fixture.root)).toEqual(before);
  expect(git(fixture.root, 'remote')).toBe('');
  const head = git(fixture.root, 'rev-parse', 'HEAD');
  await page.reload();
  const reopened = await inspect(page, 'Valid Git commit');
  await expect(reopened.locator('code')).toHaveText(head);
  await expect(reopened.getByRole('button', { name: 'Confirm initial commit', exact: true })).toHaveCount(0);
  expect(git(fixture.root, 'rev-list', '--count', 'HEAD')).toBe('1');
});

test('unborn recovery preserves branch, identity, unrelated index and working files on re-entry', async ({ page, fixture }) => {
  git(fixture.root, 'init', '-b', 'retained-branch');
  git(fixture.root, 'config', 'user.name', 'Recovery Fixture');
  git(fixture.root, 'config', 'user.email', 'recovery@example.invalid');
  const staged = path.join(fixture.root, 'unrelated.txt');
  fs.writeFileSync(staged, 'staged bytes\n');
  git(fixture.root, 'add', '--', 'unrelated.txt');
  fs.writeFileSync(staged, 'different working bytes\n');
  const before = sourceBytes(fixture.root);
  const configBefore = fs.readFileSync(path.join(fixture.root, '.git', 'config'));
  const indexBefore = git(fixture.root, 'show', ':unrelated.txt');
  await importProject(page, fixture);
  const panel = await inspect(page, 'Initial commit missing');
  expect(sourceBytes(fixture.root)).toEqual(before);
  expect(git(fixture.root, 'diff', '--cached', '--name-only')).toBe('unrelated.txt');
  await panel.getByLabel('Select exact relative file paths, one per line').fill('README.md');
  await panel.getByRole('button', { name: 'Review selected files', exact: true }).click();
  await confirm(page, panel, fixture, ['README.md']);

  const head = git(fixture.root, 'rev-parse', 'HEAD');
  expect(git(fixture.root, 'ls-tree', '-r', '--name-only', 'HEAD')).toBe('README.md');
  expect(git(fixture.root, 'branch', '--show-current')).toBe('retained-branch');
  expect(fs.readFileSync(path.join(fixture.root, '.git', 'config'))).toEqual(configBefore);
  expect(git(fixture.root, 'show', '-s', '--format=%an <%ae>', 'HEAD')).toBe('Recovery Fixture <recovery@example.invalid>');
  expect(git(fixture.root, 'show', ':unrelated.txt')).toBe(indexBefore);
  expect(git(fixture.root, 'diff', '--cached', '--name-only')).toBe('unrelated.txt');
  expect(git(fixture.root, 'diff', '--name-only')).toBe('unrelated.txt');
  expect(sourceBytes(fixture.root)).toEqual(before);
  expect(git(fixture.root, 'remote')).toBe('');

  await page.goto('/projects');
  await page.getByRole('link', { name: `Enter workspace: ${fixture.name}` }).click();
  const reopened = await inspect(page, 'Valid Git commit');
  await expect(reopened.locator('code')).toHaveText(head);
  await reopened.getByRole('button', { name: 'Refresh Git status', exact: true }).click();
  await expect(reopened.getByRole('status')).toHaveText('Valid Git commit');
  await expect(reopened.getByRole('button', { name: 'Confirm initial commit', exact: true })).toHaveCount(0);
  expect(git(fixture.root, 'rev-parse', 'HEAD')).toBe(head);
  expect(git(fixture.root, 'rev-list', '--count', 'HEAD')).toBe('1');
});
