import { test, expect, importProject } from './fixtures.js';
import { decisions, expectGate, prepareFirstTask, requestRevision, sendTask, snapshot, workState } from './business-loop-helpers.js';

// Real HTTP + ledger, explicitly fake Workers. Runtime evidence is collected separately.
test('keyboard approval completes the revision and reapproval loop without duplicate decisions', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement keyboard-accessible project filtering.');
  const runId = await prepareFirstTask(page, fixture, evidence);
  const approve = async (gate) => {
    const before = await expectGate(page, fixture.key, gate);
    await page.goto(`/projects/${fixture.key}/review/${gate}`);
    const reason = page.getByRole('textbox', { name: 'Why are you continuing despite these evidence gaps?' });
    await reason.fill('Fake-only keyboard acceptance; simulated evidence is not a real review PASS.');
    await reason.focus();
    await expect(reason).toHaveAccessibleDescription(/Ctrl\+Enter/);
    const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/approve');
    await reason.press('Control+Enter');
    await page.keyboard.press('Control+Enter');
    expect((await response).ok()).toBe(true);
    await expect.poll(async () => decisions(await workState(page, fixture.key)).length).toBe(decisions(before).length + 1);
    const after = await snapshot(page, fixture, evidence);
    expect(after.state.run_id).toBe(runId);
    expect(decisions(after).at(-1)).toMatchObject({ gate, decision: 'approved' });
    await page.reload();
    expect(decisions(await workState(page, fixture.key))).toEqual(decisions(after));
  };
  for (const gate of ['takeover', 'prd', 'rfc']) await approve(gate);
  await expectGate(page, fixture.key, 'security');
  await requestRevision(page, fixture, evidence);
  for (const gate of ['rfc', 'security', 'release']) await approve(gate);
  const completed = await snapshot(page, fixture, evidence);
  expect(completed.state).toMatchObject({ run_id: runId, current_node: 'complete', next_node: '', blockers: [] });
  expect(decisions(completed).map(d => [d.gate, d.decision])).toEqual([
    ['takeover', 'approved'], ['prd', 'approved'], ['rfc', 'approved'], ['security', 'changes_requested'],
    ['rfc', 'approved'], ['security', 'approved'], ['release', 'approved'],
  ]);
  await testInfo.attach('keyboard-approval-fake-ledger', { body: JSON.stringify(evidence, null, 2), contentType: 'application/json' });
});

test('review dialogs trap keyboard focus, preserve unsaved edits, and restore the trigger', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement keyboard review navigation.');
  await prepareFirstTask(page, fixture, []);
  const inspect = page.getByRole('button', { name: 'Inspect details →', exact: true });
  await inspect.focus();
  await inspect.press('Enter');
  const fullReview = page.getByRole('dialog').getByRole('button', { name: 'Review document', exact: true });
  if (await fullReview.count()) await fullReview.press('Enter');
  const review = page.getByRole('dialog', { name: 'Review', exact: true });
  const back = review.getByRole('button', { name: 'Back to task', exact: true });
  await expect(back).toBeFocused();
  await expect(review.getByRole('button', { name: 'Edit Source', exact: true })).toBeEnabled();
  await back.focus();
  await page.keyboard.press('Shift+Tab');
  expect(await review.evaluate(el => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Tab');
  await expect(back).toBeFocused();
  await review.getByRole('button', { name: 'Edit Source', exact: true }).press('Enter');
  const editor = review.getByRole('textbox', { name: 'Artifact content', exact: true });
  const original = await editor.inputValue();
  await editor.fill(`${original}\nKeyboard unsaved draft.`);
  await page.keyboard.press('Escape');
  const warning = page.getByRole('dialog', { name: 'Unsaved review edits' });
  await expect(warning).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(warning).toHaveCount(0);
  await expect(editor).toHaveValue(`${original}\nKeyboard unsaved draft.`);
  await editor.fill(original);
  await page.keyboard.press('Escape');
  await expect(review).toHaveCount(0);
  await expect(inspect).toBeFocused();
});

test('reduce motion setting survives reload and follows live system preference changes', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto(`/projects/${fixture.key}/settings`);
  const motion = page.getByRole('combobox', { name: 'Reduce motion', exact: true });
  await motion.selectOption('reduce');
  await expect(page.locator('html')).toHaveClass(/reduce-motion/);
  await page.reload();
  await expect(motion).toHaveValue('reduce');
  expect(await page.locator('.studio-navigation__link').first().evaluate(el => getComputedStyle(el).transitionDuration)).toBe('0s');
  await motion.selectOption('system');
  await expect(page.locator('html')).not.toHaveClass(/reduce-motion/);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('html')).toHaveClass(/reduce-motion/);
  await page.getByRole('link', { name: 'Workbench', exact: true }).click();
  await expect(page.locator('html')).toHaveClass(/reduce-motion/);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect(page.locator('html')).not.toHaveClass(/reduce-motion/);
});

test('rejection requires a reason, records it once, and remains settled after reload', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement rejection keyboard acceptance.');
  await prepareFirstTask(page, fixture, []);
  const before = await expectGate(page, fixture.key, 'takeover');
  await page.goto(`/projects/${fixture.key}/review/takeover`);
  const reject = page.getByRole('button', { name: 'Reject', exact: true });
  await expect(reject).toBeDisabled();
  const reason = page.getByRole('textbox', { name: 'Why are you continuing despite these evidence gaps?' });
  await reason.fill('Fake-only rejection acceptance: this document does not meet the task contract.');
  const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/approve');
  await reject.press('Enter');
  expect((await response).ok()).toBe(true);
  await expect.poll(async () => decisions(await workState(page, fixture.key)).length).toBe(decisions(before).length + 1);
  const after = await workState(page, fixture.key);
  expect(decisions(after).at(-1)).toMatchObject({ gate: 'takeover', decision: 'rejected', note: 'Fake-only rejection acceptance: this document does not meet the task contract.' });
  await page.reload();
  expect(decisions(await workState(page, fixture.key))).toEqual(decisions(after));
  await expect(page.getByRole('button', { name: 'Confirm manual continuation', exact: true })).toHaveCount(0);
});

test('interrupted approval retains the reason and explicit retry writes exactly one decision', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement interrupted approval acceptance.');
  await prepareFirstTask(page, fixture, []);
  const before = await expectGate(page, fixture.key, 'takeover');
  await page.goto(`/projects/${fixture.key}/review/takeover`);
  const reason = page.getByRole('textbox', { name: 'Why are you continuing despite these evidence gaps?' });
  const note = 'Fake-only interrupted transport acceptance; explicitly retry this same evidence.';
  await reason.fill(note);
  // Abort one outgoing request; no replacement API response or ledger seeding.
  await page.route('**/api/approve', route => route.abort('failed'), { times: 1 });
  await reason.press('Control+Enter');
  await expect(page.getByRole('alert').first()).toBeVisible();
  await expect(reason).toHaveValue(note);
  await expect(reason).toBeEnabled();
  expect(decisions(await workState(page, fixture.key))).toEqual(decisions(before));
  const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/approve');
  await reason.press('Control+Enter');
  expect((await response).ok()).toBe(true);
  await expectGate(page, fixture.key, 'prd');
  const after = decisions(await workState(page, fixture.key));
  expect(after).toHaveLength(decisions(before).length + 1);
  expect(after.at(-1)).toMatchObject({ gate: 'takeover', decision: 'approved', note });
  expect(fixture.errors).toEqual(['Failed to load resource: net::ERR_FAILED']);
  fixture.errors.splice(0); // The one deliberately aborted request is expected.
});
