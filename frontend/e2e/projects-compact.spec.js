/**
 * [INPUT]: 使用独立项目夹具与真实项目 API。
 * [OUTPUT]: 验证项目目录、待处理事项与移除恢复流程。
 * [POS]: 项目目录的浏览器回归测试。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, importProject } from './fixtures.js';
import { getJson } from './business-loop-helpers.js';

test('removing a project persists across reload and reimport restores its history', async ({ page, fixture }) => {
  const { project } = await importProject(page, fixture);
  const api = `/api/projects/${fixture.key}`;
  const config = fs.readFileSync(project.config_path, 'utf8');
  const readme = fs.readFileSync(path.join(fixture.root, 'README.md'), 'utf8');
  const mode = await page.request.post(`${api}/mode`, { data: { mode: 'existing_project' } });
  expect(mode.ok(), await mode.text()).toBe(true);

  await page.getByRole('link', { name: 'Projects', exact: true }).click();
  const remove = page.getByRole('button', { name: `Remove ${fixture.name}`, exact: true });
  const dialog = page.getByRole('dialog', { name: 'Remove project' });
  await remove.click();
  await expect(dialog.getByText(new RegExp(fixture.name))).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(remove).toBeFocused();

  await remove.click();
  await dialog.getByRole('button', { name: 'Remove project' }).click();
  await expect(remove).toHaveCount(0);
  const sidebarProject = page.locator('.studio-project-list').getByText(fixture.name, { exact: true });
  await expect(sidebarProject).toHaveCount(0);
  expect((await page.request.get(`${api}/home`)).status()).toBe(404);
  expect(fs.readFileSync(project.config_path, 'utf8')).toBe(config);
  expect(fs.readFileSync(path.join(fixture.root, 'README.md'), 'utf8')).toBe(readme);

  await page.reload();
  const listed = await getJson(page, '/api/projects');
  expect(listed.projects.some(entry => entry.project.project_key === fixture.key)).toBe(false);
  await expect(remove).toHaveCount(0);
  await page.getByRole('button', { name: 'Add project', exact: true }).first().click();
  await page.getByLabel('Project directory').fill(fixture.root);
  const restoredResponse = page.waitForResponse(response => response.url().endsWith('/api/projects/import'));
  await page.getByRole('button', { name: 'Load project', exact: true }).click();
  const restored = await restoredResponse;
  expect(restored.status(), await restored.text()).toBe(200);
  expect(await restored.json()).toMatchObject({
    project: { project_key: fixture.key, config_path: project.config_path },
    readiness: { project_mode: 'existing_project' },
  });
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await expect(sidebarProject).toBeVisible();
});

test('compact projects retain import, browsing, exact attention targets and honest unavailable states', async ({ page, fixture }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await importProject(page, fixture);
  const projectApi = `/api/projects/${fixture.key}`;
  const mode = await page.request.post(`${projectApi}/mode`, { data: { mode: 'existing_project' } });
  expect(mode.ok(), await mode.text()).toBe(true);
  const ready = await page.request.post(`${projectApi}/readiness/start`, { data: { inputs: {} } });
  expect(ready.status(), await ready.text()).toBe(202);
  await expect.poll(async () => (await getJson(page, `${projectApi}/home`)).readiness.execution?.current_step_id)
    .toBe('command_verification');

  // Real imports into the fixture-owned workspace; no production projects or providers.
  for (const name of ['atlas-interface', 'release-notes', 'customer-portal-with-a-long-project-name-and-local-directory']) {
    const root = path.join(path.dirname(fixture.root), name);
    fs.cpSync(fixture.root, root, { recursive: true });
    const response = await page.request.post('/api/projects/import', { data: { root } });
    expect(response.status(), await response.text()).toBe(201);
  }
  const payload = await getJson(page, '/api/projects');
  const current = payload.projects.find(item => item.project.project_key === fixture.key);
  const item = current.attention.items[0];
  expect(item).toBeTruthy();

  await page.getByRole('link', { name: 'Projects', exact: true }).click();
  await page.reload();
  const main = page.locator('.projects-page');
  const search = main.getByRole('searchbox');
  const filters = main.getByRole('group', { name: 'Filter results' });
  const entries = main.locator('.project-launcher-entry');
  await expect(entries).toHaveCount(payload.projects.length);
  await expect(filters.getByRole('button', { name: `All ${payload.projects.length}`, exact: true })).toHaveAttribute('aria-pressed', 'true');
  const metrics = await main.evaluate(node => {
    const rect = selector => node.querySelector(selector).getBoundingClientRect();
    const font = selector => getComputedStyle(node.querySelector(selector)).fontSize;
    return {
      title: font('h1'), buttonFont: font('.product-button'), name: font('.project-launcher-row__copy strong'),
      location: font('.file-location summary'), buttonHeight: rect('.product-button').height,
      searchHeight: rect('.collection-search').height, rowHeight: rect('.project-launcher-entry').height,
      listRight: rect('.projects-launcher').right, listTop: rect('.projects-launcher').top,
      attentionLeft: rect('.project-attention').left, attentionTop: rect('.project-attention').top,
      zoom: window.visualViewport.scale,
    };
  });
  expect(metrics).toMatchObject({ title: '28px', buttonFont: '13px', name: '14px', location: '12px', buttonHeight: 32, searchHeight: 32, zoom: 1 });
  expect(metrics.rowHeight).toBeLessThanOrEqual(76);
  expect(metrics.attentionLeft).toBeGreaterThan(metrics.listRight);
  expect(metrics.attentionTop).toBe(metrics.listTop);
  await testInfo.attach('projects-dimensions', { body: JSON.stringify(metrics, null, 2), contentType: 'application/json' });

  const capture = async name => testInfo.attach(name, { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
  await capture('projects-light-1920x1080');
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('zh-CN');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await capture('projects-zh-1920x1080');
  await page.locator('.studio-preferences select').selectOption('en');
  await page.getByRole('button', { name: 'Appearance: Light', exact: true }).click();
  await capture('projects-dark-1920x1080');
  await page.getByRole('button', { name: 'Appearance: Dark', exact: true }).click();

  await search.fill(current.project.display_name);
  await expect(entries).toHaveCount(1);
  await entries.locator('.file-location summary').focus();
  await page.keyboard.press('Enter');
  await expect(entries.locator('.file-location code')).toHaveText(current.project.root);
  await page.keyboard.press('Escape');
  await expect(entries.locator('.file-location summary')).toBeFocused();
  await filters.getByRole('button', { name: /^Active run/ }).click();
  await expect(main.getByText('No matches. Try another search or clear your filters.')).toBeVisible();
  await main.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(entries).toHaveCount(payload.projects.length);

  const attention = main.locator('.project-attention__link').filter({ hasText: current.project.display_name }).first();
  const target = new URL(await attention.getAttribute('href'), page.url());
  expect(target.searchParams.get('attention_target')).toBe(item.target_id);
  expect(target.searchParams.get('attention')).toBe(item.id);
  await expect(attention.locator('.project-attention__copy > span').last()).not.toBeEmpty();
  await attention.click();
  await expect(page.locator('.project-attention-focus')).toBeVisible();
  await page.goBack();
  await expect(entries).toHaveCount(payload.projects.length);

  await main.getByRole('button', { name: 'Add project', exact: true }).click();
  await expect(page.getByLabel('Project directory')).toBeFocused();
  await capture('project-import-1920x1080');
  await page.keyboard.press('Escape');
  await expect(main.getByRole('button', { name: 'Add project', exact: true })).toBeFocused();

  await page.setViewportSize({ width: 390, height: 844 });
  await capture('projects-narrow-390x844');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const list = await main.locator('.projects-launcher').boundingBox();
  const attentionPanel = await main.locator('.project-attention').boundingBox();
  expect(attentionPanel.y).toBeGreaterThanOrEqual(list.y + list.height);
  expect(attentionPanel.width).toBe(list.width);
  const navigationToggle = page.locator('.studio-topbar .studio-sidebar-toggle');
  await navigationToggle.press('Enter');
  await page.getByRole('link', { name: 'Projects', exact: true }).press('Escape');
  await expect(navigationToggle).toHaveAttribute('aria-expanded', 'false');
  await expect(navigationToggle).toBeFocused();
  expect(await entries.evaluateAll(nodes => nodes.every(node => {
    const row = node.getBoundingClientRect();
    const action = node.querySelector('.project-launcher-row__action').getBoundingClientRect();
    return action.right <= row.right && action.left >= row.left;
  }))).toBe(true);
  expect(await main.getByRole('button', { name: 'Add project', exact: true }).evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await main.getByRole('button', { name: 'Add project', exact: true }).click();
  await expect(page.getByLabel('Project directory')).toBeVisible();
  await capture('project-import-narrow-390x844');
  await page.keyboard.press('Escape');

  // Boundary displays use response fixtures only; the real backend state above is unchanged.
  await page.setViewportSize({ width: 1920, height: 1080 });
  let response = { ...payload, projects: payload.projects.map(entry => ({ ...entry, attention: null })) };
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/projects', async route => {
    await gate;
    await route.fulfill({ json: response });
  });
  await main.getByRole('button', { name: 'Refresh attention', exact: true }).click();
  await expect(main.getByText('Refreshing. The previous attention snapshot is no longer current.')).toBeVisible();
  await expect(main.locator('.project-launcher-row__activity')).toHaveCount(0);
  release();
  await expect(main.getByText(/Attention details unavailable for:/)).toBeVisible();
  await expect(filters.getByRole('button', { name: 'Needs attention', exact: true })).toBeVisible();
  await capture('projects-unavailable-1920x1080');
  response = { projects: null };
  await main.getByRole('button', { name: 'Refresh attention', exact: true }).click();
  await expect(main.getByText('Refresh failed. Attention is unknown until a fresh snapshot is available.')).toBeVisible();
  await expect(entries).toHaveCount(payload.projects.length);
  await expect(main.locator('.collection-filter-count, .project-launcher-row__activity, .project-attention__counts')).toHaveCount(0);
  response = { projects: [] };
  await main.getByRole('button', { name: 'Refresh attention', exact: true }).click();
  await expect(main.getByRole('heading', { name: 'Add your first project' })).toBeVisible();
});
