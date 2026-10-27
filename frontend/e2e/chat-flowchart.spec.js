import { test, expect, importProject } from './fixtures.js';

const diagram = {
  type: 'flowchart', title: '需求处理流程（示例）', nodes: [
    { id: 'start', label: '收到需求', kind: 'terminal' },
    { id: 'check', label: '信息完整？', kind: 'decision' },
    { id: 'ask', label: '补充关键信息' }, { id: 'plan', label: '整理实施方案' },
    { id: 'done', label: '等待用户确认', kind: 'terminal' },
  ], edges: [
    { from: 'start', to: 'check' }, { from: 'check', to: 'ask', label: '否' },
    { from: 'check', to: 'plan', label: '是' }, { from: 'ask', to: 'done' }, { from: 'plan', to: 'done' },
  ],
};

test('simple flowcharts render with fixed labels, readable branches and a text alternative', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  const mutations = [];
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.timeline = [{ id: 'flowchart-reply', actor: 'task_assistant', event: 'project_question_answered',
        content: '这是流程图显示示例。\n\n```goodteam-ui\n' + JSON.stringify(diagram) + '\n```' }];
    }
    await route.fulfill({ response, json: data });
  });
  await page.addInitScript(() => localStorage.removeItem('goodteam:conversation-reading:v1'));
  page.on('request', request => { if (request.method() === 'POST') mutations.push(request.url()); });
  await page.reload();
  const flow = page.locator('.chat-rich-flowchart');
  await expect(flow).toBeVisible();
  await expect(flow.locator('.chat-rich-flowchart__node')).toHaveCount(5);
  await expect(flow.locator('.chat-rich-flowchart__edge')).toHaveCount(5);
  await expect(flow.locator('[data-kind="decision"]')).toHaveText('信息完整？');
  for (const width of [1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await flow.locator('.chat-rich-flowchart__node').first().evaluate(node => getComputedStyle(node).fontSize)).toBe('14px');
  }
  await page.setViewportSize({ width: 1440, height: 1400 });
  await flow.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(flow.locator('li')).toContainText(['收到需求 → 信息完整？', '信息完整？ → 否 → 补充关键信息', '信息完整？ → 是 → 整理实施方案', '补充关键信息 → 等待用户确认', '整理实施方案 → 等待用户确认']);
  await flow.locator('summary').click();
  await page.locator('.team-manager-conversation').evaluate(node => { node.scrollTop = 0; });
  await page.getByRole('textbox', { name: 'Message', exact: true }).focus();
  await page.mouse.move(0, 0);
  await page.screenshot({ path: testInfo.outputPath('chat-flowchart-desktop.png') });
  await page.getByRole('button', { name: 'Appearance: Light' }).click();
  await expect.poll(() => flow.evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgb(34, 34, 37)');
  await page.screenshot({ path: testInfo.outputPath('chat-flowchart-dark.png') });
  await page.getByRole('button', { name: 'Appearance: Dark' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await flow.scrollIntoViewIfNeeded();
  const scroll = flow.locator('.chat-rich-flowchart__scroll');
  expect(await scroll.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
  await scroll.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => scroll.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('.team-manager-conversation').evaluate(node => { node.scrollTop = 0; });
  await page.getByRole('textbox', { name: 'Message', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('chat-flowchart-mobile.png') });
  expect(mutations).toEqual([]);
});

test('window resizing keeps workspace and page typography stable', async ({ page, fixture }) => {
  await importProject(page, fixture);
  for (const width of [1024, 1227, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const [selector, size] of [
      ['.studio-brand', '17px'], ['.work-current-context__identity strong', '24px'],
      ['.team-pane--manager .team-pane__header h2', '16px'],
      ['.team-manager-conversation .md-renderer p', '16px'], ['.conversation-editor textarea', '16px'],
    ]) expect(await page.locator(selector).first().evaluate(node => getComputedStyle(node).fontSize), `${selector} at ${width}px`).toBe(size);
  }
  // The chosen rem scale still respects browser text-size preferences.
  await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; });
  expect(await page.locator('.conversation-editor textarea').evaluate(node => getComputedStyle(node).fontSize)).toBe('20px');
  await page.getByRole('link', { name: 'Projects', exact: true }).click();
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
  for (const width of [1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.locator('.product-page-heading h1').evaluate(node => getComputedStyle(node).fontSize)).toBe('24px');
  }
});
