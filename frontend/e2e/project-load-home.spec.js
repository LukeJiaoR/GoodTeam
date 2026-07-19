import fs from 'node:fs';
import crypto from 'node:crypto';
import net from 'node:net';
import process from 'node:process';
import { spawn } from 'node:child_process';
import { expect, test } from '@playwright/test';

const root = '/private/tmp/goodteam-project-load-e2e';
const emptyRoot = `${root}/targets/empty`;
const existingRoot = `${root}/targets/existing`;
const ambiguousRoot = `${root}/targets/ambiguous`;
const blockedRoot = `${root}/targets/blocked`;

test.afterEach(() => {
  const settingsDirectory = `${root}/workspace/.goodteam`;
  fs.mkdirSync(settingsDirectory, { recursive: true });
  fs.writeFileSync(`${settingsDirectory}/product-settings.json`, JSON.stringify({
    model_profile: 'balanced',
    worker_backend: 'codex',
  }, null, 2) + '\n');
});

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

function useFakeWorker(projectKey, workspace = `${root}/workspace`) {
  const config = `${workspace}/projects/${projectKey.toLowerCase()}.local.yaml`;
  const content = fs.readFileSync(config, 'utf8');
  fs.writeFileSync(config, content.replace(/(\s+mode:\s*)"?codex"?/, '$1"fake"'));
}

function useUnavailableWorker(projectKey, workspace = `${root}/workspace`) {
  const config = `${workspace}/projects/${projectKey.toLowerCase()}.local.yaml`;
  const content = fs.readFileSync(config, 'utf8');
  fs.writeFileSync(config, content.replace(/(\s+mode:\s*)"?codex"?/, '$1"unavailable-e2e"'));
}

async function importProject(page, projectRoot, expectedKey) {
  await page.goto('/projects');
  await page.getByLabel(/Project directory|项目目录/).fill(projectRoot);
  await page.getByRole('button', { name: /Load project|加载项目/ }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${expectedKey}/home$`));
}

async function completeExistingConfirmations(page) {
  const command = page.getByRole('checkbox').first();
  await expect(command).toBeVisible();
  await command.focus();
  await command.press('Space');
  await expect(command).toBeChecked();
  const confirm = page.getByRole('button', { name: /Confirm and continue|确认并继续/ });
  await expect(confirm).toBeEnabled();
  confirm.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText(/Confirm project constraints|确认项目约束/)).toBeVisible();
  await page.getByRole('button', { name: /Confirm and continue|确认并继续/ }).click();
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  return port;
}

async function startOwnedServer(workspace, port) {
  const child = spawn(
    process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python',
    ['-m', 'goodteam_os.cli', 'serve', '--workspace', workspace, '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: process.cwd(),
      env: { ...process.env, PYTHONPATH: '../src' },
      stdio: 'ignore',
    },
  );
  const url = `http://127.0.0.1:${port}`;
  let lastError;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`Owned GoodTeam server exited with ${child.exitCode}.`);
    try {
      const response = await fetch(`${url}/projects`);
      if (response.ok) return { child, url };
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  child.kill('SIGTERM');
  throw new Error(`Owned GoodTeam server did not become ready: ${lastError?.message || 'timeout'}`);
}

async function stopOwnedServer(child) {
  if (!child || child.exitCode !== null) return;
  child.kill('SIGTERM');
  const exited = new Promise((resolve) => child.once('exit', resolve));
  const timedOut = await Promise.race([
    exited.then(() => false),
    new Promise((resolve) => setTimeout(() => resolve(true), 5_000)),
  ]);
  if (timedOut && child.exitCode === null) {
    child.kill('SIGKILL');
    await new Promise((resolve) => child.once('exit', resolve));
  }
}

test('loads once, restores Home, confirms mode, and switches language', async ({ page }) => {
  const consoleErrors = [];
  const unreadableResponses = [];
  let expectingUnreadableResponse = false;
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const sourceUrl = message.location().url;
    const isExpectedUnreadableLog = expectingUnreadableResponse
      && message.text().includes('403 (Forbidden)')
      && sourceUrl
      && new URL(sourceUrl).pathname === '/api/projects/import';
    if (isExpectedUnreadableLog) return;
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
  await expect(page.getByRole('heading', { name: 'Prepare a new project' })).toBeVisible();

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

  await page.getByLabel('Language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByLabel('下一步操作').getByText('理解代码库', { exact: true })).toBeVisible();
  await expect(page.locator('.project-home-root')).toHaveText(ambiguousRoot);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.project-home-page')).toBeVisible();
  await expect(page.getByRole('button', { name: '开始理解代码库' })).toBeVisible();

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => {
    localStorage.setItem('gt-language', 'en');
    localStorage.setItem('color-scheme', 'dark');
  });
  await page.reload();
  await expect(page.getByLabel('Next action').getByText('Repository understanding')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start understanding the codebase' })).toBeVisible();

  expect(fs.existsSync(`${existingRoot}/docs`)).toBe(false);
  expect(fs.existsSync(`${existingRoot}/.git`)).toBe(false);
  expect(fs.readdirSync(existingRoot).sort()).toEqual(['README.md', 'package.json', 'src']);
  expect(fs.readFileSync(`${existingRoot}/package.json`, 'utf8')).toBe(
    JSON.stringify({
      name: 'existing',
      scripts: {
        start: 'vite',
        test: 'vitest run',
        lint: 'eslint .',
        build: 'vite build',
      },
      dependencies: { react: '^19.0.0' },
    }, null, 2),
  );
  expect(fs.readdirSync(emptyRoot)).toEqual([]);
  expect(fs.readdirSync(ambiguousRoot)).toEqual(['blob.bin']);
  expect(fs.readFileSync(`${ambiguousRoot}/blob.bin`, 'utf8')).toBe('asset');
  expect(unreadableResponses).toEqual([403]);
  expect(consoleErrors).toEqual([]);
});

test('existing project readiness is real, resumable, read-only, and bridges to workflow', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));
  const before = targetSnapshot(existingRoot);

  await importProject(page, existingRoot, 'EXISTING');
  useFakeWorker('EXISTING');
  await page.getByRole('button', { name: 'Start understanding the codebase' }).click();

  await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();
  await expect(page.getByText('npm run test').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Codebase overview' })).toBeVisible();
  await expect(page.getByText('Node.js / JavaScript')).toBeVisible();
  await expect(page.getByText('src/main.jsx')).toBeVisible();

  await page.reload();
  await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();
  await expect(page.getByText('npm run test').first()).toBeVisible();
  await completeExistingConfirmations(page);

  const workflow = page.getByRole('button', { name: 'Start workflow' });
  await expect(workflow).toBeEnabled();
  await workflow.click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work$/);
  await expect(page.getByRole('heading', { name: 'Work' })).toBeVisible();

  expect(targetSnapshot(existingRoot)).toEqual(before);
});

test('product routes, deep links, navigation state, locale, theme, and mobile never enter legacy', async ({ page }) => {
  const legacyRequests = [];
  const consoleErrors = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/legacy') legacyRequests.push(request.url());
  });
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.goto('/projects');
  await page.evaluate(() => localStorage.setItem('gt-language', 'en'));
  await page.reload();
  await importProject(page, existingRoot, 'EXISTING');
  await expect(page.getByLabel('Next action')).toBeVisible();
  const startUnderstanding = page.getByRole('button', { name: 'Start understanding the codebase' });
  if (await startUnderstanding.count()) {
    useFakeWorker('EXISTING');
    await startUnderstanding.click();
    await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();
    await completeExistingConfirmations(page);
    await expect(page.getByRole('button', { name: 'Start workflow' })).toBeEnabled();
  }

  await page.goto('/?project=EXISTING');
  await expect(page).toHaveURL(/\/projects\/EXISTING\/home$/);
  await expect(page.getByRole('heading', { name: 'existing', exact: true })).toBeVisible();

  await page.getByLabel('Project navigation').getByRole('link', { name: 'Work', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work$/);
  await expect(page.getByRole('heading', { name: 'Start a run' })).toBeVisible();
  await page.getByLabel('Run goal').fill('Prove the complete product workspace');
  await page.getByRole('button', { name: 'Start workflow' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work\/runs\/[^/?]+$/);
  const activeRunUrl = page.url();
  const firstRunId = new URL(activeRunUrl).pathname.split('/').at(-1);
  await expect(page.getByText('Highest-priority next step')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Workflow stages' }).getByRole('listitem')).not.toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Actions' })).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(activeRunUrl);
  await expect(page.getByRole('heading', { name: 'Work' })).toBeVisible();

  await page.getByRole('link', { name: 'Review', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/review\/[^/?]+$/);
  await expect(page.getByRole('heading', { name: 'Review', exact: true })).toBeVisible();
  await expect(page.locator('.review-queue a')).not.toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible();
  await expect(page.getByText('The server validates policy, evidence, identity, and ledger state before accepting this decision.')).toBeVisible();
  await expect(page.getByText('Evidence snapshot')).toBeVisible();
  await expect(page.getByText('Dissent', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Diff' }).click();
  await expect(page.getByLabel('Diff')).toBeVisible();
  await page.getByRole('button', { name: 'Artifact', exact: true }).click();
  const reviewLinks = page.locator('.review-queue a');
  for (let index = 0; index < await reviewLinks.count(); index += 1) {
    const candidate = reviewLinks.nth(index);
    if (!(await candidate.innerText()).includes('Action required')) {
      await candidate.click();
      await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
      await expect(page.getByText('This review is read-only because the server does not mark it actionable.')).toBeVisible();
      break;
    }
  }

  await page.goto('/inbox');
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();
  const inboxReview = page.locator('a[href*="/projects/EXISTING/review/"]').first();
  await expect(inboxReview).toBeVisible();
  await inboxReview.click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/review\/[^/?]+$/);

  await page.getByRole('link', { name: 'Knowledge', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/knowledge$/);
  await expect(page.getByRole('heading', { name: 'Knowledge', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Confirmed project knowledge' })).toBeVisible();
  await expect(page.getByText('npm run test').first()).toBeVisible();
  await expect(page.getByText('Node.js / JavaScript')).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/review\/[^/?]+$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/knowledge$/);

  await page.goto('/activity');
  await expect(page.getByRole('heading', { name: 'Activity' })).toBeVisible();
  await page.getByLabel('Project').selectOption('EXISTING');
  await page.getByLabel('Type').selectOption('operation');
  await page.getByLabel('Run ID').fill(firstRunId);
  await expect(page).toHaveURL(new RegExp(`activity\\?.*project=EXISTING.*type=operation.*run=${firstRunId}`));
  await expect(page.getByRole('heading', { name: /started workflow/i })).toBeVisible();

  await page.goto(activeRunUrl);
  await page.getByText('Run controls').click();
  await page.getByRole('button', { name: 'Terminate this run' }).click();
  await expect(page.getByText('Historical runs are read-only and visually separate from the active run.')).toBeVisible();
  await page.goto('/projects/EXISTING/work');
  await page.getByLabel('Run goal').fill('Prove full persisted run history');
  await page.getByRole('button', { name: 'Start workflow' }).click();
  await expect(page).toHaveURL(/\/projects\/EXISTING\/work\/runs\/[^/?]+$/);
  await expect(page.getByLabel('Select run').locator('option')).toHaveCount(2);
  await page.getByLabel('Select run').selectOption(firstRunId);
  await expect(page).toHaveURL(new RegExp(`/projects/EXISTING/work/runs/${firstRunId}$`));
  await expect(page.getByText('Historical runs are read-only and visually separate from the active run.')).toBeVisible();

  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'GoodTeam service' })).toBeVisible();
  await page.getByLabel('Default worker backend').selectOption('fake');
  await page.getByRole('button', { name: 'Save workspace defaults' }).click();
  await expect(page.getByText('Workspace defaults saved.')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Default worker backend')).toHaveValue('fake');
  await page.getByLabel('Default worker backend').selectOption('codex');
  await page.getByRole('button', { name: 'Save workspace defaults' }).click();
  await expect(page.getByText('Workspace defaults saved.')).toBeVisible();

  await page.getByLabel('Language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
  await page.getByLabel('使用深色外观').check();
  await expect(page.locator('html')).toHaveClass(/dark/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/projects/EXISTING/work');
  await expect(page.getByRole('heading', { name: '工作' })).toBeVisible();
  await expect(page.getByText('最高优先级下一步')).toBeVisible();

  expect(legacyRequests).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('new project form persists all inputs, completes preflight, and stays target-read-only at 390px', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('gt-language', 'zh-CN'));
  await page.setViewportSize({ width: 390, height: 844 });
  const before = targetSnapshot(emptyRoot);

  await importProject(page, emptyRoot, 'EMPTY');
  useFakeWorker('EMPTY');
  await page.getByLabel('项目目标').fill('交付本地内容工作室');
  await page.getByLabel('项目简述').fill('一个小型离线优先发布流程');
  await page.getByLabel('技术偏好').fill('React 与 Python');
  await page.getByLabel('必要约束').fill('离线优先');
  await page.getByLabel('禁止或需要审批的操作').fill('未经批准不得安装依赖');
  await page.getByRole('button', { name: '开始初始化项目' }).click();

  await expect(page.getByRole('button', { name: '启动工作流' })).toBeEnabled();
  await page.reload();
  await expect(page.getByRole('button', { name: '启动工作流' })).toBeEnabled();
  expect(targetSnapshot(emptyRoot)).toEqual(before);
});

test('failed worker preflight exposes recovery and retry completes without rescanning writes', async ({ page }) => {
  const retryTarget = `${root}/targets/retry`;
  fs.mkdirSync(retryTarget, { recursive: true });
  fs.writeFileSync(`${retryTarget}/package.json`, JSON.stringify({
    name: 'retry',
    scripts: { test: 'vitest run' },
  }, null, 2));
  const before = targetSnapshot(retryTarget);
  await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));

  await importProject(page, retryTarget, 'RETRY');
  useUnavailableWorker('RETRY');
  await page.getByRole('button', { name: 'Start understanding the codebase' }).click();
  await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();
  await completeExistingConfirmations(page);
  await expect(page.getByText('worker_preflight_failed')).toBeVisible();
  await expect(page.getByText('Worker preflight did not pass.')).toBeVisible();

  const config = `${root}/workspace/projects/retry.local.yaml`;
  fs.writeFileSync(config, fs.readFileSync(config, 'utf8').replace(/(\s+mode:\s*)"?unavailable-e2e"?/, '$1"fake"'));
  await page.getByRole('button', { name: 'Retry this step' }).click();
  await expect(page.getByRole('button', { name: 'Start workflow' })).toBeEnabled();
  expect(targetSnapshot(retryTarget)).toEqual(before);
});

test('needs-input execution survives a server restart in the same isolated workspace', async ({ page }) => {
  const restartRoot = '/private/tmp/goodteam-readiness-restart-e2e';
  const workspace = `${restartRoot}/workspace`;
  const target = `${restartRoot}/target`;
  fs.rmSync(restartRoot, { recursive: true, force: true });
  fs.mkdirSync(workspace, { recursive: true });
  fs.mkdirSync(target, { recursive: true });
  fs.writeFileSync(`${target}/package.json`, JSON.stringify({
    name: 'restart',
    scripts: { test: 'vitest run' },
  }, null, 2));
  const before = targetSnapshot(target);
  const port = await freePort();
  let owned;
  try {
    owned = await startOwnedServer(workspace, port);
    await page.addInitScript(() => localStorage.setItem('gt-language', 'en'));
    await page.goto(`${owned.url}/projects`);
    await page.getByLabel('Project directory').fill(target);
    await page.getByRole('button', { name: 'Load project' }).click();
    await expect(page).toHaveURL(new RegExp(`${owned.url}/projects/TARGET/home$`));
    useFakeWorker('TARGET', workspace);
    await page.getByRole('button', { name: 'Start understanding the codebase' }).click();
    await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();
    await page.reload();
    await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();

    await stopOwnedServer(owned.child);
    owned = await startOwnedServer(workspace, port);
    await page.goto(`${owned.url}/projects/TARGET/home`);
    await expect(page.getByText('Confirm how this project should be verified')).toBeVisible();
    await expect(page.getByText('npm run test').first()).toBeVisible();
    expect(targetSnapshot(target)).toEqual(before);
  } finally {
    await stopOwnedServer(owned?.child);
  }
});
