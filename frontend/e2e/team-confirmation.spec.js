import { expect, importProject, test } from './fixtures.js';
import { clickMutation, workState } from './business-loop-helpers.js';

test('Team proposals identify workers and show bound effects before confirmation', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  const actions = `/api/projects/${fixture.key}/task-assistant/actions`;
  const say = async (text) => {
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
    return clickMutation(page, page.getByRole('button', { name: 'Send', exact: true }),
      `/api/projects/${fixture.key}/task-assistant/messages`);
  };
  const original = (await workState(page, fixture.key)).team;
  const ambiguous = await say('Pause Builder A');
  expect(ambiguous.session.pending_team_decision).toBeNull();
  const answer = ambiguous.session.timeline.at(-1).content;
  for (const id of ['implementation-a', 'frontend-a', 'backend-a']) expect(answer).toContain(id);
  expect((await workState(page, fixture.key)).team).toEqual(original);

  const proposed = await say('Pause frontend-a');
  const pending = proposed.session.pending_team_decision;
  expect(pending.commands).toEqual([{ kind: 'inactivate_worker', worker_id: 'frontend-a' }]);
  expect(pending.preview.team_revision).toBe(original.revision);
  const card = page.getByRole('region', { name: 'Pending Team change; not applied', exact: true });
  await expect(card).toContainText('Frontend / Builder A');
  await expect(card.getByRole('cell', { name: 'Active', exact: true })).toBeVisible();
  await expect(card.getByRole('cell', { name: 'Inactive', exact: true })).toBeVisible();
  await expect(card).toContainText('frontend-a');
  expect((await workState(page, fixture.key)).team).toEqual(original);
  await page.reload();
  expect((await workState(page, fixture.key)).task_assistant_session.pending_team_decision).toEqual(pending);
  await expect(card.getByRole('button', { name: 'Confirm Team change' })).toBeEnabled();
  await testInfo.attach('team-proposal-desktop', { body: await page.screenshot(), contentType: 'image/png' });

  await clickMutation(page, card.getByRole('button', { name: 'Dismiss proposal' }), actions);
  const rejected = await page.request.post(actions, { data: { action: 'confirm_team_decision', proposal_id: pending.id } });
  expect(rejected.status()).toBe(409);
  expect((await workState(page, fixture.key)).team).toEqual(original);

  const second = (await say('Pause frontend-a')).session.pending_team_decision;
  await clickMutation(page, card.getByRole('button', { name: 'Confirm Team change' }), actions);
  const applied = (await workState(page, fixture.key)).team;
  expect(applied.revision).toBe(original.revision + 1);
  expect(applied.roles.find((role) => role.role === 'frontend').workers[0].lifecycle).toBe('inactive');
  expect(applied.roles.filter((role) => role.role !== 'frontend')).toEqual(original.roles.filter((role) => role.role !== 'frontend'));
  const replay = await page.request.post(actions, { data: { action: 'confirm_team_decision', proposal_id: second.id } });
  expect(replay.ok()).toBeTruthy();
  expect((await workState(page, fixture.key)).team).toEqual(applied);

  await say('Disable QA');
  await expect(card).toContainText('QA / QA A');
  await expect(card.getByRole('cell', { name: 'Enabled', exact: true })).toBeVisible();
  await expect(card.getByRole('cell', { name: 'Disabled', exact: true })).toBeVisible();
  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 844 });
    await card.getByRole('button', { name: 'Confirm Team change' }).scrollIntoViewIfNeeded();
    await expect(card.getByRole('button', { name: 'Confirm Team change' })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await testInfo.attach(`team-proposal-${width}`, { body: await card.screenshot(), contentType: 'image/png' });
  }
  const old = (await workState(page, fixture.key)).task_assistant_session.pending_team_decision;
  const change = await page.request.post(`/api/projects/${fixture.key}/team/commands`, {
    data: { commands: [{ kind: 'rename_worker', worker_id: 'qa-a', name: 'QA changed elsewhere' }] },
  });
  expect(change.ok()).toBeTruthy();
  await page.reload();
  await expect(card).toContainText('Team proposal failed or changed. Request a new proposal.');
  await expect(page.getByRole('region', { name: 'Current task and next step' })).toContainText('Team proposal failed or changed. Request a new proposal.');
  await expect(card.getByRole('button', { name: 'Confirm Team change' })).toHaveCount(0);
  const stale = await page.request.post(actions, { data: { action: 'confirm_team_decision', proposal_id: old.id } });
  expect(stale.status()).toBe(409);
  expect((await workState(page, fixture.key)).team.roles.find((role) => role.role === 'qa').state).toBe('enabled');
  await page.reload();
  await clickMutation(page, card.getByRole('button', { name: 'Dismiss proposal' }), actions);
  const events = await page.request.get(`/api/projects/${fixture.key}/team/events`);
  const conversationEvents = (await events.json()).events.filter((event) => event.surface === 'manager_conversation');
  expect(conversationEvents).toHaveLength(1);
  expect(conversationEvents[0]).toMatchObject({ actor: 'user', target: { id: 'frontend-a' }, cause: 'user_command' });
  expect((await workState(page, fixture.key)).state).toBeNull();
  await testInfo.attach('team-confirmation-evidence', {
    body: JSON.stringify({ original, pending, applied, staleProposal: old, conversationEvents }, null, 2),
    contentType: 'application/json',
  });
});
