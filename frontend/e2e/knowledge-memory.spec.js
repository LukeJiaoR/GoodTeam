import { execFileSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { test, expect, importProject } from './fixtures.js';

// Synthetic memory records in this test's isolated project; no live Worker/model.
test('knowledge filters audit records before limit and keeps original detail on desktop and mobile', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  execFileSync(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', ['-c', `
import sys
from pathlib import Path
from goodteam_os.memory import RoleMemoryStore
s = RoleMemoryStore(Path(sys.argv[1]))
s.append_memory_event('architect', horizon='mid-term', text='Historical 中文 original note', source='old/result.md')
s.append_memory_event('architect', horizon='short-term', text='Which migration should we choose? 原始问题', source='job/result.md', category='open_questions')
for i in range(55):
    s.append_memory_event('architect', horizon='short-term', text=f'Dispatched job {i} adapter=synthetic', source=f'job-{i}/provenance')
`, fixture.root], { env: { ...process.env, PYTHONPATH: path.resolve('../src') } });
  await page.goto(`/projects/${fixture.key}/knowledge?view=memory`);
  const list = page.locator('.knowledge-memory-records');
  await expect(list.locator('li')).toHaveCount(2);
  await expect(list.getByText('Open questions', { exact: true })).toBeVisible();
  await expect(list.getByText('Historical / unclassified', { exact: true })).toBeVisible();
  await expect(list).not.toContainText('Dispatched job');
  const row = list.getByRole('button').first();
  const style = await row.evaluate(el => { const s = getComputedStyle(el); return { padding: parseFloat(s.paddingTop), margin: parseFloat(s.marginBottom), font: parseFloat(s.fontSize) }; });
  expect(style.padding).toBeLessThanOrEqual(12);
  expect(style.margin).toBe(0);
  expect(style.font).toBeLessThanOrEqual(14);
  const roleHeight = await page.locator('.knowledge-memory > aside > button').first().evaluate(el => el.getBoundingClientRect().height);
  expect(roleHeight).toBeGreaterThanOrEqual(48);
  expect(roleHeight).toBeLessThanOrEqual(56);
  await row.click();
  const detail = page.getByRole('complementary', { name: 'Event inspector' });
  await expect(detail).toContainText('Which migration should we choose? 原始问题');
  await expect(detail).toContainText('job/result.md');
  expect(await row.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  await expect(detail).toContainText(/mem:[a-f0-9]+/);
  await page.screenshot({ path: testInfo.outputPath('knowledge-desktop.png'), fullPage: true });
  const toggle = page.getByRole('checkbox', { name: 'Include execution records' });
  await toggle.click();
  await expect(toggle).toBeChecked();
  await expect(list.locator('li')).toHaveCount(50);
  await expect(list).toContainText('Execution record');
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(list.locator('li')).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(toggle).toBeVisible();
  const input = page.getByRole('searchbox', { name: 'Search this role’s memory' });
  expect(await input.evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('knowledge-mobile.png'), fullPage: true });
});
