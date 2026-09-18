import { test, expect, importProject } from './fixtures.js';
import { clickMutation, decisions, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

async function say(page, fixture, text) {
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
  const reply = await clickMutation(page, page.getByRole('button', { name: 'Send', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/messages`);
  expect(reply.workflow_started).toBe(false);
  return reply;
}

test('one composer keeps pending questions, running questions and review changes separate', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Implement a searchable project list.');
    const staged = await say(page, fixture, 'Build a login form instead.');
    const pending = staged.session.proposed_task;
    expect(pending.pending_change).toBeTruthy();

    const answered = await say(page, fixture, 'Why replace rather than append?');
    expect(answered.session.proposed_task).toEqual(pending);
    expect((await snapshot(page, fixture, evidence)).state).toBeNull();
    await clickMutation(page, page.getByRole('button', { name: 'Keep current draft', exact: true }),
      `/api/projects/${fixture.key}/task-assistant/actions`);
    await prepareFirstTask(page, fixture, evidence);

    const running = await snapshot(page, fixture, evidence);
    await say(page, fixture, 'What is the team waiting for?');
    const afterQuestion = await snapshot(page, fixture, evidence);
    expect(afterQuestion.state).toEqual(running.state);
    expect(afterQuestion.task_assistant_session.proposed_task).toEqual(running.task_assistant_session.proposed_task);
    expect(decisions(afterQuestion)).toEqual(decisions(running));

    await page.getByRole('button', { name: 'Request changes', exact: true }).click();
    const dock = page.locator('.team-manager-dock');
    await expect(dock.getByRole('textbox', { name: 'What should change?' })).toBeVisible();
    await expect(page.locator('.team-attention textarea')).toHaveCount(0);
    await expect(dock.locator('textarea')).toHaveCount(1);
    const note = 'Keep the existing database schema.';
    await dock.getByRole('textbox', { name: 'What should change?' }).fill(note);
    await dock.getByRole('button', { name: 'Review Task Assistant interpretation' }).click();
    await expect(page.getByRole('button', { name: 'Confirm changes', exact: true })).toBeEnabled();
    const discussed = await workState(page, fixture.key);
    const originalIntent = discussed.team_workspace.attention_request.intent_commit;

    await dock.getByRole('button', { name: 'Message', exact: true }).click();
    await say(page, fixture, 'Explain the current change request.');
    const stillPending = await workState(page, fixture.key);
    expect(stillPending.team_workspace.attention_request.intent_commit).toEqual(originalIntent);
    expect(stillPending.state).toEqual(running.state);
    expect(decisions(stillPending)).toEqual(decisions(running));

    await dock.getByRole('button', { name: 'Request changes', exact: true }).click();
    await dock.getByRole('textbox', { name: 'What should change?' }).fill('Also cover empty results.');
    await dock.getByRole('button', { name: 'Review Task Assistant interpretation' }).click();
    await expect.poll(async () => (await workState(page, fixture.key)).team_workspace.attention_request.intent_commit.draft_text)
      .toBe(`${note}\n\nAlso cover empty results.`);
    await page.reload();
    await expect(page.getByRole('button', { name: 'Confirm changes', exact: true })).toBeEnabled();
    const restored = await snapshot(page, fixture, evidence);
    const restoredIntent = restored.team_workspace.attention_request.intent_commit;
    expect(restoredIntent.id).toBe(originalIntent.id);
    expect(restoredIntent.target).toEqual(originalIntent.target);
    expect(restoredIntent.status).toBe('ready_to_confirm');
    expect(restored.state).toEqual(running.state);
    expect(decisions(restored)).toEqual(decisions(running));
  } finally {
    await testInfo.attach('conversation-continuity-evidence', {
      body: JSON.stringify({ project: fixture.key, snapshots: evidence }, null, 2),
      contentType: 'application/json',
    });
  }
});
