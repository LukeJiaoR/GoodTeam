import { execFileSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { test, expect, importProject } from './fixtures.js';

// Prepare synthetic stopped records through the existing domain store in the
// test-owned workspace. The route, state/history APIs and disclosures are real;
// this read-only history smoke does not claim Goal execution/model acceptance.
function prepareStoppedHistory(fixture) {
  const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  execFileSync(process.env.GOODTEAM_E2E_PYTHON || path.join(repository, '.venv/bin/python'), [
    '-c', `
from pathlib import Path
import sys
from goodteam_os.goals import GoalStore
from goodteam_os.goal_continuation import goal_guard
workspace = Path(sys.argv[1])
with goal_guard(workspace, sys.argv[2]):
    store = GoalStore(workspace, sys.argv[2])
    for index in range(23):
        goal = store.create_goal(
            objective=f"History goal {index:02d}",
            acceptance_criteria=[f"Complete criterion {index:02d}"],
            allowed_scope=["Read-only synthetic history smoke"],
            max_work_units=1, confirmed=True,
            idempotency_key=f"history-create-{index}",
            authority_snapshot={"synthetic_history_fixture": True},
        )
        store.stop_goal(goal["id"], expected_revision=goal["revision"],
                        idempotency_key=f"history-stop-{index}")
`, path.join(process.env.GOODTEAM_E2E_ROOT, 'workspace'), fixture.key,
  ], { cwd: repository, env: { ...process.env, PYTHONPATH: path.join(repository, 'src') } });
}

test('Goal history uses canonical identity, real paginated HTTP reads and historical disclosures', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  prepareStoppedHistory(fixture);
  const before = await (await page.request.get(`/api/state?project=${fixture.key}`)).json();
  expect(before.task_assistant_session.goals).toHaveLength(23);
  expect(before.task_assistant_session.goals.every(goal => goal.status === 'stopped')).toBe(true);
  const mutations = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/')) mutations.push(request.url());
  });
  const history = page.getByRole('region', { name: 'Goal history', exact: true });
  for (const [route, width] of [
    [`/projects/${fixture.key.toLowerCase()}/work`, 1440],
    [`/?project=${fixture.key.toLowerCase()}`, 390],
  ]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(route);
    const latest = page.getByRole('article', { name: 'Latest Goal', exact: true });
    await expect(latest).toContainText('History goal 22');
    await expect(history.locator(':scope > details')).toHaveCount(0);
    const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/goal-history');
    await history.getByRole('button', { name: 'Load older goals' }).click();
    const first = await response;
    expect(first.ok(), await first.text()).toBe(true);
    expect(new URL(first.url()).searchParams.get('project')).toBe(fixture.key);
    expect((await first.json()).project_key).toBe(fixture.key);
    await expect(history.locator(':scope > details')).toHaveCount(10);
    const older = history.locator(':scope > details').first();
    const summary = older.locator(':scope > summary');
    await expect(summary).toHaveText('History goal 21 · Stopped');
    await summary.focus();
    await page.keyboard.press('Enter');
    await expect(older.getByRole('article', { name: 'Historical Goal', exact: true })).toBeVisible();
    await expect(history.getByRole('article', { name: 'Latest Goal', exact: true })).toHaveCount(0);
    await older.getByText('Complete goal and evidence record', { exact: true }).click();
    await expect(older.locator('.goal-history-record')).toContainText('Complete criterion 21');
    await expect(history.getByRole('button', { name: 'Stop continuation', exact: true })).toHaveCount(0);
    await history.getByRole('button', { name: 'Load older goals' }).click();
    await expect(history.locator(':scope > details')).toHaveCount(20);
    await history.getByRole('button', { name: 'Load older goals' }).click();
    await expect(history.locator(':scope > details')).toHaveCount(22);
    await expect(history.locator(':scope > details > summary').last()).toHaveText('History goal 00 · Stopped');
    await expect(history.getByRole('button', { name: 'Load older goals' })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await testInfo.attach(`goal-history-${width}`, { body: await page.screenshot(), contentType: 'image/png' });
    await page.reload();
    await expect(history.locator(':scope > details')).toHaveCount(0);
    await expect(history.getByRole('button', { name: 'Load older goals' })).toBeVisible();
  }
  const after = await (await page.request.get(`/api/state?project=${fixture.key}`)).json();
  expect(after.worker_jobs).toEqual(before.worker_jobs);
  expect(after.task_assistant_session.goals).toEqual(before.task_assistant_session.goals);
  expect(mutations).toEqual([]);
});
