import { test, expect, importProject } from './fixtures.js';
import { approveGate, decisions, expectGate, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

async function submitFromBothTabs(tabs) {
  let arrivals = 0;
  let release;
  const bothSubmitted = new Promise((resolve) => { release = resolve; });
  for (const tab of tabs) {
    await tab.route('**/api/approve', async (route) => {
      arrivals += 1;
      if (arrivals === tabs.length) release();
      await bothSubmitted;
      await route.continue();
    });
  }
  const pending = tabs.map((tab) => tab.waitForResponse((response) => (
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/approve'
  )));
  await Promise.all(tabs.map((tab) => tab.getByRole('button', { name: 'Approve', exact: true }).click()));
  const responses = await Promise.all(pending);
  expect(responses.some((response) => response.status() === 200)).toBe(true);
  for (const response of responses) {
    expect([200, 400, 409]).toContain(response.status());
    const body = await response.json();
    if (response.status() === 200) expect(body.next_node).toBe('await_release_approval');
    else expect(body.message).toMatch(/busy|changed|actionable/i);
  }
  expect(responses[0].request().postDataJSON()).toEqual(responses[1].request().postDataJSON());
}

test('two tabs approving the same evidence dispatch one execution and preserve one decision', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  const tabs = [];
  const errors = [];
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Implement concurrent approval protection.');
    await prepareFirstTask(page, fixture, evidence);
    for (const gate of ['takeover', 'prd', 'rfc']) await approveGate(page, fixture, gate);
    const before = await snapshot(page, fixture, evidence);
    for (let index = 0; index < 2; index += 1) {
      const tab = await page.context().newPage();
      tabs.push(tab);
      tab.on('pageerror', (error) => errors.push(error.message));
      await tab.goto(`/projects/${fixture.key}/review/security`);
      await expect(tab.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled();
    }
    await submitFromBothTabs(tabs);
    await expectGate(page, fixture.key, 'release');
    const after = await snapshot(page, fixture, evidence);
    const added = after.worker_jobs.filter((job) => !before.worker_jobs.some((prior) => prior.id === job.id));
    expect(added.filter((job) => job.role === 'implementation')).toHaveLength(1);
    expect(added.every((job) => job.adapter === 'fake' && job.status === 'completed')).toBe(true);
    expect(decisions(after).filter((item) => item.gate === 'security')).toHaveLength(1);
    await Promise.all(tabs.map((tab) => tab.reload()));
    for (const tab of tabs) await expect(tab.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
    expect((await workState(page, fixture.key)).state).toEqual(after.state);
    expect(errors).toEqual([]);
  } finally {
    await Promise.all(tabs.map((tab) => tab.close()));
    await testInfo.attach('concurrent-approval-evidence', { body: JSON.stringify(evidence), contentType: 'application/json' });
  }
});
