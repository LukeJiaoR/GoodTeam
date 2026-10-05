import { test, expect, importProject } from './fixtures.js';
import { clickMutation, decisions, expectGate, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

// Real HTTP and durable ledger with explicitly fake Workers, not a #411 PASS.
test('Review and Work share evidence semantics and Review records a bound manual reason', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Verify an informed planning decision.');
    await prepareFirstTask(page, fixture, evidence);
    const before = await expectGate(page, fixture.key, 'takeover');
    const review = before.approval_reviews.find(item => item.approval === 'takeover');
    expect(review.recommendation.verdict).toBe(before.team_workspace.attention_request.approval_brief.recommendation.verdict);
    expect(review.recommendation.verdict).not.toBe('approve');
    await page.goto(`/projects/${fixture.key}/review/takeover`);
    const button = page.getByRole('button', { name: 'Confirm manual continuation', exact: true });
    const reason = page.getByRole('textbox', { name: 'Why are you continuing despite these evidence gaps?' });
    await expect(button).toBeDisabled();
    await reason.fill('  ');
    await reason.press('Control+Enter');
    await reason.press('Meta+Enter');
    expect(decisions(await workState(page, fixture.key))).toEqual(decisions(before));
    const note = 'Fake-only acceptance: proceed with this planning document, not a verified reviewer pass.';
    await reason.fill(note);
    const request = page.waitForRequest(item => item.method() === 'POST' && new URL(item.url()).pathname === '/api/approve');
    await clickMutation(page, button, '/api/approve');
    expect((await request).postDataJSON()).toMatchObject({
      project_key: fixture.key, approval: 'takeover', decision: 'approved', note,
      expected_run_id: before.state.run_id, expected_target_type: 'review', expected_target_id: 'takeover',
      expected_artifact_key: review.artifact, expected_artifact_version: review.artifact_version,
    });
    await expectGate(page, fixture.key, 'prd');
    const after = await snapshot(page, fixture, evidence);
    expect(decisions(after)).toHaveLength(decisions(before).length + 1);
    expect(decisions(after).at(-1)).toMatchObject({ gate: 'takeover', decision: 'approved', note });
    await page.reload();
    await expect(button).toHaveCount(0);
    expect(decisions(await workState(page, fixture.key))).toEqual(decisions(after));
  } finally {
    await testInfo.attach('informed-review-fake-evidence', {
      body: JSON.stringify({ source: 'fake-only', project: fixture.key, snapshots: evidence }, null, 2),
      contentType: 'application/json',
    });
  }
});
