import { test, expect, importProject } from './fixtures.js';
import { sendTask, workState } from './business-loop-helpers.js';

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
  const outcomesBox = await page.locator('#team-panel-outcomes').boundingBox();
  expect(outcomesBox.width).toBeGreaterThan(256);
  await composer.fill('Check send button clearance');
  const sendButton = manager.getByRole('button', { name: 'Send', exact: true });
  const sendBox = await sendButton.boundingBox();
  expect(sendBox.x + sendBox.width).toBeLessThanOrEqual(outcomesBox.x);
  expect(await sendButton.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return Boolean(hit && node.contains(hit));
  })).toBe(true);
  await composer.fill('');
  await outcomesToggle.click();
  await expect(page.locator('#team-panel-outcomes')).toBeHidden();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#team-tab-execution').focus();
  await page.locator('#team-tab-execution').press('Enter');
  await expect(page.locator('#team-panel-execution')).toBeVisible();
  await expect(page.locator('#team-panel-execution')).toHaveAttribute('data-mobile-active', 'true');
  await page.keyboard.press('Escape');
  await expect(page.locator('#team-panel-execution')).toHaveAttribute('data-mobile-active', 'false');
  await expect(page.locator('.team-pane-backdrop')).toBeHidden();
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('team-tab-execution');

  await page.locator('#team-tab-execution').press('Enter');
  await expect(page.locator('#team-panel-execution')).toBeVisible();
  await page.locator('#team-panel-execution .team-panel-close').focus();
  await page.locator('#team-panel-execution .team-panel-close').press('Enter');
  await expect(page.locator('#team-panel-execution')).toHaveAttribute('data-mobile-active', 'false');
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('team-tab-execution');

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
