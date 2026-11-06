import { test, expect, importProject } from './fixtures.js';

test('compact team keeps workers visible and separates configuration availability from execution', async ({ page, fixture }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await importProject(page, fixture);
  const stateResponse = await page.request.get(`/api/state?project=${fixture.key}`);
  const state = await stateResponse.json();
  await page.goto(`/projects/${fixture.key}/team`);
  const main = page.locator('.team-page');
  const members = main.locator('.team-member');
  const workerCount = state.team.roles.reduce((sum, role) => sum + role.workers.length, 0);
  await expect(members).toHaveCount(workerCount);
  await expect(main.getByText('Configuration availability applies to new jobs; it does not indicate running work.')).toBeVisible();
  await expect(main.getByRole('link', { name: 'View execution in Workbench' })).toHaveAttribute('href', `/projects/${fixture.key}/work`);
  await expect(main.locator('.team-member[open], .team-role-settings[open]')).toHaveCount(0);
  const metrics = await main.evaluate(node => {
    const font = selector => getComputedStyle(node.querySelector(selector)).fontSize;
    const rect = selector => node.querySelector(selector).getBoundingClientRect();
    return {
      title: font('h1'), name: font('.team-member strong'), model: font('.team-role-workers__runtime'),
      status: font('.team-member .product-status-badge'), button: font('.product-button'),
      buttonHeight: rect('.product-button').height, searchHeight: rect('.collection-search').height,
      roleHeight: rect('.team-worker').height, memberHeight: rect('.team-member > summary').height,
      rosterWidth: rect('.team-responsibility-list').width,
      visibleWorkers: [...node.querySelectorAll('.team-member > summary')].filter(member => member.getBoundingClientRect().bottom <= innerHeight).length,
      viewport: [innerWidth, innerHeight], zoom: visualViewport.scale,
    };
  });
  expect(metrics).toMatchObject({ title: '16px', name: '14px', model: '13px', status: '12px', button: '13px', buttonHeight: 32, searchHeight: 32, viewport: [1920, 1080], zoom: 1 });
  expect(metrics.roleHeight).toBeLessThanOrEqual(80);
  expect(metrics.visibleWorkers).toBeGreaterThanOrEqual(6);
  await testInfo.attach('team-dimensions', { body: JSON.stringify(metrics, null, 2), contentType: 'application/json' });
  const capture = async name => testInfo.attach(name, { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
  await capture('team-light-1920x1080');
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('zh-CN');
  await capture('team-zh-1920x1080');
  await page.locator('.studio-preferences select').selectOption('en');
  await page.getByRole('button', { name: 'Appearance: Light', exact: true }).click();
  await capture('team-dark-1920x1080');
  await page.getByRole('button', { name: 'Appearance: Dark', exact: true }).click();

  const summary = members.first().locator('summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(members.first()).toHaveAttribute('open', '');
  expect(await summary.evaluate(node => getComputedStyle(node).outlineStyle)).toBe('solid');
  await members.first().getByRole('button', { name: /^Edit / }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(members.first().getByRole('button', { name: /^Edit / })).toBeFocused();
  await capture('team-configuration-1920x1080');
  await page.keyboard.press('Escape');
  await expect(summary).toBeFocused();
  await expect(members.first()).not.toHaveAttribute('open');
  await main.locator('.team-role-settings > summary').first().press('Enter');
  await expect(main.getByRole('button', { name: 'Methods', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  const search = main.getByRole('searchbox');
  await search.fill('no-such-team-worker');
  await expect(members).toHaveCount(0);
  await expect(main.getByText('No matches. Try another search or clear your filters.')).toBeVisible();
  await main.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(members).toHaveCount(workerCount);

  // Only boundary displays use response fixtures; saved configuration stays in the isolated backend.
  const responseState = structuredClone(state);
  const role = responseState.team.roles.find(item => item.workers.length);
  const worker = role.workers[0];
  worker.name = 'Worker-with-a-long-unbroken-name-for-the-configuration-list-and-narrow-screen';
  worker.model = 'model-with-a-long-version-and-deployment-identifier-for-wrapping';
  worker.lifecycle = 'inactive';
  worker.dispatchable = false;
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
  await expect(main.getByText(worker.name, { exact: true })).toBeVisible();
  await expect(main.getByText('Paused', { exact: true })).toBeVisible();
  await expect(main.getByText('Role disabled', { exact: true })).toBeVisible();
  await expect(main.getByText('No rostered workers. Add one to make this capability dispatchable.')).toBeVisible();
  await capture('team-boundaries-1920x1080');
  await main.getByRole('group', { name: 'Filter results' }).getByRole('button', { name: /^Needs setup/ }).click();
  await expect(main.getByRole('article')).toHaveCount(2);
  await main.getByRole('group', { name: 'Filter results' }).getByRole('button', { name: /^All/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('team-narrow-390x844');
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
  await expect(members).toHaveCount(0);
  await capture('team-error-1920x1080');
  mode = 'ready';
  await main.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(main.getByText(worker.name, { exact: true })).toBeVisible();
  await page.unroute(stateUrl);
  const expectedErrors = fixture.errors.filter(error => error.includes('503 (Service Unavailable)'));
  expect(expectedErrors.length).toBeGreaterThanOrEqual(2);
  fixture.errors.splice(0, fixture.errors.length, ...fixture.errors.filter(error => !expectedErrors.includes(error)));
});
