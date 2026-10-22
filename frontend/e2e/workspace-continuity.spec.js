import { test, expect, importProject } from './fixtures.js';
import { sendTask, prepareFirstTask } from './business-loop-helpers.js';

test('unsent conversation survives page changes and reload without sending', async ({ page, fixture }) => {
  await importProject(page, fixture);
  const draft = 'Keep my draft while I check the team.\nDo not start work yet.';
  let messages = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/task-assistant/messages')) messages += 1;
  });
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(draft);
  await page.getByRole('link', { name: 'Team', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Task Assistant', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Workbench', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(draft);
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(draft);
  expect(messages).toBe(0);
});

test('task context opens explicitly on desktop and narrow screens', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  const disclosure = page.locator('details.work-context-disclosure');
  const summary = disclosure.locator('summary');
  await expect(summary).toBeVisible();
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(disclosure).toHaveAttribute('open', '');
  await page.getByRole('link', { name: 'Workbench', exact: true }).hover();
  await page.mouse.move(0, 0);
  await expect(disclosure).toHaveAttribute('open', '');
  await summary.click();
  await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await summary.click();
  await expect(disclosure).toHaveAttribute('open', '');
  await expect(disclosure.getByRole('heading', { name: 'Work', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true });
});

test('assistant settings save in place while the conversation and draft remain', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  await expect(page.getByRole('navigation', { name: 'Project tasks', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Continue this after configuration.');
  const url = page.url();
  await expect(page.locator('.team-manager-conversation .assistant-runtime')).toHaveCount(0);
  await expect(page.locator('.team-manager-conversation .project-assistant-activity')).toHaveCount(0);
  const trigger = page.locator('.team-pane--manager > header').getByRole('button', { name: 'Configure runtime', exact: true });
  await trigger.click();
  const panel = page.getByRole('dialog', { name: 'Task Assistant settings' });
  await expect(panel).toBeVisible();
  await panel.getByLabel('Agent', { exact: true }).selectOption('fake');
  await panel.getByRole('button', { name: 'Enter model ID', exact: true }).click();
  await panel.getByLabel('Model', { exact: true }).fill('isolated-panel-model');
  const saved = page.waitForResponse((response) => response.url().endsWith('/team/commands') && response.request().method() === 'POST');
  await panel.getByRole('button', { name: 'Save and check connection', exact: true }).click();
  expect((await saved).ok()).toBe(true);
  await expect(panel.getByRole('button', { name: 'Close dialog', exact: true }).first()).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath('runtime-panel.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('runtime-panel-mobile.png'), fullPage: true });
  await panel.getByRole('button', { name: 'Close dialog', exact: true }).first().click();
  await expect(panel).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(page.url()).toBe(url);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Continue this after configuration.');
  const response = await page.request.get(`/api/state?project=${fixture.key}`);
  const data = await response.json();
  expect(data.team.manager.runtime.model).toBe('isolated-panel-model');
  expect(data.active_run).toBeFalsy();
});


test('running workspace keeps the composer usable on short and narrow screens', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement a searchable project list.');
  await prepareFirstTask(page, fixture, []);
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await expect(page.getByRole('button', { name: 'Inspect details →', exact: true })).toBeVisible();
  await expect(page.locator('.team-workspace')).not.toHaveClass(/team-workspace--composing/);
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1280, height: 720 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const manager = page.locator('.team-pane--manager');
    await manager.scrollIntoViewIfNeeded();
    const input = page.getByRole('textbox', { name: 'Message', exact: true });
    await expect(input).toBeVisible();
    await input.fill('Keep this draft while inspecting progress.');
    const navigation = page.getByRole('navigation', { name: 'Project tasks', exact: true });
    await input.evaluate(node => { node.dataset.continuity = 'same-composer'; });
    if (viewport.width < 768) {
      await expect(navigation).toBeHidden();
      await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
      await expect(navigation).toBeVisible();
      await navigation.getByRole('button', { name: 'Close task list', exact: true }).click();
      await expect(navigation).toBeHidden();
      await expect(input).toBeFocused();
      // Keyboard dismissal follows the same return-to-composer path.
      await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
      await navigation.getByRole('button', { name: 'Close task list', exact: true }).focus();
      await page.keyboard.press('Escape');
      await expect(navigation).toBeHidden();
      await expect(input).toBeFocused();
    } else {
      await expect(navigation).toBeVisible();
      await manager.getByRole('button', { name: 'Manage tasks', exact: true }).click();
      await expect(navigation).toBeFocused();
      const execution = page.locator('.team-execution-disclosure');
      for (const open of [true, false]) {
        if (await execution.evaluate(node => node.open) !== open) {
          await execution.locator(':scope > summary').click();
        }
        await expect(execution).toHaveJSProperty('open', open);
        const guidance = page.locator('.team-change-context-help');
        for (const guidanceOpen of [false, true]) {
          if (await guidance.evaluate(node => node.open) !== guidanceOpen) {
            await guidance.locator(':scope > summary').click();
          }
          await manager.scrollIntoViewIfNeeded();
          const send = manager.getByRole('button', { name: 'Send', exact: true });
          // Visibility alone does not detect ancestor clipping. The whole button
          // must fit its dock and receive pointer input without scrolling the dock.
          await expect.poll(() => send.evaluate(button => {
            const box = button.getBoundingClientRect();
            const dock = button.closest('.team-manager-dock');
            const bounds = dock.getBoundingClientRect();
            return box.top >= bounds.top && box.bottom <= bounds.bottom
              && dock.scrollTop === 0
              && button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
          })).toBe(true);
          await send.click({ trial: true });
        }
      }
      await input.focus();
    }
    await expect(input).toHaveAttribute('data-continuity', 'same-composer');
    await expect(input).toHaveValue('Keep this draft while inspecting progress.');
    const conversationBox = await page.locator('.team-manager-conversation').boundingBox();
    expect(conversationBox.height).toBeGreaterThanOrEqual(90);
    const box = await input.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
    const sendBox = await manager.getByRole('button', { name: 'Send', exact: true }).boundingBox();
    expect(sendBox.y + sendBox.height).toBeLessThanOrEqual(viewport.height);
    expect(sendBox.x + sendBox.width).toBeLessThanOrEqual(viewport.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`running-${viewport.width}.png`) });
  }
});
