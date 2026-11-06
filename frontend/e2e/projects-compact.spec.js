import fs from 'node:fs';
import path from 'node:path';
import { test, expect, importProject } from './fixtures.js';
import { clickMutation, getJson, sendTask } from './business-loop-helpers.js';

test('compact projects retain import, browsing, exact attention targets and honest unavailable states', async ({ page, fixture }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement a searchable project list.');
  await clickMutation(page, page.getByRole('button', { name: 'Start this task', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/actions`);
  await expect(page.getByRole('heading', { name: 'Which project commands should the team use for verification?' })).toBeVisible();

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
      listWidth: rect('.projects-launcher-list').width, zoom: window.visualViewport.scale,
    };
  });
  expect(metrics).toMatchObject({ title: '16px', buttonFont: '13px', name: '14px', location: '12px', buttonHeight: 32, searchHeight: 32, zoom: 1 });
  expect(metrics.rowHeight).toBeLessThanOrEqual(76);
  expect(metrics.listWidth).toBeGreaterThan(1000);
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
