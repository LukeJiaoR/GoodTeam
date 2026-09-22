import { test, expect, importProject } from './fixtures.js';
import { clickMutation, decisions, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

test('project Manager keeps chatting across tasks and saves future work without assigning it to itself', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  await importProject(page, fixture);
  const endpoint = `/api/projects/${fixture.key}/task-assistant/actions`;
  expect((await workState(page, fixture.key)).team_workspace.mission).toBeNull();
  await sendTask(page, fixture, 'Implement a searchable project list.');
  const firstRun = await prepareFirstTask(page, fixture, evidence);
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  const before = await snapshot(page, fixture, evidence);
  const manager = page.getByRole('region', { name: 'Task Assistant', exact: true });
  await expect(manager.locator('.product-status-badge').first()).toHaveText('Available');
  await manager.getByRole('button', { name: 'New task', exact: true }).click();
  const future = 'Add keyboard navigation.\n\nKeep the existing schema.';
  await manager.getByRole('textbox', { name: 'New task', exact: true }).fill(future);
  const saved = await clickMutation(page, manager.getByRole('button', { name: 'Save task', exact: true }), endpoint);
  expect(saved.session.queued_task_proposals[0].exact_request).toBe(future);
  const afterSave = await snapshot(page, fixture, evidence);
  expect(afterSave.state).toEqual(before.state);
  expect(afterSave.task_assistant_session.proposed_task).toEqual(before.task_assistant_session.proposed_task);
  expect(decisions(afterSave)).toEqual(decisions(before));
  expect(afterSave.team_workspace.execution_actors.every((actor) => actor.role !== 'orchestrator')).toBe(true);
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toHaveCount(0);
  await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Prepare this draft', exact: true })).toBeDisabled();

  // Termination is exercised through the real API in the fixture-owned workspace.
  const stopped = await page.request.post('/api/terminate', { data: { project_key: fixture.key } });
  expect(stopped.ok(), await stopped.text()).toBe(true);
  await page.reload();
  await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Prepare this draft', exact: true })).toBeEnabled();
  await clickMutation(page, page.getByRole('button', { name: 'Prepare this draft', exact: true }), endpoint);
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Proposed task', exact: true })).toContainText(future);
  const selected = await snapshot(page, fixture, evidence);
  expect(selected.task_assistant_session.proposed_task.confirmed).toBe(false);
  const started = await clickMutation(page, page.getByRole('button', { name: 'Start this task', exact: true }), endpoint);
  expect(started.workflow_started).toBe(true);
  expect(started.run_id).not.toBe(firstRun);

  const composer = manager.getByRole('textbox', { name: 'Message', exact: true });
  await composer.fill('Explain the earlier task.');
  await composer.evaluate((node) => { node.dataset.continuity = 'same-project-composer'; });
  await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await page.getByRole('navigation', { name: 'Project tasks' }).getByRole('button', { name: /Implement a searchable project list/ }).click();
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/runs/${firstRun}$`));
  await expect(composer).toHaveAttribute('data-continuity', 'same-project-composer');
  await expect(composer).toHaveValue('Explain the earlier task.');
  await manager.getByRole('button', { name: 'Configure runtime', exact: true }).click();
  const runtime = page.getByRole('dialog', { name: 'Task Assistant settings' });
  await expect(runtime).toBeVisible();
  await runtime.getByRole('button', { name: 'Close dialog', exact: true }).first().click();
  await expect(composer).toHaveAttribute('data-continuity', 'same-project-composer');
  await expect(composer).toHaveValue('Explain the earlier task.');
  await expect(manager.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
  const current = await snapshot(page, fixture, evidence);
  const reply = await clickMutation(page, manager.getByRole('button', { name: 'Send', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/messages`);
  expect(reply.session.timeline.at(-1).run_id).toBe(firstRun);
  const afterHistoryQuestion = await snapshot(page, fixture, evidence);
  expect(afterHistoryQuestion.state).toEqual(current.state);
  expect(decisions(afterHistoryQuestion)).toEqual(decisions(current));
  await testInfo.attach('project-manager-history', { body: await page.screenshot(), contentType: 'image/png' });
  await testInfo.attach('project-manager-evidence', { body: JSON.stringify(evidence, null, 2), contentType: 'application/json' });
});
