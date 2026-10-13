import { test, expect, importProject } from './fixtures.js';

// The app/server/project import are live in the test-owned workspace. Goal
// dispatch receipts are intercepted so this browser contract never calls a model.
// Scheduler and durable command authority are tested with isolated backend fakes.
test('Goal confirmation and exact one-unit gate remain explicit on desktop and mobile', async ({ page, fixture }, testInfo) => {
  const commands = [];
  let goal = null;
  await importProject(page, fixture);
  await page.route(`**/api/state?project=${encodeURIComponent(fixture.key)}`, async route => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.task_assistant_session = { ...payload.task_assistant_session, goals: goal ? [goal] : [] };
    await route.fulfill({ response, json: payload });
  });
  await page.route(`**/api/projects/${encodeURIComponent(fixture.key)}/task-assistant/actions`, async route => {
    const command = route.request().postDataJSON();
    commands.push(command);
    if (command.action === 'create_goal') {
      goal = {
        id: 'browser-goal', revision: 4, status: 'awaiting_approval', objective: command.objective,
        acceptance_criteria: command.acceptance_criteria, allowed_scope: command.allowed_scope,
        max_work_units: command.max_work_units,
        work_units: [{ id: 'w1', run_id: 'browser-bound-run', task: 'Implement keyboard search', status: 'done',
          started_at: '2026-10-13T00:00:00Z', criterion_indices: [0], rationale: 'Keyboard coverage still needs verification',
          evidence_refs: ['tests/search.spec.js'], priority: 'p1', dependencies: [] }],
        gate: { id: 'browser-gate', revision: 4, status: 'pending', kind: 'budget', proposed_max_work_units: 2,
          criterion_indices: [0], evidence_refs: ['tests/search.spec.js'], rationale: 'One bounded verification unit remains' },
      };
    } else if (command.action === 'decide_goal_gate') {
      goal = { ...goal, revision: 5, status: 'active', max_work_units: 2, gate: { ...goal.gate, status: 'approved' } };
    } else if (command.action === 'stop_goal') {
      goal = { ...goal, revision: 6, status: command.status, stop_reason: 'User stopped continuation' };
    } else {
      throw new Error(`Unexpected Goal command: ${command.action}`);
    }
    await route.fulfill({ json: { goal, task_assistant_session: { goals: [goal] } } });
  });

  const panel = page.getByRole('region', { name: 'Bounded Goal', exact: true });
  await panel.getByRole('button', { name: 'Set a Goal', exact: true }).click();
  await panel.getByRole('textbox', { name: 'Objective', exact: true }).fill('Make search keyboard accessible');
  await panel.getByRole('button', { name: 'Discard Goal draft' }).click();
  await page.reload();
  await panel.getByRole('button', { name: 'Set a Goal', exact: true }).click();
  await expect(panel.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue('');
  expect(commands).toEqual([]);
  await panel.getByRole('textbox', { name: 'Objective', exact: true }).fill('Make search keyboard accessible');
  await panel.getByRole('textbox', { name: 'Acceptance criteria (one per line)', exact: true }).fill('Search works with keyboard');
  await panel.getByRole('textbox', { name: 'Allowed scope and constraints (one per line)', exact: true }).fill('Search UI only\nNo new dependencies');
  await expect(panel.getByRole('checkbox')).not.toBeChecked();
  await expect(panel.getByRole('button', { name: 'Confirm and start Goal' })).toBeDisabled();
  await expect(panel).toContainText('not a money or token cap');
  await panel.getByRole('checkbox').check();
  await panel.getByRole('button', { name: 'Confirm and start Goal' }).click();
  await expect(panel).toContainText('Work-unit limit reached');
  expect(commands).toHaveLength(1);
  expect(commands[0]).toMatchObject({ action: 'create_goal', objective: 'Make search keyboard accessible',
    acceptance_criteria: ['Search works with keyboard'], allowed_scope: ['Search UI only', 'No new dependencies'],
    max_work_units: 1, confirmed: true, billing_acknowledged: true, idempotency_key: expect.any(String) });
  await testInfo.attach('goal-budget-desktop', { body: await page.screenshot(), contentType: 'image/png' });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(panel).toContainText('Run: browser-bound-run');
  expect(commands).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach('goal-budget-mobile', { body: await page.screenshot(), contentType: 'image/png' });
  await panel.getByRole('button', { name: 'Approve one more unit' }).click();
  await expect(panel).toContainText('limit 2');
  expect(commands[1]).toEqual({ action: 'decide_goal_gate', goal_id: 'browser-goal', gate_id: 'browser-gate',
    expected_revision: 4, decision: 'approve', idempotency_key: expect.any(String) });
  await panel.getByRole('button', { name: 'Stop continuation', exact: true }).click();
  await expect(panel).toContainText('User stopped continuation');
  expect(commands[2]).toEqual({ action: 'stop_goal', goal_id: 'browser-goal', expected_revision: 5,
    status: 'stopped', idempotency_key: expect.any(String) });
  await expect(panel.getByRole('button', { name: 'Approve one more unit' })).toHaveCount(0);
  await testInfo.attach('goal-command-evidence', { body: JSON.stringify(commands, null, 2), contentType: 'application/json' });
});
