import { test, expect, importProject } from './fixtures.js';

test('waiting reply and project inspection are readable without duplicated request text', async ({ page, fixture }, testInfo) => {
  await importProject(page, fixture);
  let waiting = true;
  const question = 'Help me understand this project.';
  await page.route('**/api/state?*', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.assistant_requests = waiting ? { ui_wait: { status: 'running', text: question, attempt: 1, updated_at: new Date(Date.now() - 31000).toISOString() } } : {};
      data.project_assistant.timeline = [{ id: 'ui_user', actor: 'user', content: question }, ...(!waiting ? [{
        id: 'ui_reply', actor: 'task_assistant', event: 'project_understanding_ready',
        content: '技术栈 / Stack: Node.js / JavaScript\n入口 / Entry points: apps/admin/main.tsx, apps/server/index.ts\n命令 / Commands: npm run test, npm run test, npm run build\n风险 / Risks: Repository scan reached the bounded entry limit; deeper indexing is recommended.',
      }] : [])];
    }
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  const timeline = page.locator('.team-manager-timeline');
  await expect(timeline.locator('.assistant-pending-reply')).toContainText('Responding');
  await expect(timeline).toContainText('No need to send the message again.');
  await expect(timeline.getByText(question, { exact: true })).toHaveCount(1);
  await expect(page.locator('.assistant-request')).toHaveCount(0);
  await timeline.locator('.assistant-pending-reply').scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('waiting.png') });
  waiting = false;
  await page.reload();
  await expect(timeline.locator('.assistant-pending-reply')).toHaveCount(0);
  await expect(timeline).toContainText('This project uses Node.js / JavaScript.');
  await expect(timeline).toContainText('Some files were not examined');
  const report = timeline.locator('.project-understanding-reply');
  const commands = report.locator('details').filter({ hasText: 'Development commands (2)' });
  await expect(commands).not.toHaveAttribute('open', '');
  await commands.locator('summary').click();
  await expect(commands.getByText('npm run test', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await report.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('inspection-mobile.png') });
});


test('factual answers and specific clarification appear without execution boilerplate', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.route('**/api/state?*', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.assistant_requests = {};
      data.project_assistant.timeline = [
        { id: 'fact', actor: 'task_assistant', event: 'project_question_answered', content: 'Yes, this project uses Git.' },
        { id: 'clarify', actor: 'task_assistant', event: 'intent_clarification_requested', content: 'Which branch do you mean?' },
      ];
    }
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  const timeline = page.locator('.team-manager-timeline');
  await expect(timeline.getByText('Yes, this project uses Git.', { exact: true })).toBeVisible();
  await expect(timeline.getByText('Which branch do you mean?', { exact: true })).toBeVisible();
  await expect(timeline).not.toContainText('I won’t start work until');
});


test('execution blocker lives in the task pane with collapsed diagnostics', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.team_workspace.attention_request = { id: 'blocked-ui', kind: 'blocker', reason: 'NEEDS HUMAN [rate_limit]: ui worker failed [rate_limit, 3 attempts]: Codex CLI exited with code 1.', actions: ['retry'], refs: [] };
    }
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  const task = page.locator('.team-pane--execution');
  await expect(page.getByRole('button', { name: 'Manage tasks', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  await expect(task.locator('.task-blocker')).toBeVisible();
  await expect(page.locator('.team-pane--manager .task-blocker')).toHaveCount(0);
  await expect(page.locator('.conversation-blocker-notice')).toContainText('rate limited');
  await expect(task.getByText(/NEEDS HUMAN/)).not.toBeVisible();
  await expect(task.getByRole('link', { name: 'Check execution settings' })).toHaveAttribute('href', `/projects/${fixture.key}/team`);
  await task.getByText('View failure details', { exact: true }).click();
  await expect(task.getByText(/NEEDS HUMAN/)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#team-tab-manager').click();
  await page.locator('.conversation-blocker-notice').getByRole('button', { name: 'View task' }).click();
  await expect(task.locator('.task-blocker')).toBeVisible();
  await expect(task.getByRole('button', { name: 'Retry failed stage', exact: true })).toBeDisabled();
  await expect(task).toContainText('Recovery identity is unavailable for this view.');
});

test('server receipt clears sent input while a reply is pending', async ({ page, fixture }) => {
  await importProject(page, fixture);
  let messageId;
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route(`**/api/projects/${fixture.key}/task-assistant/messages`, async route => {
    messageId = route.request().postDataJSON().message_id;
    const response = await route.fetch();
    await held;
    await route.fulfill({ response });
  });
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (messageId && new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.timeline = [{ id: messageId, actor: 'user', content: 'Tell me about this project.' }];
      data.project_assistant.assistant_requests = { [messageId]: { status: 'running', text: 'Tell me about this project.', updated_at: new Date().toISOString() } };
    }
    await route.fulfill({ response, json: data });
  });
  try {
    const input = page.getByRole('textbox', { name: 'Message', exact: true });
    await input.fill('Tell me about this project.');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(input).toHaveValue('');
    await expect(page.getByRole('button', { name: 'Awaiting reply', exact: true })).toBeDisabled();
    await input.fill('My next question');
    await expect(input).toHaveValue('My next question');
    await expect(page.locator('.team-manager-timeline')).not.toContainText('Sending…');
  } finally { release(); }
});

test('running execution replaces the stale blocker and prevents retry after reload', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.team_workspace.attention_request = { kind: 'blocker', reason: 'old failure', actions: ['retry'] };
      data.team_workspace.execution_actors = [{ id: 'implementation', role: 'implementation', state: 'working', current_attempt_id: 'current-job' }];
      data.worker_jobs = [{ id: 'current-job', role: 'implementation', status: 'running', updated_at: new Date().toISOString() }];
    }
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  const task = page.locator('.task-blocker');
  await expect(task.getByRole('heading', { name: 'Execution in progress' })).toBeVisible();
  await expect(task.getByRole('button', { name: 'Execution in progress' })).toBeDisabled();
  await expect(task.getByRole('button', { name: 'Retry failed stage' })).toHaveCount(0);
  await expect(page.locator('.conversation-blocker-notice')).toContainText('A Worker has started');
  await expect(task.getByText('old failure', { exact: true })).not.toBeVisible();
});

test('new-project discovery remains a conversation instead of a proposed task', async ({ page, fixture }) => {
  await importProject(page, fixture);
  const manager = page.getByRole('region', { name: 'Task Assistant', exact: true });
  for (const text of [
    'This is a new project. Help me shape and start it.',
    'This is a new project. Help me clarify the product idea first.',
  ]) {
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
    const response = page.waitForResponse((item) => item.request().method() === 'POST'
      && item.url().endsWith(`/api/projects/${fixture.key}/task-assistant/messages`));
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    const result = await response;
    expect(result.ok(), await result.text()).toBe(true);
    const data = await result.json();
    expect(data.workflow_started).toBe(false);
    expect(data.session.proposed_task).toBeNull();
    await expect(page.getByRole('region', { name: 'Proposed task', exact: true })).toHaveCount(0);
    await expect(manager).toContainText('what product do you want to make, and who is it for?');
  }
});

for (const complete of [true, false]) {
  test(`inventory coverage is ${complete ? 'complete' : 'partial'} without claiming semantic understanding`, async ({ page, fixture }) => {
    await importProject(page, fixture);
    await page.context().route('**/api/state?*', async route => {
      const response = await route.fetch();
      const data = await response.json();
      if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
        data.project_assistant.timeline = [{
          id: 'inventory-coverage', actor: 'task_assistant',
          event: complete ? 'project_understanding_ready' : 'project_understanding_incomplete',
          content: 'Stack: Python\nEntry points: src/deep/main.py',
          details: { inventory_scan: { complete, files_seen: 1522, directories_seen: 12, snapshot_omitted_files: complete ? 0 : 1 } },
        }];
      }
      await route.fulfill({ response, json: data });
    });
    await page.reload();
    const reply = page.locator('.project-understanding-reply');
    await expect(reply).toContainText(complete ? 'Directory enumeration complete: 1522 files, 12 directories' : 'Coverage incomplete: 1522 files, 12 directories');
    await expect(reply).toContainText('It does not establish code semantics');
    await expect(reply).not.toContainText('scan reached its file limit');
    if (!complete) await expect(page.locator('.team-manager-timeline')).toContainText('The initial inventory is incomplete.');
    const chinese = await page.context().newPage();
    chinese.on('pageerror', error => fixture.errors.push(error.message));
    await chinese.addInitScript(() => localStorage.setItem('gt-language', 'zh-CN'));
    await chinese.setViewportSize({ width: 390, height: 844 });
    await chinese.goto(`/projects/${fixture.key}/work`);
    const translated = chinese.locator('.project-understanding-reply');
    await expect(translated).toContainText(complete ? '目录枚举完成' : '覆盖不完整');
    await expect(translated).toContainText('尚未进行代码语义理解');
    await chinese.close();
  });
}

// Read-only response fixture: proves presentation, not Worker/Planner execution.
test('structured replies retain plan nesting, list markers and checklist alignment', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.route('**/api/state?*', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (new URL(route.request().url()).searchParams.get('project') === fixture.key) {
      data.project_assistant.assistant_requests = {};
      data.project_assistant.timeline = [{ id: 'structured_reply', actor: 'task_assistant', event: 'project_question_answered',
        content: '3. Implement\n   - Preserve input\n   - Verify output\n4. Review\n- [x] Parent\n  - [ ] Pending one\n  - [ ] Pending two\n  - [ ] Pending three\n7. Continue' }];
    }
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  const reply = page.locator('.team-manager-timeline .md-renderer').filter({ hasText: 'Preserve input' });
  const rootLists = reply.locator(':scope > .md-list');
  await expect(rootLists).toHaveCount(3);
  await expect(rootLists.nth(0)).toHaveAttribute('start', '3');
  await expect(rootLists.nth(2)).toHaveAttribute('start', '7');
  await expect(rootLists.nth(0)).toHaveCSS('list-style-type', 'decimal');
  await expect(rootLists.nth(1)).toHaveCSS('list-style-type', 'disc');
  await expect(rootLists.nth(0).locator(':scope > li')).toHaveCount(2);
  await expect(rootLists.nth(0).locator(':scope > li').first().locator('ul > li')).toHaveCount(2);
  const parent = rootLists.nth(1).locator(':scope > li').first();
  await expect(parent.locator(':scope > input')).toBeChecked();
  await expect(parent.locator('ul input:checked')).toHaveCount(0);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    const checkbox = await parent.locator(':scope > input').boundingBox();
    const label = await parent.locator(':scope > div > span').boundingBox();
    expect(checkbox).not.toBeNull();
    expect(label).not.toBeNull();
    expect(Math.abs(checkbox.y - label.y)).toBeLessThan(12);
  }
});
