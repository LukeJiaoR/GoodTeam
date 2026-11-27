/**
 * [INPUT]: 隔离项目的 Team 快照与浏览器。
 * [OUTPUT]: 验证角色配置与 Worker 管理各自的内容。
 * [POS]: 覆盖 Team 的键盘操作和不同视口。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { test, expect, importProject } from './fixtures.js';

test('compact team shows role settings independently of worker management', async ({ page, fixture }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await importProject(page, fixture);
  const stateResponse = await page.request.get(`/api/state?project=${fixture.key}`);
  const state = await stateResponse.json();
  await page.goto(`/projects/${fixture.key}/team`);
  const main = page.locator('.team-page');
  const members = main.locator('.team-member');
  const roles = main.getByRole('article');
  const enabledRole = state.team.roles.find((role) => role.state === 'enabled');
  const configure = main.getByRole('button', { name: `Configure ${enabledRole.label}`, exact: true });
  await expect(roles).toHaveCount(state.team.roles.length);
  await expect(members).toHaveCount(0);
  await expect(main.getByText('Role readiness applies to new jobs.')).toBeVisible();
  await expect(main.getByRole('link', { name: 'View execution in Workbench' })).toHaveAttribute('href', `/projects/${fixture.key}/work`);
  await expect(main.locator('.team-role-settings[open]')).toHaveCount(0);
  const metrics = await main.evaluate(node => {
    const font = selector => getComputedStyle(node.querySelector(selector)).fontSize;
    const rect = selector => node.querySelector(selector).getBoundingClientRect();
    return {
      title: font('h1'), role: font('.team-role__identity h3'),
      status: font('.team-role-settings .product-status-badge'), button: font('.product-button'),
      buttonHeight: rect('.product-button').height, searchHeight: rect('.collection-search').height,
      roleHeight: rect('.team-role').height,
      rosterWidth: rect('.team-responsibility-list').width,
      visibleRoles: [...node.querySelectorAll('.team-role')].filter(role => role.getBoundingClientRect().bottom <= innerHeight).length,
      viewport: [innerWidth, innerHeight], zoom: visualViewport.scale,
    };
  });
  expect(metrics).toMatchObject({ title: '16px', role: '14px', status: '12px', button: '13px', buttonHeight: 32, searchHeight: 32, viewport: [1920, 1080], zoom: 1 });
  expect(metrics.roleHeight).toBeLessThanOrEqual(80);
  expect(metrics.visibleRoles).toBeGreaterThanOrEqual(6);
  await testInfo.attach('team-dimensions', { body: JSON.stringify(metrics, null, 2), contentType: 'application/json' });
  const capture = async name => testInfo.attach(name, { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
  await capture('team-light-1920x1080');
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('zh-CN');
  await capture('team-zh-1920x1080');
  await page.locator('.studio-preferences select').selectOption('en');
  await page.getByRole('button', { name: 'Appearance: Light', exact: true }).click();
  await capture('team-dark-1920x1080');
  await page.getByRole('button', { name: 'Appearance: Dark', exact: true }).click();

  await configure.press('Enter');
  const dialog = page.getByRole('dialog', { name: `Configure ${enabledRole.label}` });
  await expect(dialog.getByLabel('Agent', { exact: true })).toBeVisible();
  await expect(dialog).not.toContainText(/Worker/i);
  await capture('team-configuration-1920x1080');
  await page.keyboard.press('Escape');
  await expect(configure).toBeFocused();
  await main.locator('.team-role-settings > summary').first().press('Enter');
  await expect(main.getByRole('button', { name: 'Methods', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  const search = main.getByRole('searchbox');
  await search.fill('no-such-role');
  await expect(roles).toHaveCount(0);
  await expect(main.getByText('No matches. Try another search or clear your filters.')).toBeVisible();
  await main.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(roles).toHaveCount(state.team.roles.length);

  // Only boundary displays use response fixtures; saved configuration stays in the isolated backend.
  const responseState = structuredClone(state);
  const role = responseState.team.roles.find(item => item.workers.length);
  const worker = role.workers[0];
  worker.name = 'Worker-with-a-long-unbroken-name-for-the-configuration-list-and-narrow-screen';
  worker.model = 'model-with-a-long-version-and-deployment-identifier-for-wrapping';
  worker.lifecycle = 'inactive';
  worker.dispatchable = false;
  role.workers.push({ ...worker, id: `${role.role}-b`, name: 'Backup worker', lifecycle: 'active' });
  role.capability = 'degraded';
  const emptyRole = responseState.team.roles.find(item => item !== role);
  emptyRole.workers = [];
  emptyRole.capability = 'unstaffed';
  const disabledRole = responseState.team.roles.find(item => item !== role && item !== emptyRole);
  disabledRole.state = 'disabled';
  disabledRole.capability = 'disabled';
  const stateUrl = `**/api/state?project=${fixture.key}*`;
  let mode = 'loading';
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route(stateUrl, async route => {
    if (mode === 'loading') await gate;
    if (mode === 'error') return route.fulfill({ status: 503, json: { code: 'offline', message: 'Team snapshot unavailable' } });
    await route.fulfill({ json: responseState });
  });
  await page.reload();
  await expect(main.getByRole('status', { name: 'Loading' })).toBeVisible();
  await capture('team-loading-1920x1080');
  mode = 'ready';
  release();
  await expect(main.getByText(worker.name, { exact: true })).toHaveCount(0);
  await expect(main.getByText('Needs configuration', { exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: `Configure ${disabledRole.label}`, exact: true })).toBeDisabled();
  await capture('team-boundaries-1920x1080');
  await main.getByRole('group', { name: 'Filter results' }).getByRole('button', { name: /^Needs setup/ }).click();
  await expect(main.getByRole('article')).toHaveCount(2);
  await main.getByRole('group', { name: 'Filter results' }).getByRole('button', { name: /^All/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('team-narrow-390x844');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await main.getByRole('button', { name: 'Worker management', exact: true }).click();
  await expect(main.getByText(worker.name, { exact: true })).toBeVisible();
  await expect(main.getByText('Paused', { exact: true })).toBeVisible();
  await members.first().scrollIntoViewIfNeeded();
  await capture('team-workers-narrow-390x844');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await main.getByRole('button', { name: 'Add worker', exact: true }).evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await members.first().locator('summary').click();
  expect(await members.first().getByRole('button', { name: /^Pause |^Activate / }).evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await members.first().getByRole('button', { name: /^Edit / }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture('team-edit-narrow-390x844');
  await page.keyboard.press('Escape');

  await page.setViewportSize({ width: 1920, height: 1080 });
  await main.locator('h1').scrollIntoViewIfNeeded();
  mode = 'error';
  await expect(main.getByText('Showing the last saved snapshot. Current configuration availability could not be refreshed.')).toBeVisible();
  await capture('team-stale-1920x1080');
  await page.reload();
  await expect(main.getByRole('alert')).toBeVisible();
  await expect(roles).toHaveCount(0);
  await capture('team-error-1920x1080');
  mode = 'ready';
  await main.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(roles).toHaveCount(state.team.roles.length);
  await page.unroute(stateUrl);
  const expectedErrors = fixture.errors.filter(error => error.includes('503 (Service Unavailable)'));
  expect(expectedErrors.length).toBeGreaterThanOrEqual(2);
  fixture.errors.splice(0, fixture.errors.length, ...fixture.errors.filter(error => !expectedErrors.includes(error)));
});
