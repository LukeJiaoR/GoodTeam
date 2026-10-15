import { test, expect, importProject } from './fixtures.js';
import { approveGate, getJson, prepareFirstTask, sendTask, snapshot } from './business-loop-helpers.js';

// Real HTTP state and fake Workers in the isolated test workspace. Navigation,
// focus, browser history and refresh must never submit a workflow decision.
test('all-project attention links preserve exact run/gate identity through keyboard and history', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement title search without changing the database schema.');
  const runId = await prepareFirstTask(page, fixture, evidence);
  const before = await snapshot(page, fixture, evidence);
  const list = await getJson(page, '/api/projects');
  const entry = list.projects.find(project => project.project.project_key === fixture.key);
  expect(entry.attention_count).toBe(0);
  expect(entry.attention.counts).toEqual({ blocked: 0, approval: 1, needs_input: 0, total: 1 });
  const selected = entry.attention.items[0];
  expect(selected).toMatchObject({ source: 'workflow', run_id: runId, target_id: 'takeover' });

  const mutations = [];
  const recordMutation = request => { if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/')) mutations.push(request.url()); };
  page.on('request', recordMutation);
  await page.goto('/projects');
  const overview = page.getByRole('region', { name: 'Needs your attention' });
  const selectedLink = overview.getByRole('link').filter({ hasText: fixture.name });
  await expect(selectedLink).toHaveCount(1);
  const href = await selectedLink.getAttribute('href');
  expect(href).toContain(`/work/runs/${runId}?`);
  expect(new URL(href, 'http://local.test').searchParams.get('attention')).toBe(selected.id);
  await page.getByRole('searchbox').fill('no-such-project-filter');
  await expect(selectedLink).toBeVisible();
  await selectedLink.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`/work/runs/${runId}\\?`));
  const notice = page.getByRole('region', { name: 'Attention item', exact: true });
  await expect(notice).toHaveAttribute('data-status', 'current');
  await expect(page.locator('.team-attention[data-attention-id="approval:takeover"]')).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/\/projects$/);
  await page.goForward();
  await expect(notice).toHaveAttribute('data-status', 'current');
  await expect(page.locator('.team-attention[data-attention-id="approval:takeover"]')).toBeFocused();
  expect(mutations).toEqual([]);
  const after = await snapshot(page, fixture, evidence);
  expect(after.state).toEqual(before.state);
  expect(after.action_items).toEqual(before.action_items);
  expect(after.worker_jobs).toEqual(before.worker_jobs);
  await testInfo.attach('exact-attention-desktop', { body: await page.screenshot(), contentType: 'image/png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(notice).toHaveAttribute('data-status', 'current');
  await expect(page.locator('.team-attention[data-attention-id="approval:takeover"]')).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach('exact-attention-mobile', { body: await page.screenshot(), contentType: 'image/png' });

  page.off('request', recordMutation);
  await approveGate(page, fixture, 'takeover');
  mutations.length = 0;
  page.on('request', recordMutation);
  await page.goto(href);
  await expect(notice).toHaveAttribute('data-status', 'changed');
  await expect(notice).toBeFocused();
  await expect(page.locator('.team-attention[data-attention-id="approval:prd"]')).not.toBeFocused();
  expect(mutations).toEqual([]);
});
