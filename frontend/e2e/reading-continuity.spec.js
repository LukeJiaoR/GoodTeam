import fs from 'node:fs';
import path from 'node:path';
import { test, expect, importProject } from './fixtures.js';
import { decisions, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

const storageKey = 'goodteam:conversation-reading:v1';
const conversation = page => page.locator('.team-manager-conversation');
const progress = page => page.getByRole('region', { name: 'Reading progress', exact: true });
const latestButton = page => page.getByRole('button', { name: 'Return to latest', exact: true });

async function askThroughApi(page, fixture, id, long = false) {
  const response = await page.request.post(`/api/projects/${fixture.key}/task-assistant/messages`, {
    data: { message_id: id, text: `What is the current task status? ${id}. ${long ? 'Please explain the current project state and the pending review. '.repeat(12) : ''}` },
  });
  expect(response.ok(), await response.text()).toBe(true);
  expect((await response.json()).workflow_started).toBe(false);
  await expect.poll(async () => {
    const data = await workState(page, fixture.key);
    return data.project_assistant.assistant_requests?.[id]?.status;
  }).toBe('succeeded');
}
async function fillHistory(page, fixture) {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement title search without changing the database schema.');
  await prepareFirstTask(page, fixture, []);
  for (let i = 0; i < 7; i++) await askThroughApi(page, fixture, `reading-history-${i}`, true);
  await page.reload();
  await expect(page.locator('[data-reading-id="reply:reading-history-6"]')).toHaveCount(1);
  await page.evaluate(() => document.fonts.ready);
  await expect.poll(() => conversation(page).evaluate(node => node.scrollHeight - node.clientHeight)).toBeGreaterThan(1000);
  if (await latestButton(page).count()) await latestButton(page).click();
}
async function anchor(page) {
  return conversation(page).evaluate(node => {
    const bounds = node.getBoundingClientRect();
    const row = [...node.querySelectorAll('[data-reading-id]')].find(item => item.getBoundingClientRect().bottom > bounds.top);
    return row ? { id: row.dataset.readingId, offset: Math.round(row.getBoundingClientRect().top - bounds.top) } : null;
  });
}
async function readHistory(page) {
  await conversation(page).evaluate(node => { node.scrollTop = Math.floor(node.scrollHeight * .3); });
  await expect(progress(page)).toBeVisible();
  return anchor(page);
}
async function checkpoint(page, key) {
  return page.evaluate(({ storageKey, key }) => JSON.parse(localStorage.getItem(storageKey) || '[]')
    .find(item => JSON.parse(item.scope)[0] === key), { storageKey, key });
}
async function expectAnchor(page, expected) {
  await expect.poll(async () => (await anchor(page))?.id).toBe(expected.id);
  expect(Math.abs((await anchor(page)).offset - expected.offset)).toBeLessThanOrEqual(3);
}

// Real API, temporary workspace, fake Workers. Reading/navigation never grants
// approval or dispatches work, and no business response is replaced by a fixture.
test('reading anchor survives repeated reload and project A-B-A while new work stays unread', async ({ page, fixture }, testInfo) => {
  await fillHistory(page, fixture);
  const before = await snapshot(page, fixture, []);
  const original = await readHistory(page);
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  await input.fill('My unfinished thought');
  const saved = await checkpoint(page, fixture.key);
  expect(saved.following).toBe(false);
  expect(saved.anchor.id).toBe(original.id);
  await page.reload();
  await expectAnchor(page, original);
  await expect(input).toHaveValue('My unfinished thought');
  await page.reload();
  await expectAnchor(page, original);

  const other = { root: path.join(fixture.root, 'other-project'), name: 'Other reading project' };
  fs.mkdirSync(other.root, { recursive: true });
  fs.writeFileSync(path.join(other.root, 'package.json'), JSON.stringify({ name: 'other-reading-project', scripts: { test: 'node --test' } }));
  await importProject(page, other);
  await expect(progress(page)).toHaveCount(0);
  await askThroughApi(page, fixture, 'away-result');
  await page.goto(`/projects/${fixture.key}/work`);
  await expectAnchor(page, original);
  await expect(progress(page)).toContainText('2 unread updates');
  await expect(progress(page)).toContainText('conversation updates');
  const sources = progress(page).locator('.project-conversation-follow__events button');
  await expect(sources).toHaveCount(2);
  await sources.last().click();
  await expect(page.locator('[data-reading-id="reply:away-result"]')).toBeFocused();
  await expect(progress(page)).toContainText('2 unread updates');
  expect((await checkpoint(page, fixture.key)).anchor).toEqual(saved.anchor);
  await expect(input).toHaveValue('My unfinished thought');
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/projects/${other.key}/work$`));
  await expect(progress(page)).toHaveCount(0);
  await page.goForward();
  await expectAnchor(page, original);
  await expect(progress(page)).toContainText('2 unread updates');

  const mutations = [];
  page.on('request', request => { if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/')) mutations.push(request.url()); });
  await page.getByRole('button', { name: 'View pending items', exact: true }).click();
  await expect(page.locator('.project-conversation-attention')).toBeFocused();
  await expect(progress(page)).toContainText('2 unread updates');
  expect((await checkpoint(page, fixture.key)).seenId).toBe(saved.seenId);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect((await checkpoint(page, fixture.key)).anchor).toEqual(saved.anchor);
  await page.reload();
  await expectAnchor(page, original); // inspecting pending does not replace the reading bookmark
  await page.getByRole('button', { name: 'Read new updates', exact: true }).click();
  await expect(progress(page)).toBeVisible(); // inspecting near bottom must not reenable follow
  await latestButton(page).click();
  await expect(progress(page)).toHaveCount(0);
  await page.reload();
  await expect(progress(page)).toHaveCount(0);
  expect(mutations).toEqual([]);
  const after = await snapshot(page, fixture, []);
  expect(after.state).toEqual(before.state);
  expect(decisions(after)).toEqual(decisions(before));
  expect(after.worker_jobs).toEqual(before.worker_jobs);
  await testInfo.attach('reading-continuity-desktop', { body: await page.screenshot(), contentType: 'image/png' });
});

test('an away latest frontier and hidden in-flight arrival remain unread until explicit reading', async ({ page, fixture }) => {
  await fillHistory(page, fixture);
  const old = await checkpoint(page, fixture.key);
  expect(old.following).toBe(true);
  await page.goto('/projects');
  await askThroughApi(page, fixture, 'away-from-latest');
  await page.goto(`/projects/${fixture.key}/work`);
  await expect(progress(page)).toContainText('2 unread updates');
  expect((await checkpoint(page, fixture.key)).seenId).toBe(old.seenId);
  await latestButton(page).click();
  const read = await checkpoint(page, fixture.key);

  // Hold a real state response across a visibility transition. This simulates
  // the browser's in-flight poll boundary without mocking business data.
  let release;
  let entered;
  const waiting = new Promise(resolve => { entered = resolve; });
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (data.project_assistant?.timeline?.some(item => item.id === 'reply:hidden-inflight')) {
      entered();
      await held;
    }
    await route.fulfill({ response });
  });
  await askThroughApi(page, fixture, 'hidden-inflight');
  // Polling is real; wait for one response and hide before delivering it.
  await waiting;
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  release();
  await expect(progress(page)).toContainText('2 unread updates');
  expect((await checkpoint(page, fixture.key)).seenId).toBe(read.seenId);
  await page.unroute('**/api/state?*');
  await page.evaluate(() => {
    delete document.visibilityState;
    delete document.hidden;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(progress(page)).toContainText('2 unread updates');
  await page.reload();
  await expect(progress(page)).toContainText('2 unread updates');
  await latestButton(page).click();
  await expect(progress(page)).toHaveCount(0);

  // An unchanged real poll arriving after return cannot consume the away
  // boundary before the next delayed update arrives.
  const beforeDelayed = await checkpoint(page, fixture.key);
  let releaseUnchanged;
  let enteredUnchanged;
  const pendingUnchanged = new Promise(resolve => { enteredUnchanged = resolve; });
  const heldUnchanged = new Promise(resolve => { releaseUnchanged = resolve; });
  let holdNext = true;
  await page.route('**/api/state?*', async route => {
    const response = await route.fetch();
    if (holdNext) {
      holdNext = false;
      enteredUnchanged();
      await heldUnchanged;
    }
    await route.fulfill({ response });
  });
  await pendingUnchanged;
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    delete document.visibilityState;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const unchangedDelivered = page.waitForResponse(response => new URL(response.url()).pathname === '/api/state');
  releaseUnchanged();
  await unchangedDelivered;
  await page.unroute('**/api/state?*');
  await askThroughApi(page, fixture, 'delayed-after-unchanged');
  await expect(progress(page)).toContainText('2 unread updates');
  expect((await checkpoint(page, fixture.key)).seenId).toBe(beforeDelayed.seenId);
});

test('missing anchors, corrupted storage and unavailable storage recover without moving drafts or approvals', async ({ page, fixture }, testInfo) => {
  await fillHistory(page, fixture);
  const before = await snapshot(page, fixture, []);
  const saved = await checkpoint(page, fixture.key);
  await page.evaluate(({ storageKey, saved }) => localStorage.setItem(storageKey, JSON.stringify([
    { ...saved, following: false, anchor: { id: 'removed-event', offset: -18 }, seenId: 'removed-event' },
  ])), { storageKey, saved });
  await page.reload();
  await expect(progress(page)).toContainText('no longer in this history');
  expect(await conversation(page).evaluate(node => node.scrollTop)).toBe(0);
  await expect(page.getByRole('button', { name: 'Read new updates', exact: true })).toHaveCount(0);
  await latestButton(page).click();
  await expect(progress(page)).toHaveCount(0);

  for (const raw of ['{tampered', JSON.stringify([{ ...saved, savedAt: 1 }]), JSON.stringify([{ ...saved, anchor: { id: 'invalid', offset: 1e99 } }])]) {
    await page.evaluate(({ storageKey, raw }) => localStorage.setItem(storageKey, raw), { storageKey, raw });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Task Assistant', exact: true })).toBeVisible();
    await expect(progress(page)).toHaveCount(0);
  }
  await page.evaluate(storageKey => {
    const get = Storage.prototype.getItem;
    const set = Storage.prototype.setItem;
    Storage.prototype.getItem = function(key) { if (key === storageKey) throw new Error('Storage disabled'); return get.call(this, key); };
    Storage.prototype.setItem = function(key, value) { if (key === storageKey) throw new Error('Quota exhausted'); return set.call(this, key, value); };
  }, storageKey);
  const original = await readHistory(page);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Draft while storage is unavailable');
  await askThroughApi(page, fixture, 'storage-unavailable');
  await expect(progress(page)).toContainText('2 unread updates');
  await expectAnchor(page, original);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Draft while storage is unavailable');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#team-tab-manager').click();
  await expect(progress(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach('reading-continuity-mobile', { body: await page.screenshot(), contentType: 'image/png' });
  const after = await snapshot(page, fixture, []);
  expect(after.state).toEqual(before.state);
  expect(decisions(after)).toEqual(decisions(before));
  expect(after.worker_jobs).toEqual(before.worker_jobs);
});
