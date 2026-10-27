import { test, expect, importProject } from './fixtures.js';
import { prepareFirstTask, sendTask } from './business-loop-helpers.js';

const fenced = value => '```goodteam-ui\n' + JSON.stringify(value) + '\n```';
// Presentation fixtures, not model output or verified project evidence.
const answer = [
  '下面是交互效果示例，工时为假设值。可以点表头排序、筛选和复制数据，也可以切换图表指标。',
  '| 事项 | 预计工时 | 说明 |\n| --- | --- | --- |\n| 界面设计 | 10 | 统一对话排版与输入体验 |\n| 前端实现 | 20 | 接入表格、图表与预览 |\n| 交互验收 | 4 | 检查键盘、手机与深色显示 |',
  fenced({ type: 'chart', title: '阶段工作量（示例）', columns: ['阶段', '预计工时', '已用工时'], rows: [['界面设计', 10, 6], ['前端实现', 20, 12], ['交互验收', 4, 2]] }),
  fenced({ type: 'steps', title: '接下来怎么做', items: [
    { title: '对齐交互与排版', body: '保持左侧 Worker Status、中间 Chat、右侧 History / Deliver。正文 16px，控件 14px，辅助信息 13px。' },
    { title: '接入回答组件', body: '表格支持排序、筛选和复制。图表支持切换指标，所有数据也可以用表格查看。' },
    { title: '完成交互验收', body: '在隔离测试项目中检查桌面、手机、键盘和深色主题。' },
  ] }),
  fenced({ type: 'cards', title: '方案预览', items: [
    { title: '表格与图表', summary: '把对比数据变成可以操作的回答。', body: '适合工时对比、方案比较、阶段统计。示例数值不会作为真实项目进度。' },
    { title: '步骤与内容卡片', summary: '先看概览，需要时展开细节。', body: '适合实施计划、方案说明、结果摘要。展开只显示内容，不会开始执行任务。' },
  ] }),
].join('\n\n');

test('rich chat supports data interactions, keyboard disclosure, themes and phone reading', async ({ page, context, fixture }, testInfo) => {
  expect(answer.length).toBeLessThanOrEqual(4000);
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement a searchable project list.');
  await prepareFirstTask(page, fixture, []);
  let showFallback = false;
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.timeline = [
        { id: 'rich-user', actor: 'user', timestamp: '2026-10-26T13:00:00Z', content: '用示例展示一下表格、图表、步骤和内容卡片，细节可以展开。' },
        { id: 'rich-reply', actor: 'task_assistant', event: 'project_question_answered', timestamp: '2026-10-26T13:00:01Z', content: answer },
        ...(showFallback ? [{ id: 'rich-fallback', actor: 'task_assistant', event: 'project_question_answered', content: '```goodteam-ui\n{"type":"html","body":"<script>bad()</script>"}\n```' }] : []),
      ];
    }
    await route.fulfill({ response, json: data });
  });
  // Synthetic reply IDs replace the real history; discard its unrelated bookmark.
  await page.addInitScript(() => localStorage.removeItem('goodteam:conversation-reading:v1'));
  await page.reload();
  const reply = page.locator('[data-reading-id="rich-reply"]');
  const table = reply.locator('.chat-rich-table').first();
  const rows = table.locator('tbody tr');
  await expect(rows).toHaveCount(3);
  await table.getByRole('button', { name: '预计工时', exact: true }).click();
  await expect(rows.first()).toContainText('交互验收');
  await expect(table.getByRole('columnheader', { name: '预计工时' })).toHaveAttribute('aria-sort', 'ascending');
  await table.getByRole('searchbox', { name: 'Filter table' }).fill('前端');
  await expect(rows).toHaveCount(1);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await table.getByRole('button', { name: 'Copy data' }).click();
  await expect(table.getByRole('status')).toContainText('Copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('事项\t预计工时\t说明\n前端实现\t20\t接入表格、图表与预览');
  await table.getByRole('searchbox').fill('不存在');
  await expect(table).toContainText('No matching rows');
  await table.getByRole('searchbox').fill('');

  const chart = reply.locator('.chat-rich-chart');
  await chart.getByRole('button', { name: '前端实现 20', exact: true }).focus();
  await expect(chart.locator('output')).toHaveText('前端实现 · 预计工时: 20');
  await chart.getByRole('combobox', { name: 'Metric' }).selectOption('2');
  await expect(chart.locator('output')).toHaveText('前端实现 · 已用工时: 12');
  await chart.getByRole('button', { name: '界面设计 6', exact: true }).hover();
  await expect(chart.locator('output')).toHaveText('界面设计 · 已用工时: 6');
  await chart.locator('.chat-rich-chart__data > summary').focus();
  await page.keyboard.press('Enter');
  await expect(chart.getByRole('table')).toBeVisible();
  await chart.locator('.chat-rich-chart__data > summary').click();

  const step = reply.locator('.chat-rich-steps details').first();
  await expect(step.locator('.chat-rich-preview')).toBeHidden();
  await step.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(step.locator('.chat-rich-preview')).toContainText('左侧 Worker Status');
  await expect(step.locator('.chat-rich-preview')).toBeVisible();
  const card = reply.locator('.chat-rich-cards article').first();
  await card.locator('summary').click();
  await expect(card.locator('.chat-rich-preview')).toBeVisible();
  await expect(card.getByRole('button')).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Language' }).selectOption('zh-CN');
  await expect(table.getByRole('searchbox', { name: '筛选表格' })).toBeVisible();
  await page.locator('.team-manager-conversation').evaluate(node => { node.scrollTop = 0; });
  await page.screenshot({ path: testInfo.outputPath('rich-chat-desktop.png') });
  await reply.locator('.chat-rich-steps').scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('rich-chat-steps.png') });
  await reply.locator('.chat-rich-cards').scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('rich-chat-details.png') });
  await page.getByRole('button', { name: '外观: 浅色' }).click();
  await expect.poll(() => chart.evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgb(34, 34, 37)');
  await chart.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('rich-chat-dark.png') });
  await page.getByRole('button', { name: '外观: 深色' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await chart.scrollIntoViewIfNeeded();
  await expect(chart.getByRole('combobox', { name: '指标' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const bounds = await chart.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await table.getByRole('button', { name: '复制数据' }).evaluate(node => getComputedStyle(node).transitionDuration)).toBe('0s');
  await page.getByRole('textbox', { name: '消息', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath('rich-chat-mobile.png') });

  showFallback = true;
  await page.reload();
  const fallback = page.locator('[data-reading-id="rich-fallback"]');
  await expect(fallback.locator('code')).toContainText('<script>bad()</script>');
  await expect(fallback.locator('script, iframe')).toHaveCount(0);
});
