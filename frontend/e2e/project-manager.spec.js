import { test, expect, importProject } from './fixtures.js';
import { Buffer } from 'node:buffer';
import { clickMutation, decisions, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

test('project import waits for the user and research uses ordinary task intake', async ({ page, fixture }) => {
  const automaticWork = [];
  page.on('request', request => {
    if (request.method() === 'POST' && /\/task-assistant\/messages$|\/readiness\/(start|retry)$/.test(new URL(request.url()).pathname)) {
      automaticWork.push(new URL(request.url()).pathname);
    }
  });
  const imported = await importProject(page, fixture);
  expect(imported.readiness.project_mode).toBe('unconfirmed');
  expect(imported.readiness.execution).toBeFalsy();
  const before = await workState(page, fixture.key);
  expect(before.project_assistant.timeline).toHaveLength(1);
  expect(before.project_assistant.proposed_task).toBeFalsy();
  expect(before.state).toBeNull();
  expect(automaticWork).toEqual([]);

  const text = 'Summarize this project’s architecture, key flows, and open work. Research only; do not modify code.';
  const proposal = await sendTask(page, fixture, text);
  const after = await workState(page, fixture.key);
  expect(after.project_assistant.proposed_task.id).toBe(proposal.id);
  expect(after.project_assistant.timeline.some(item => item.event?.startsWith('project_understanding')
    || item.event === 'project_inspection_started')).toBe(false);
  expect(after.state).toBeNull();
  expect(automaticWork).toEqual([`/api/projects/${fixture.key}/task-assistant/messages`]);
});

test('the workbench keeps worker detail next to its owner at desktop and phone widths', async ({ page, fixture }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await importProject(page, fixture);
  let showQuestionBoard = false;
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const json = await response.json();
    json.team_workspace.execution_actors = [
      { id: 'worker-one', state: 'waiting', role: 'implementation', task_id: 'TASK-1' },
      { id: 'worker-two', state: 'waiting', role: 'qa', task_id: 'TASK-2' },
    ];
    if (showQuestionBoard) {
      json.task_assistant_session = {
        ...(json.task_assistant_session || {}),
        question_board: {
          schema_version: 'project_question_board.v1',
          items: [{
            id: 'q-1',
            question: 'Keep question board aligned?',
            why_human: 'Verify reading-column alignment.',
            status: 'awaiting_answer',
            current: true,
            can_answer: false,
          }],
        },
      };
    }
    await route.fulfill({ response, json });
  });
  await page.reload();
  const manager = page.getByRole('region', { name: 'Task Assistant', exact: true });
  await expect(manager.locator('.conversation-author').first()).toHaveText('Task Assistant');
  await expect(page.locator('.project-question-board--empty')).toBeHidden();
  const headerContext = await page.locator('.work-header-rail > .work-current-context').boundingBox();
  const headerToolbar = await page.locator('.work-header-rail .studio-work-toolbar').boundingBox();
  expect(headerContext.x + headerContext.width).toBeLessThanOrEqual(headerToolbar.x + 1);
  const title = await manager.getByRole('heading', { name: 'Project conversation' }).boundingBox();
  const composer = manager.getByRole('textbox', { name: 'Message', exact: true });
  const before = await composer.boundingBox();
  expect((await manager.locator('.conversation-editor').boundingBox()).x).toBeCloseTo(title.x, 0);
  await expect(composer).toHaveCSS('font-size', '14px');
  const worker = page.locator('.team-worker-summary').first();
  await worker.click();
  await expect(worker.locator('xpath=following-sibling::*[1]')).toHaveText(/TASK-1/);
  expect(await composer.boundingBox()).toEqual(before);

  showQuestionBoard = true;
  await page.reload();
  const board = manager.locator('.project-question-board');
  await expect(board).toBeVisible();
  expect((await board.boundingBox()).x).toBeCloseTo((await manager.getByRole('heading', { name: 'Project conversation' }).boundingBox()).x, 0);

  await page.locator('.studio-sidebar-toggle').click();
  await expect(page.locator('.product-shell')).toHaveAttribute('data-sidebar', 'collapsed');
  expect((await page.locator('.studio-sidebar').boundingBox()).width).toBeCloseTo(48, 0);
  for (const label of ['Workbench', 'Reviews', 'Team', 'Knowledge base']) {
    await expect(page.getByRole('link', { name: label, exact: true })).toBeVisible();
  }
  await page.locator('.studio-sidebar-toggle').click();
  await expect(page.locator('.product-shell')).toHaveAttribute('data-sidebar', 'expanded');

  await page.locator('#team-panel-execution .team-panel-close').click();
  await expect(page.locator('#team-panel-execution')).toBeHidden();
  await expect(page.locator('.studio-work-toolbar__reopen')).toBeVisible();
  await page.locator('.studio-work-toolbar__reopen').click();
  await expect(page.locator('#team-panel-execution')).toBeVisible();

  const outcomesToggle = page.locator('.studio-work-toolbar button[aria-controls="team-panel-outcomes"]');
  await outcomesToggle.click();
  await expect(outcomesToggle).toHaveAttribute('data-active', 'true');
  await expect(page.locator('#team-panel-outcomes')).toBeVisible();
  expect((await page.locator('#team-panel-outcomes').boundingBox()).width).toBeGreaterThan(256);
  await outcomesToggle.click();
  await expect(page.locator('#team-panel-outcomes')).toBeHidden();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#team-tab-execution').click();
  await expect(composer).toHaveCSS('font-size', '16px');
  if (await worker.getAttribute('aria-expanded') !== 'true') await worker.click();
  await worker.press('Enter');
  await expect(worker).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('.team-pane-backdrop')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('.team-pane-backdrop').click({ position: { x: 16, y: 24 } });
  await expect(page.locator('#team-panel-execution')).toHaveAttribute('data-mobile-active', 'false');
  await expect(page.locator('.team-pane-backdrop')).toBeHidden();
});

test('project Manager keeps chatting across tasks and saves future work without assigning it to itself', async ({ page, fixture }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
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
  await page.getByRole('button', { name: 'Hide delivery panel', exact: true }).click();
  const dock = manager.locator('.team-manager-dock');
  const beforeDetails = await dock.boundingBox();
  expect(beforeDetails.width).toBe(832);
  expect(await page.locator('.studio-sidebar').evaluate(node => node.getBoundingClientRect().width)).toBe(256);
  expect(await manager.locator('textarea').evaluate(node => getComputedStyle(node).fontSize)).toBe('14px');
  expect(await page.locator('.team-pane--execution').evaluate(node => node.getBoundingClientRect().width)).toBe(256);
  const userBubble = manager.locator('.team-manager-event[data-actor="user"] .team-manager-event__bubble').first();
  const assistantBubble = manager.locator('.team-manager-event:not([data-actor="user"]):not(.team-manager-event--system) .team-manager-event__bubble').first();
  const chat = await manager.locator('.team-manager-timeline').boundingBox();
  const userMessage = await userBubble.boundingBox();
  await expect(assistantBubble.locator('.conversation-author')).toHaveText('Task Assistant');
  const assistantMessage = await assistantBubble.boundingBox();
  expect(userMessage.x + userMessage.width).toBeCloseTo(chat.x + chat.width, 0);
  expect(assistantMessage.x).toBeCloseTo(chat.x, 0);
  await userBubble.scrollIntoViewIfNeeded();
  await testInfo.attach('workbench-conversation-1920x1080', { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
  const worker = page.locator('.team-worker-summary').first();
  await worker.click();
  await expect(worker).toHaveAttribute('aria-expanded', 'true');
  const workerDetail = worker.locator('xpath=following-sibling::*[1]');
  await expect(workerDetail).toHaveAttribute('id', await worker.getAttribute('aria-controls'));
  await expect(workerDetail).toBeVisible();
  expect(await dock.boundingBox()).toEqual(beforeDetails);
  await testInfo.attach('workbench-worker-detail-1920x1080', { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
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
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toBeVisible();
  await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Prepare this draft', exact: true })).toBeDisabled();

  // Termination is exercised through the real API in the fixture-owned workspace.
  const stopped = await page.request.post('/api/terminate', { data: { project_key: fixture.key, expected_run_id: firstRun } });
  expect(stopped.ok(), await stopped.text()).toBe(true);
  await page.reload();
  await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Prepare this draft', exact: true })).toBeEnabled();
  await clickMutation(page, page.getByRole('button', { name: 'Prepare this draft', exact: true }), endpoint);
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Proposed task', exact: true })).toContainText(future);
  const selected = await snapshot(page, fixture, evidence);
  expect(selected.task_assistant_session.proposed_task.confirmed).toBe(false);
  const started = await clickMutation(page, page.getByRole('button', { name: 'Start this task', exact: true }), endpoint);
  expect(started.workflow_started).toBe(true);
  expect(started.run_id).not.toBe(firstRun);

  const composer = manager.getByRole('textbox', { name: 'Message', exact: true });
  await composer.fill('Explain the earlier task.');
  await manager.locator('input[type="file"]').setInputFiles({ name: 'history.md', mimeType: 'text/markdown', buffer: Buffer.from('Use the original iteration as a reference.') });
  await expect(manager.locator('.conversation-editor li[data-upload="ready"]')).toHaveCount(1);
  await composer.evaluate((node) => { node.dataset.continuity = 'same-project-composer'; });
  await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await page.getByRole('navigation', { name: 'Project tasks' }).getByRole('button', { name: /Implement a searchable project list/ }).click();
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/runs/${firstRun}$`));
  await expect(composer).toHaveAttribute('data-continuity', 'same-project-composer');
  await expect(composer).toHaveValue('Explain the earlier task.');
  await expect(manager.locator('.conversation-editor li[data-upload="ready"]')).toHaveCount(1);
  await expect(page.locator('.studio-sidebar #project-task-navigation')).toBeVisible();
  await manager.getByRole('button', { name: 'Configure runtime', exact: true }).click();
  const runtime = page.getByRole('dialog', { name: 'Task Assistant settings' });
  await expect(runtime).toBeVisible();
  await runtime.getByRole('button', { name: 'Close dialog', exact: true }).first().click();
  await expect(composer).toHaveAttribute('data-continuity', 'same-project-composer');
  await expect(composer).toHaveValue('Explain the earlier task.');
  await expect(manager.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Team', exact: true }).click();
  await expect(page.locator('.team-page')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/runs/${firstRun}$`));
  await expect(composer).toHaveValue('Explain the earlier task.');
  await expect(manager.locator('.conversation-editor li[data-upload="ready"]')).toHaveCount(1);
  const current = await snapshot(page, fixture, evidence);
  const reply = await clickMutation(page, manager.getByRole('button', { name: 'Send', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/messages`);
  expect(reply.session.timeline.at(-1).run_id).toBe(firstRun);
  const afterHistoryQuestion = await snapshot(page, fixture, evidence);
  expect(afterHistoryQuestion.state).toEqual(current.state);
  expect(decisions(afterHistoryQuestion)).toEqual(decisions(current));
  await testInfo.attach('project-manager-history', { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
  await page.getByRole('button', { name: 'Appearance: Light', exact: true }).click();
  await testInfo.attach('project-manager-dark', { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
  await page.getByRole('button', { name: 'Appearance: Dark', exact: true }).click();
  await testInfo.attach('project-manager-evidence', { body: JSON.stringify(evidence, null, 2), contentType: 'application/json' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#team-tab-execution').click();
  const narrowWorker = page.locator('.team-worker-summary').first();
  if (await narrowWorker.getAttribute('aria-expanded') !== 'true') await narrowWorker.click();
  await expect(narrowWorker.locator('xpath=following-sibling::*[1]')).toBeVisible();
  await narrowWorker.press('Enter');
  await expect(narrowWorker).toHaveAttribute('aria-expanded', 'false');
  await expect(composer).toBeVisible();
  await narrowWorker.click();
  await testInfo.attach('project-manager-narrow', { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
});
