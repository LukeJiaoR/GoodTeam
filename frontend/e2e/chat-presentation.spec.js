import { test, expect, importProject } from './fixtures.js';
import { prepareFirstTask, sendTask } from './business-loop-helpers.js';

// Isolated fake-Worker project; the reply text below is presentation fixture data.
test('chat keeps message details and change help accessible in a compact composer', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement a searchable project list.');
  await prepareFirstTask(page, fixture, []);
  const timestamp = '2026-10-26T13:00:00Z';
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.timeline = [
        { id: 'presentation-user', actor: 'user', timestamp, content: '项目列表的搜索应该怎么做？先说说方案，保持现有接口不变。' },
        { id: 'presentation-reply', actor: 'task_assistant', event: 'project_question_answered', timestamp,
          content: '可以先在现有项目列表上做本地筛选，保持接口和数据结构不变。\n\n### 交互方式\n\n- 输入项目名称时，列表即时更新。\n- 没有匹配结果时，显示搜索词和清除入口。\n- 清空搜索后，恢复完整列表。\n\n搜索框放在列表顶部，结果数量显示在旁边。这样可以直接开始搜索，也能清楚地知道当前看到了多少个项目。' },
      ];
    }
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  const reply = page.locator('[data-reading-id="presentation-reply"]');
  await expect(reply).toContainText('交互方式');
  const details = reply.locator('.conversation-message-details');
  await expect(details.locator('time')).toBeHidden();
  await details.locator('summary').click();
  await expect(details.locator('time')).toBeVisible();
  await expect(details.locator('time')).toHaveAttribute('datetime', timestamp);
  await details.locator('summary').click();

  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('先保持现有接口不变，补上空结果的交互。');
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
  const guidance = page.locator('.team-change-context-help');
  await guidance.locator('summary').click();
  await expect(guidance.locator('p')).toBeVisible();
  await guidance.locator('summary').click();
  await page.getByRole('combobox', { name: 'Language' }).selectOption('zh-CN');
  await page.locator('.team-manager-conversation').evaluate(node => { node.scrollTop = 0; });
  await page.screenshot({ path: testInfo.outputPath('chatgpt-style-desktop.png') });
  await page.getByRole('button', { name: '外观: 浅色' }).click();
  await expect.poll(() => page.locator('.team-actor-list > li').first().evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgb(24, 24, 27)');
  await page.screenshot({ path: testInfo.outputPath('chatgpt-style-dark.png') });
  await page.getByRole('button', { name: '外观: 深色' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const input = page.getByRole('textbox', { name: '消息', exact: true });
  await input.scrollIntoViewIfNeeded();
  await expect(input).toHaveValue('先保持现有接口不变，补上空结果的交互。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('chatgpt-style-mobile.png') });
});
